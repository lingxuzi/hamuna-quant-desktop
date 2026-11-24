"""盘前简报 + 外部风险温度 + 事件传导链。

参考 ychenfen/a-share-daily 的 scripts/global_context.py, 把「隔夜外围」这一层
搬进本仓库: 外盘指数 + 跨资产 + 宏观 + 规则指纹打分。

定位分工:
- 本模块的「外部风险温度」是 -100..+100, 量化**外部环境**(外围股市/汇率/宏观压力);
- `emotion_score.py` 的「情绪评分」是 0-100, 量化**内部情绪**(指数/盈利/资金/投机)。
两者独立, 不互相引用, 也不重复计算同一指标 —— compute_risk 收 overview 入参,
A股动量/市场宽度/短线情绪直接读 market_overview_builder 的既有结果。

取数优先级 (实测 2026-11-24):
1. easytdx 扩展行情 0.15s —— 美股 3 大指数 / 纳指100 / 费半 / 中概金龙 / 期指 / 美债 / 龙头股
2. 腾讯 https://qt.gtimg.cn/q=usINX,usIXIC,usDJI,hkHSI,usVGK  GBK  —— 兜底 + 恒生/欧洲
   [3]=现价 [4]=昨收 [30]=报价时间 [32]=涨跌%
3. 新浪 https://hq.sinajs.cn/list=...  GBK, **必须带 Referer 否则 403** —— 跨资产兜底
   期货型(15段): [0]=最新 [7]=昨收 [6]=时间 [12]=日期 [13]=名称
   汇率型(>=11段): [1]=最新 [5]=昨收 [0]=时间 [10]=涨跌%
4. FRED https://fred.stlouisfed.org/graph/fredgraph.csv?id=X&cosd=YYYY-MM-DD
   对 HTTP/2 偶发 INTERNAL_ERROR, 必须走 httpx 默认协议; curl --http1.1 会挂住。

VIX 只有 FRED 有 (easytdx 的 INTL_INDEX / MACRO_INDICATOR 都没有), 日经 225 指数
同理走 FRED —— easytdx 的 NK0Y 是**期指**不是指数, 不混用。
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta

import httpx

from app.config import settings
from app.market_time import cn_now, cn_today
from app.services import us_overnight

logger = logging.getLogger(__name__)

SINA_REFERER = "https://finance.sina.com.cn"
_TIMEOUT = 12.0
# 快照内存缓存: 盘前数字是隔夜收盘, 3 分钟内不会变, 避免每个页面轮询都打外网
_TTL_S = 180.0

# ---------------------------------------------------------------------------
# 标的定义
# ---------------------------------------------------------------------------

# 腾讯: 只留 easytdx 给不了的两个, 其余作为 easytdx 挂掉时的兜底。
# 每个源都写全 5 个, easytdx 成功时优先用自己的行, 失败的才回落到腾讯。
TENCENT_MARKETS: tuple[tuple[str, str, str, str], ...] = (
    # (腾讯代码, 展示名, 地区, kind)
    ("usINX", "标普 500", "美国", "index"),
    ("usIXIC", "纳斯达克", "美国", "index"),
    ("usDJI", "道琼斯", "美国", "index"),
    ("hkHSI", "恒生指数", "中国香港", "index"),
    ("usVGK", "欧洲股票 ETF", "欧洲（美股代理）", "ETF proxy"),
)

# easytdx 代码 → 本模块统一 key。沿用腾讯的 key 命名, 下游 _WATCH_KEYS /
# compute_risk / 测试都不必改; 新增的 NDX/SOX/HXC 只做展示, 不参与打分。
EASYTDX_INDEX_KEYS: dict[str, str] = {
    "A_SPX": "usINX", "A_IXIC": "usIXIC", "A_DJI": "usDJI",
    "A_NDX": "usNDX", "A_SOX": "usSOX", "A_HXC": "usHXC",
}

# 参与「外围股市」均值的市场。NDX/SOX 与 IXIC 高度重叠, 中概金龙更不是外部
# 市场 —— 都只展示不打分, 否则 6 个美股指数会把日经/恒生/欧洲的权重稀释掉。
_SCORED_MARKET_KEYS = frozenset({"usINX", "usIXIC", "usDJI", "hkHSI", "usVGK", "N225"})

SINA_ASSETS: tuple[tuple[str, str, str, str], ...] = (
    # (新浪代码, 展示名, role, format) — format 决定字段偏移
    ("hf_CHA50CFD", "富时中国 A50 期货", "scored", "future"),
    ("fx_susdcnh", "美元兑离岸人民币", "scored", "fx"),
    ("DINIW", "美元指数", "scored", "fx"),
    ("hf_GC", "COMEX 黄金", "context", "future"),
    ("hf_CL", "WTI 原油", "context", "future"),
)

FRED_SERIES = ("VIXCLS", "DGS10", "NIKKEI225")
# FRED 单次要 3s (VIX 尤其慢), 但 VIX/10Y/日经都是日频 —— 15 分钟内重抓没有意义。
_FRED_TTL_S = 900.0
_fred_cache: dict[str, tuple[float, float | None]] = {}


# FRED 对完整浏览器 UA 会走慢通道 (实测稳定 10s+ 超时), 短 UA 反而 1-2s 返回。
# 只有 FRED 用这个, 其余源保持浏览器 UA (绕 WAF)。
_FRED_UA = "hquant-premarket/1.0"


def _headers(referer: str | None = None, ua: str | None = None) -> dict[str, str]:
    h = {"User-Agent": ua or settings.ai_user_agent}
    if referer:
        h["Referer"] = referer
    return h


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _fnum(raw) -> float | None:
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def parse_tencent(text: str) -> list[dict]:
    """腾讯行情文本 → 市场行。字段缺失或价格为 0 的行丢弃。"""
    out: list[dict] = []
    meta = {k: (name, region, kind) for k, name, region, kind in TENCENT_MARKETS}
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip().removeprefix("v_")
        if key not in meta:
            continue
        payload = line.split('="', 1)
        if len(payload) < 2:
            continue
        f = payload[1].rstrip('";\n').split("~")
        close = _fnum(f[3]) if len(f) > 3 else None
        prev = _fnum(f[4]) if len(f) > 4 else None
        if not close:
            continue
        name, region, kind = meta[key]
        pct = _fnum(f[32]) if len(f) > 32 else None
        if pct is None and prev:
            pct = (close / prev - 1) * 100
        out.append({
            "key": key, "name": name, "region": region, "kind": kind,
            "close": close, "previous_close": prev,
            "pct": round(pct, 2) if pct is not None else None,
            "quote_time": (f[30] if len(f) > 30 else None) or None,
            "state": "fresh",
        })
    return out


def parse_sina(text: str) -> list[dict]:
    """新浪行情文本 → 跨资产行。按 format 取不同字段偏移。"""
    out: list[dict] = []
    meta = {k: (name, role, fmt) for k, name, role, fmt in SINA_ASSETS}
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip()
        key = key.removeprefix("var hq_str_h_").removeprefix("var hq_str_")
        if key not in meta:
            continue
        payload = line.split('="', 1)
        if len(payload) < 2:
            continue
        f = payload[1].rstrip('";\n').split(",")
        if len(f) < 8:
            continue
        name, role, fmt = meta[key]
        if fmt == "future":
            close, prev, tstamp, dstr = _fnum(f[0]), _fnum(f[7]), f[6], f[12] if len(f) > 12 else ""
        else:
            close, prev, tstamp = _fnum(f[1]), _fnum(f[5]), f[0]
            dstr = f[-1] if f[-1][:4].isdigit() else ""
            if len(f) > 10:
                pct_raw = _fnum(f[10])
                if pct_raw is not None:
                    out.append({
                        "key": key, "name": name, "role": role,
                        "close": close, "previous_close": prev,
                        "pct": round(pct_raw, 2),
                        "quote_time": f"{dstr} {tstamp}".strip() or None,
                        "state": "fresh",
                    })
                    continue
        if not close:
            continue
        pct = (close / prev - 1) * 100 if prev else None
        out.append({
            "key": key, "name": name, "role": role,
            "close": close, "previous_close": prev,
            "pct": round(pct, 2) if pct is not None else None,
            "quote_time": f"{dstr} {tstamp}".strip() or None,
            "state": "fresh",
        })
    return out


def parse_fred_csv(text: str) -> float | None:
    """FRED CSV → 最后一个有效数值。缺失值在 CSV 里写作 '.'。"""
    last = None
    for line in text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        v = _fnum(parts[1])
        if v is not None:
            last = v
    return last


# ---------------------------------------------------------------------------
# 抓取
# ---------------------------------------------------------------------------

def _get(client: httpx.Client, url: str, referer: str | None = None, encoding: str = "utf-8",
         ua: str | None = None) -> str:
    r = client.get(url, headers=_headers(referer, ua))
    r.raise_for_status()
    return r.content.decode(encoding, "replace")


def _fetch_fred(client: httpx.Client, series: str) -> float | None:
    import time

    t = time.monotonic()
    hit = _fred_cache.get(series)
    if hit and (t - hit[0]) < _FRED_TTL_S:
        return hit[1]
    start = (cn_today() - timedelta(days=40)).isoformat()
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"
    val = parse_fred_csv(_get(client, url, ua=_FRED_UA))
    if val is not None:  # 失败不缓存, 下一轮重试
        _fred_cache[series] = (t, val)
    return val


def _overnight_markets(over: dict) -> list[dict]:
    """easytdx 结果 → markets 行。key 沿用腾讯命名, 状态标 easytdx。"""
    out: list[dict] = []
    for row in over.get("indices") or []:
        key = EASYTDX_INDEX_KEYS.get(row["code"])
        if not key:
            continue
        out.append({
            "key": key, "name": row["name"], "region": row.get("region") or "美国",
            "kind": "index", "close": row["close"],
            "previous_close": row.get("previous_close"), "pct": row.get("pct"),
            "quote_time": over.get("as_of"), "state": "fresh",
        })
    # 期指也进 markets, 用独立 key 单独展示, 不打分
    for row in over.get("futures") or []:
        out.append({
            "key": f"future:{row['code']}", "name": row["name"], "region": "期指",
            "kind": "future", "close": row["close"],
            "previous_close": row.get("previous_close"), "pct": row.get("pct"),
            "quote_time": over.get("as_of"), "state": "fresh",
        })
    return out


def fetch_all() -> dict:
    """easytdx 打头 (0.15s), 腾讯/新浪/FRED 兜底补齐。任一源失败 → 该组为空记 missing, 不抛。"""
    cross: list[dict] = []
    macro: dict[str, float | None] = {"vix": None, "us10y": None}
    health = {
        "easytdx": "missing", "tencent": "missing", "sina": "missing",
        "fred": "missing", "nikkei": "missing",
    }

    # easytdx 是同步阻塞调用 (内部 RLock 串行), 但只有 0.15s, 直接在主线程取,
    # 同时把腾讯/新浪/FRED 丢进线程池 —— 两者重叠, 总耗时 ≈ FRED 的 3s。
    with (
        httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client,
        ThreadPoolExecutor(max_workers=4) as ex,
    ):
        futs = {
            "tencent": ex.submit(
                lambda: parse_tencent(
                    _get(client, f"https://qt.gtimg.cn/q={','.join(k for k, *_ in TENCENT_MARKETS)}",
                         encoding="gbk"))),
            "sina": ex.submit(
                lambda: parse_sina(
                    _get(client, f"https://hq.sinajs.cn/list={','.join(k for k, *_ in SINA_ASSETS)}",
                         SINA_REFERER, "gbk"))),
            "vix": ex.submit(_fetch_fred, client, "VIXCLS"),
            "us10y": ex.submit(_fetch_fred, client, "DGS10"),
            "nikkei": ex.submit(_fetch_fred, client, "NIKKEI225"),
        }
        over = us_overnight.fetch_overnight()
        results: dict[str, object] = {}
        for name, fut in futs.items():
            try:
                results[name] = fut.result()
            except Exception as e:  # noqa: BLE001
                logger.debug("盘前数据源 %s 拉取失败: %s", name, e)
                results[name] = None

    # 美股指数: easytdx 行优先, 缺的 key 用腾讯补
    markets = _overnight_markets(over) if over else []
    if over:
        health["easytdx"] = "fresh" if len(over.get("indices") or []) == len(us_overnight.US_INDICES) else "partial"
    have = {m["key"] for m in markets}
    tencent_rows = results.get("tencent") or []
    missing = [m for m in tencent_rows if m["key"] not in have]
    if tencent_rows:
        health["tencent"] = "fresh" if len(tencent_rows) == len(TENCENT_MARKETS) else "partial"
    markets = [*markets, *missing]

    cross = results.get("sina") or []
    if cross:
        health["sina"] = "fresh" if len(cross) == len(SINA_ASSETS) else "partial"

    vix, us10y, n225 = results.get("vix"), results.get("us10y"), results.get("nikkei")
    # 美债 10Y: easytdx 有就用它 (比 FRED 的日频快得多)
    ez10y = (over or {}).get("us10y")
    macro["vix"], macro["us10y"], macro["nikkei225"] = vix, ez10y if ez10y is not None else us10y, n225
    if vix is not None and macro["us10y"] is not None:
        health["fred"] = "fresh"
    elif vix is not None or macro["us10y"] is not None:
        health["fred"] = "partial"
    if n225 is not None:
        health["nikkei"] = "fresh"

    if n225 is not None:
        markets = [*markets, {
            "key": "N225", "name": "日经 225", "region": "日本", "kind": "index",
            "close": n225, "previous_close": None, "pct": None,
            "quote_time": None, "state": "stale"}]

    return {
        "markets": markets,
        "cross_assets": cross,
        "macro": macro,
        "health": health,
        "as_of": cn_now().strftime("%Y-%m-%d %H:%M"),
        "us": over,
    }


# ---------------------------------------------------------------------------
# 快照缓存: 失败保留上次成功值并标 stale
# ---------------------------------------------------------------------------

@dataclass
class _Cache:
    at: float = 0.0
    payload: dict | None = None


_cache = _Cache()


def snapshot(now: float | None = None) -> dict:
    import time

    t = time.monotonic() if now is None else now
    if _cache.payload is not None and (t - _cache.at) < _TTL_S:
        return _cache.payload
    try:
        fresh = fetch_all()
    except Exception as e:  # noqa: BLE001
        logger.warning("盘前快照刷新失败, 沿用上次: %s", e)
        if _cache.payload is not None:
            stale = dict(_cache.payload)
            stale["stale"] = True
            return stale
        return {"markets": [], "cross_assets": [], "macro": {}, "health": {},
                "as_of": None, "stale": True}
    fresh.setdefault("stale", False)
    _cache.at, _cache.payload = t, fresh
    return fresh


def reset_cache() -> None:
    """测试用: 清空内存缓存。"""
    _cache.at = 0.0
    _cache.payload = None


# ---------------------------------------------------------------------------
# 外部风险温度
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _stance(score: int) -> tuple[str, str]:
    if score >= 25:
        return "积极但不追高", "#c9211e"
    if score >= 5:
        return "谨慎偏多", "#e05a47"
    if score > -5:
        return "中性等待", "#8c959f"
    if score > -25:
        return "谨慎防守", "#3b9b68"
    return "防守优先", "#08783e"


# 五档结论 + 研究观察。文案照抄参考项目 —— 是研究提示不是仓位建议,
# 换措辞就得重新审一遍合规口径, 不值得。
_STANCE_TEXT: dict[str, tuple[str, tuple[str, ...]]] = {
    "积极但不追高": (
        "A股内部强度与外围风险偏好形成正向共振，但仍需用回撤和量能确认持续性。",
        ("关注强势行业能否在下一交易日继续获得成交额与市场宽度支持。",
         "研究执行宜分批、等待回撤确认，避免把单日普涨直接外推成趋势。"),
    ),
    "谨慎偏多": (
        "风险偏好略占优，但信号并未形成全面共振。",
        ("保留进攻观察清单，同时以市场宽度和外围指数是否续强作为确认条件。",),
    ),
    "中性等待": (
        "多空线索接近平衡，等待价格、宽度或宏观压力出现更清晰方向。",
        ("减少方向性预判，优先记录关键阈值被突破后的跟随信号。",),
    ),
    "谨慎防守": (
        "外部或内部风险信号偏多，短期更适合提高验证门槛。",
        ("降低追涨优先级，重点观察 VIX、长端利率和跌停扩散是否继续恶化。",),
    ),
    "防守优先": (
        "内部市场与外围压力形成负向共振，先控制暴露并等待风险指标回落。",
        ("研究上优先压力测试与回撤控制，避免仅凭单条利好逆势下注。",),
    ),
}


# 新闻语气的关键词表, 低权重只作提示
_POS_WORDS = ("上涨", "利好", "突破", "回暖", "增长", "超预期", "反弹", "创新高",
              "rally", "surge", "beat", "record high", "upgrade")
_NEG_WORDS = ("下跌", "利空", "暴跌", "衰退", "低于预期", "风险", "抛售", "违约",
              "selloff", "plunge", "miss", "downgrade", "recession")


def _news_tone(news: list[dict]) -> tuple[int, str]:
    text = " ".join((n.get("title") or "") for n in news[:40]).lower()
    pos = sum(1 for w in _POS_WORDS if w in text)
    neg = sum(1 for w in _NEG_WORDS if w in text)
    return pos, neg


def compute_risk(overview: dict, snap: dict, news: list[dict]) -> dict:
    """-100..+100 外部风险温度。纯函数, 不查库不联网。

    overview: build_market_overview 的返回 (取 indices/breadth/limit 三项);
    snap:     snapshot() 的返回 (外盘/跨资产/宏观);
    news:     news.list_recent() 的返回 (标题语气)。
    系数照抄参考项目, 每一项都保留可审计的 score + note。
    """
    signals: list[dict] = []

    def add(key, label, value, score, note):
        signals.append({"key": key, "label": label, "value": value,
                        "score": round(score, 1), "note": note})

    # 1. A股动量 — 上证涨跌
    sh_pct = None
    for idx in overview.get("indices") or []:
        if (idx.get("symbol") or "").startswith("000001"):
            sh_pct = idx.get("change_pct")
            break
    if sh_pct is None and overview.get("indices"):
        sh_pct = overview["indices"][0].get("change_pct")
    a_momentum = _clamp((sh_pct or 0) * 8, -20, 20)
    add("a_momentum", "A股动量", f"上证 {sh_pct:+.2f}%" if sh_pct is not None else "缺失",
        a_momentum, "反映本地市场价格强弱")

    # 2. 市场宽度
    br = overview.get("breadth") or {}
    up, down = br.get("up") or 0, br.get("down") or 0
    breadth_sc = _clamp(((up - down) / (up + down) * 20) if (up + down) else 0, -20, 20)
    add("breadth", "市场宽度", f"涨 {up} / 跌 {down}", breadth_sc,
        "上涨家数占优时提高风险偏好")

    # 3. 短线情绪 — 涨停/跌停/炸板率
    lim = overview.get("limit") or {}
    lu, ld = lim.get("limit_up") or 0, lim.get("limit_down") or 0
    seal = lim.get("seal_rate")
    broken_rate = (100 - seal) if seal is not None else 0.0
    sentiment_sc = _clamp((lu - ld) / 6 - broken_rate / 8, -15, 15)
    add("sentiment", "短线情绪",
        f"涨停 {lu} / 跌停 {ld} / 炸板率 {broken_rate:.1f}%", sentiment_sc,
        "涨停扩散加分, 炸板率过高扣分")

    # 4. 外围股市 — 只算跨市场的 6 个 (美股三大/恒生/欧洲/日经)。
    # easytdx 多给的 NDX/SOX/HXC 与 IXIC 高度重叠, 计进去等于给美股加权两次。
    eq = [m["pct"] for m in snap.get("markets") or []
          if m.get("pct") is not None and m.get("key") in _SCORED_MARKET_KEYS]
    global_sc = _clamp((sum(eq) / len(eq)) * 7, -20, 20) if eq else 0.0
    add("global_equity", "外围股市",
        f"{len(eq)} 个市场均值 {sum(eq)/len(eq):+.2f}%" if eq else "缺失",
        global_sc, "衡量隔夜风险偏好共振")

    # 5. A50 先行
    cross = {c["key"]: c for c in snap.get("cross_assets") or []}
    a50 = cross.get("hf_CHA50CFD") or {}
    a50_pct = a50.get("pct")
    a50_sc = _clamp((a50_pct or 0) * 6, -8, 8)
    add("a50", "A50 先行",
        f"富时 A50 {a50_pct:+.2f}%" if a50_pct is not None else "缺失",
        a50_sc, "离岸期货低权重先行确认, 避免重复计 A 股现货动量")

    # 6. 美元与人民币
    cnh = cross.get("fx_susdcnh") or {}
    dxy = cross.get("DINIW") or {}
    cnh_pct, dxy_pct = cnh.get("pct"), dxy.get("pct")
    fx_sc = _clamp(-(cnh_pct or 0) * 12 - (dxy_pct or 0) * 5, -8, 8)
    add("fx", "美元与人民币",
        f"USD/CNH {cnh.get('close')} ({(cnh_pct or 0):+.2f}%) / "
        f"DXY {dxy.get('close')} ({(dxy_pct or 0):+.2f}%)" if cnh_pct is not None else "缺失",
        fx_sc, "美元走弱与人民币走强通常缓解外部流动性压力")

    # 7. 波动压力
    vix = (snap.get("macro") or {}).get("vix")
    if vix is None:
        vix_sc = 0.0
    elif vix < 15:
        vix_sc = 5.0
    elif vix >= 30:
        vix_sc = -15.0
    elif vix >= 25:
        vix_sc = -10.0
    elif vix >= 20:
        vix_sc = -5.0
    else:
        vix_sc = 0.0
    add("vix", "波动压力", f"VIX {vix:.2f}" if vix is not None else "缺失",
        vix_sc, "VIX 越高, 全球避险需求通常越强")

    # 8. 利率压力
    y10 = (snap.get("macro") or {}).get("us10y")
    if y10 is None:
        y10_sc = 0.0
    elif y10 >= 5:
        y10_sc = -8.0
    elif y10 >= 4.5:
        y10_sc = -5.0
    elif y10 < 3.5:
        y10_sc = 3.0
    else:
        y10_sc = 0.0
    add("us10y", "利率压力", f"美债 10Y {y10:.2f}%" if y10 is not None else "缺失",
        y10_sc, "长端利率偏高时压制高估值资产")

    # 9. 新闻语气
    pos, neg = _news_tone(news)
    news_sc = _clamp((pos - neg) * 2, -8, 8)
    add("news_tone", "新闻语气", f"正向词 {pos} / 风险词 {neg}", news_sc,
        "标题关键词只做低权重提示, 不代替事实核验")

    total = _clamp(sum(s["score"] for s in signals), -100, 100)
    score = round(total)
    stance, color = _stance(score)
    summary, advice = _STANCE_TEXT[stance]

    h = snap.get("health") or {}
    coverage = sum([
        bool(overview.get("indices")),
        bool(br.get("total")),
        a50_pct is not None and (cnh_pct is not None or dxy_pct is not None),
        vix is not None,
        y10 is not None,
        bool(news),
    ])
    degraded = snap.get("stale") or any(v == "missing" for v in h.values())
    confidence = "高" if coverage == 6 and not degraded else "中" if coverage >= 4 else "低"

    return {
        "score": score,
        "stance": stance,
        "color": color,
        "confidence": confidence,
        "summary": summary,
        "advice": list(advice),
        "coverage": coverage,
        "signals": signals,
        "degraded": bool(degraded),
        "methodology": (
            "透明规则评分: A股动量、市场宽度、短线情绪、外围股市、A50先行、"
            "美元与人民币、VIX、美债10Y 和新闻标题低权重语气。"
            "黄金与原油只作背景, 事件传导链不直接计分。"
        ),
        "as_of": snap.get("as_of"),
    }


# ---------------------------------------------------------------------------
# 事件传导链
# ---------------------------------------------------------------------------

EVENT_RULES: tuple[dict, ...] = (
    {
        "key": "central_bank", "label": "央行与利率",
        "keywords": ("美联储", "央行", "加息", "降息", "利率决议", "货币政策",
                     "fed", "rate hike", "rate cut"),
        "macro_path": "政策利率预期 → 美债收益率与美元 → 全球流动性",
        "a_share_lens": "高估值成长、券商、地产与人民币敏感资产",
        "watch": ("US10Y", "DXY", "USDCNH"),
        "invalidation": (
            "若美债利率、美元与人民币没有同向确认, 标题冲击可能只是短期噪音。"
        ),
    },
    {
        "key": "inflation", "label": "通胀与就业",
        "keywords": ("通胀", "cpi", "ppi", "非农", "就业", "工资", "物价",
                     "inflation", "payroll"),
        "macro_path": "通胀与就业 → 政策路径 → 折现率与风险偏好",
        "a_share_lens": "成长股估值、消费定价权与出口链",
        "watch": ("US10Y", "DXY", "NDX"),
        "invalidation": "若通胀或就业数据未带动利率与美元同向调整，仅作背景观察。",
    },
    {
        "key": "geopolitics", "label": "地缘与贸易",
        "keywords": ("冲突", "战争", "制裁", "关税", "地缘", "中东", "贸易战",
                     "geopolit", "sanction", "tariff"),
        "macro_path": "地缘冲突 → 避险与供给溢价 → 风险资产折价",
        "a_share_lens": "军工、能源、黄金与出口占比高的制造链",
        "watch": ("VIX", "GOLD", "WTI"),
        "invalidation": "若 VIX、黄金与原油没有同步走强，标题冲击可能只是情绪。",
    },
    {
        "key": "technology", "label": "科技与产业",
        "keywords": ("科技", "芯片", "半导体", "人工智能", "大模型", "光通信", "存储",
                     "英伟达", "technology", "chip", "semiconductor"),
        "macro_path": "产业景气 → 资本开支与订单 → 成长股定价",
        "a_share_lens": "半导体设备、算力链、光模块与国产替代",
        "watch": ("NDX", "A50", "US10Y"),
        "invalidation": "若纳指与 A50 未跟随，产业链共振尚未成立。",
    },
    {
        "key": "commodities", "label": "商品与成本",
        "keywords": ("原油", "油价", "黄金", "铜价", "大宗", "商品",
                     "opec", "wti", "gold", "commodity"),
        "macro_path": "商品价格 → 输入成本与通胀预期 → 企业利润分配",
        "a_share_lens": "能源、化工、有色、航空运输与中下游成本敏感行业",
        "watch": ("WTI", "GOLD", "US10Y"),
        "invalidation": "若现货代理价格未确认标题方向，暂不推断产业链利润迁移。",
    },
    {
        "key": "currency", "label": "美元与人民币",
        "keywords": ("美元", "人民币", "汇率", "外汇",
                     "dxy", "usdcnh", "renminbi", "yuan"),
        "macro_path": "美元与人民币 → 跨境流动性及进口成本 → 外资风险偏好",
        "a_share_lens": "外资敏感权重、航空造纸、出口链与离岸中国资产",
        "watch": ("DXY", "USDCNH", "A50"),
        "invalidation": "若 DXY 与 USD/CNH 背离，不能把单一汇率波动解释成统一资金方向。",
    },
    {
        "key": "domestic_policy", "label": "国内政策与需求",
        "keywords": ("财政", "政策", "刺激", "房地产", "地产", "消费", "专项债", "降准", "两会"),
        "macro_path": "国内政策预期 → 信用与需求 → 盈利修复和风险溢价",
        "a_share_lens": "金融地产、基建、消费与顺周期方向",
        "watch": ("A50", "HSI", "USDCNH"),
        "invalidation": "若 A50、港股与人民币均未改善，政策标题仍需等待价格和数据验证。",
    },
)

# watch 代号 → 从快照取值的路径
_WATCH_KEYS = {
    "US10Y": ("macro", "us10y", "%"),
    "VIX": ("macro", "vix", ""),
    "A50": ("cross", "hf_CHA50CFD", ""),
    "USDCNH": ("cross", "fx_susdcnh", ""),
    "DXY": ("cross", "DINIW", ""),
    "GOLD": ("cross", "hf_GC", ""),
    "WTI": ("cross", "hf_CL", ""),
    "NDX": ("market", "usNDX", ""),
    "HSI": ("market", "hkHSI", ""),
}


def _watch_value(snap: dict, code: str) -> dict:
    spec = _WATCH_KEYS.get(code)
    if spec is None:
        return {"code": code, "value": None, "state": "missing"}
    group, key, unit = spec
    if group == "macro":
        v = (snap.get("macro") or {}).get(key)
        return {"code": code, "value": v, "pct": None, "unit": unit,
                "state": "fresh" if v is not None else "missing"}
    pool = snap.get("markets") if group == "market" else snap.get("cross_assets")
    for row in pool or []:
        if row.get("key") == key:
            return {"code": code, "value": row.get("close"), "pct": row.get("pct"),
                    "unit": unit, "state": row.get("state") or "fresh"}
    return {"code": code, "value": None, "pct": None, "unit": unit, "state": "missing"}


def build_events(news: list[dict], snap: dict) -> list[dict]:
    """按关键词把去重后的标题分进 7 个传导通道。

    同一标题可命中多个通道 (参考项目也是这个口径)。分类数量不等于已确认事件,
    传导链不参与评分。

    结构上明确分成两层, 前端和 AI 都不许混着说:
    - `facts`   —— 可核验的观察: 命中的新闻标题 + 规则指标的实际读数
    - `inference` —— 本仓库的推断: 宏观传导路径 / A 股映射 / 失效条件
    只有 facts 能进结论句; inference 只能写成「如果…则…」式的待验证线索。
    """
    out: list[dict] = []
    for rule in EVENT_RULES:
        hits = []
        for item in news:
            text = f"{item.get('title') or ''} {item.get('snippet') or ''}".lower()
            if any(k.lower() in text for k in rule["keywords"]):
                hits.append({
                    "title": item.get("title"),
                    "source": item.get("source"),
                    "published_date": item.get("published_date"),
                    "url": item.get("url"),
                })
        out.append({
            "key": rule["key"],
            "label": rule["label"],
            "headline_count": len(hits),
            "facts": {
                "headlines": hits[:2],
                "watch": [_watch_value(snap, c) for c in rule["watch"]],
            },
            "inference": {
                "macro_path": rule["macro_path"],
                "a_share_lens": rule["a_share_lens"],
                "invalidation": rule["invalidation"],
            },
        })
    return out


def premarket_payload(overview: dict, news: list[dict]) -> dict:
    """API 出口: 快照 + 风险温度 + 事件链 一次给全。"""
    snap = snapshot()
    return {
        **snap,
        "risk": compute_risk(overview, snap, news),
        "events": build_events(news, snap),
    }
