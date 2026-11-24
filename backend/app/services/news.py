"""要闻服务 — 多源 RSS 合并、去重、标的映射、命中自选的系统通知。

新闻源取自 news-source/source.json 的 macro 分组 (108 源里只有这 11 个与盘前研判
相关; 其余 77 个是 AI/半导体/汽车/生物/科学, 每天抓 88 个 HTTP 请求没有收益)。
放开其它分组只需往 _FEEDS 加行。

只有东财两个源带结构化标的 (data-code="002242|0|2"): 前缀 0→深, 1→沪;
90/100/101/102/103/105/106/116/122/124/133/251 是板块/期指/外盘/港股/商品,
本仓库无对应标的, 一律丢弃。

去重照抄参考项目: 标题规范化后长度 ≥16 时用 SequenceMatcher 0.88 判近似。

通知走 notify_adapter (与策略告警同一出口), 但**不写 alert_store** ——
那是 7 天/5000 条滚动的告警流, 新闻进去会把它淹掉。
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from app.config import settings
from app.market_time import cn_now, cn_today
from app.symbols import symbol_of_code

logger = logging.getLogger(__name__)

# 连接阶段单独收紧: 境外源 (CNBC/SEC/FT) 在部分网络下 connect 会挂满 20s,
# 拖慢整轮。连不上就快速放弃, 读阶段保留 10s 给慢但可达的源。
_TIMEOUT = httpx.Timeout(connect=4.0, read=10.0, write=10.0, pool=4.0)
# 东财两个源各带近百条且只有它们带标的代码, 截太短会丢掉全部 A 股关联条目
_MAX_PER_FEED = 100
# 同标的一小时内只弹一次系统通知
_NOTIFY_COOLDOWN_S = 3600.0

# (源名, URL) — source.json 的 macro 分组, 已剔除实测不可达项
_FEEDS: tuple[tuple[str, str], ...] = (
    ("东方财富", "http://rss.eastmoney.com/rss_stock.xml"),
    ("东方财富·资讯", "http://rss.eastmoney.com/rss_partener.xml"),
    ("华尔街见闻", "https://dedicated.wallstreetcn.com/rss.xml"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("SEC", "https://www.sec.gov/news/pressreleases.rss"),
    ("美联储", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Financial Times", "https://www.ft.com/rss/home"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
    ("经济观察网", "http://www.eeo.com.cn/rss.xml"),
)

# source.json 的 redline_keywords: 赌博/加密/色情类，命中即丢
_REDLINE = (    "赌博", "博彩", "赌场", "彩票", "下注", "押注", "预测市场", "polymarket",
    "kalshi", "prediction market", "加密货币", "虚拟货币", "比特币", "以太坊",
    "稳定币", "crypto", "bitcoin", "ethereum", "stablecoin", "gambling",
    "betting", "casino", "lottery", "sportsbook", "色情", "porn",
)

# 中文 A 股源优先。看板是 A 股看板, 纯时间排序会让 Seeking Alpha 的英文财报
# 占满前几条, 把东财/华尔街见闻挤到看不见。境外条目仍然保留, 只是排在后面。
_TIER: dict[str, int] = {"东方财富": 0, "东方财富·资讯": 0, "华尔街见闻": 0, "经济观察网": 0}

_DATA_CODE_RE = re.compile(r'data-code="(\d{6})\|(\d+)\|')
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_HREF_RE = re.compile(r'href="(https?://[^"]+)"')


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _text(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return _WS_RE.sub(" ", (el.text or "")).strip()


def _clean_html(raw: str, limit: int = 240) -> str:
    txt = _WS_RE.sub(" ", _TAG_RE.sub(" ", raw or "")).strip()
    return txt[:limit]


def _pub_iso(raw: str) -> str | None:
    """RFC822 → 北京时间 ISO。解析失败返回 None。"""
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    return dt.astimezone(cn_now().tzinfo).strftime("%Y-%m-%d %H:%M")


def extract_symbols(html: str) -> list[str]:
    """从东财正文里抽 A 股标的。只认 0(深)/1(沪) 前缀, 其余市场一律丢弃。"""
    seen: dict[str, None] = {}
    for code, prefix in _DATA_CODE_RE.findall(html or ""):
        if prefix not in ("0", "1"):
            continue
        sym = symbol_of_code(code)
        if sym:
            seen.setdefault(sym, None)
    return list(seen)


def parse_feed(xml_text: str, source: str) -> list[dict]:
    """RSS/Atom → 归一化条目。单条解析失败跳过, 不拖垮整源。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = root.findall(".//item")
    if not items:  # Atom 兜底
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    out: list[dict] = []
    for it in items[:_MAX_PER_FEED]:
        try:
            title = _text(it.find("title"))
            if not title:
                continue
            link_el = it.find("link")
            url = _text(link_el)
            if not url:
                url = (link_el.get("href") if link_el is not None else "") or ""
            raw_body = _text(it.find("description")) or _text(it.find("summary"))
            if not raw_body:
                raw_body = _text(it.find("content"))
            pub = _pub_iso(_text(it.find("pubDate")) or _text(it.find("published")) or "")
            # 东财正文是 HTML 且带 data-code, 必须用原始串抽标的
            href = _HREF_RE.search(raw_body)
            if href and not url:
                url = href.group(1)
            out.append({
                "id": hashlib.md5((url or title).encode("utf-8")).hexdigest()[:16],
                "title": _WS_RE.sub(" ", title),
                "snippet": _clean_html(raw_body),
                "source": source,
                "url": url or None,
                "published_date": pub,
                "symbols": extract_symbols(raw_body),
            })
        except Exception as e:  # noqa: BLE001
            logger.debug("RSS 条目解析失败 (%s): %s", source, e)
            continue
    return out


def _is_redline(item: dict) -> bool:
    text = f"{item.get('title') or ''} {item.get('snippet') or ''}".lower()
    return any(w in text for w in _REDLINE)


def order(items: list[dict]) -> list[dict]:
    """中文 A 股源在前, 同层内按发布时间倒序。"""
    rows = sorted(items, key=lambda i: i.get("published_date") or "", reverse=True)
    return sorted(rows, key=lambda i: _TIER.get(i.get("source") or "", 1))


# ---------------------------------------------------------------------------
# 去重
# ---------------------------------------------------------------------------

def normalize_title(title: str) -> str:
    return re.sub(r"[\W_]+", "", str(title or "").lower(), flags=re.UNICODE)


def dedupe(items: list[dict]) -> list[dict]:
    """同标题归一化后完全相同, 或长度 ≥16 且相似度 ≥0.88 视为重复。"""
    unique: list[dict] = []
    normalized: list[str] = []
    seen_ids: set[str] = set()
    for item in items:
        iid = item.get("id")
        if iid in seen_ids:
            continue
        candidate = normalize_title(item.get("title"))
        if not candidate:
            continue
        dup = candidate in normalized
        if not dup and len(candidate) >= 16:
            dup = any(
                len(existing) >= 16 and SequenceMatcher(None, candidate, existing).ratio() >= 0.88
                for existing in normalized
            )
        if dup:
            continue
        seen_ids.add(iid)
        unique.append(item)
        normalized.append(candidate)
    return unique


# ---------------------------------------------------------------------------
# 抓取 + 落盘
# ---------------------------------------------------------------------------

def _fetch_one(client: httpx.Client, name: str, url: str) -> list[dict]:
    r = client.get(url, headers={"User-Agent": settings.ai_user_agent})
    r.raise_for_status()
    return parse_feed(r.content.decode("utf-8", "replace"), name)


def fetch_all() -> list[dict]:
    """并发抓全部源。单源失败静默跳过 —— 境外源国内可能不可达。"""
    gathered: list[dict] = []
    with (
        httpx.Client(timeout=_TIMEOUT, follow_redirects=True) as client,
        ThreadPoolExecutor(max_workers=8) as ex,
    ):
        futs = {name: ex.submit(_fetch_one, client, name, url) for name, url in _FEEDS}
        for name, fut in futs.items():
            try:
                gathered.extend(fut.result())
            except Exception as e:  # noqa: BLE001
                logger.debug("新闻源 %s 抓取失败: %s", name, e)
    kept = [i for i in gathered if not _is_redline(i)]
    return dedupe(order(kept))


def _cache_path(data_dir: Path, day: str | None = None) -> Path:
    return Path(data_dir) / "news" / f"date={day or cn_today().isoformat()}.json"


def _read_cache(path: Path) -> list[dict]:
    import json

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    return items if isinstance(items, list) else []


def _store_cache(path: Path, items: list[dict]) -> None:
    """整份原子重写 (临时文件 + replace), 单写者线程独占。"""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(
        json.dumps({"date": path.stem.removeprefix("date="), "items": items},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(path)


def refresh(data_dir: Path) -> list[dict]:
    """抓一轮, 与当日缓存按 id 合并后落盘, 返回合并结果。"""
    today = _cache_path(data_dir)
    existing = _read_cache(today)
    merged: dict[str, dict] = {}
    for it in [*existing, *fetch_all()]:
        iid = it.get("id")
        if iid:
            merged.setdefault(iid, it)
    items = order(dedupe(list(merged.values())))
    try:
        _store_cache(today, items)
    except OSError as e:
        logger.warning("新闻缓存写入失败: %s", e)
    return items


def list_recent(data_dir: Path, limit: int = 20, symbols: list[str] | None = None) -> list[dict]:
    """读今日 + 昨日缓存合并、倒序、可按标的过滤。AI / Agent / 页面共用入口。"""
    today = cn_today()
    yesterday = today - timedelta(days=1)
    rows = [*_read_cache(_cache_path(data_dir, today.isoformat())),
            *_read_cache(_cache_path(data_dir, yesterday.isoformat()))]
    if symbols:
        # 按标的查是「这只股票最近发生了什么」, 纯时间序即可, 不按源分层
        wanted = {s.upper() for s in symbols}
        rows = [r for r in rows if wanted & {s.upper() for s in (r.get("symbols") or [])}]
        rows.sort(key=lambda r: r.get("published_date") or "", reverse=True)
    else:
        rows = order(rows)
    return rows[:max(1, limit)]


def cached_with_names(data_dir: Path, limit: int, symbols: list[str] | None,
                       repo) -> list[dict]:
    """给条目补上标的名称, 前端直接展示, 不必二次请求。"""
    rows = list_recent(data_dir, limit, symbols)
    codes = sorted({s for r in rows for s in (r.get("symbols") or [])})
    if not codes or repo is None:
        return rows
    try:
        names = repo.get_name_map(codes)
    except Exception as e:  # noqa: BLE001
        logger.debug("标的名称解析失败: %s", e)
        return rows
    for r in rows:
        r["symbol_names"] = {s: names[s] for s in (r.get("symbols") or []) if s in names}
    return rows


# ---------------------------------------------------------------------------
# 后台轮询 + 命中自选通知
# ---------------------------------------------------------------------------

def _watch_targets(data_dir: Path) -> set[str]:
    from app.services import watchlist
    from app.strategy import lots

    out = {s.upper() for s in watchlist.symbols()}
    try:
        out.update((lot.get("symbol") or "").upper() for lot in lots.load_all(Path(data_dir)))
    except Exception as e:  # noqa: BLE001
        logger.debug("持仓读取失败: %s", e)
    out.discard("")
    return out


@dataclass
class NewsPoller:
    """daemon 线程定时刷新要闻; 命中自选/持仓时发系统通知 (按标的冷却)。"""

    data_dir: Path
    repo: object | None = None
    interval_s: float = 60.0
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _cooldown: dict[str, float] = field(default_factory=dict)
    _last_error: str | None = None

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="news-poller", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                items = refresh(Path(self.data_dir))
                self._notify_hits(items)
                self._last_error = None
            except Exception as e:  # noqa: BLE001
                self._last_error = str(e)
                logger.warning("要闻轮询异常 (忽略): %s", e)
            self._stop.wait(self.interval_s)

    def _notify_hits(self, items: list[dict]) -> None:
        from app.services import preferences

        if not preferences.get_system_notify_enabled():
            return
        targets = _watch_targets(self.data_dir)
        if not targets:
            return
        import time

        now = time.monotonic()
        fired: list[str] = []
        names: dict[str, str] = {}
        for item in items:
            hits = [s for s in (item.get("symbols") or []) if s.upper() in targets]
            fresh = [s for s in hits
                     if now - self._cooldown.get(s.upper(), 0.0) > _NOTIFY_COOLDOWN_S]
            if not fresh:
                continue
            for s in fresh:
                self._cooldown[s.upper()] = now
            if not names:
                # 通知正文给人看, 显示名称; 查不到才退回代码
                try:
                    names = self.repo.get_name_map(list(targets)) if self.repo else {}
                except Exception as e:  # noqa: BLE001
                    logger.debug("通知标的名称解析失败: %s", e)
            fired.append(f"{item.get('title')}\n命中 {', '.join(names.get(s, s) for s in fresh)}")
        if not fired:
            return
        body = "\n\n".join(fired[:3])
        from app.services.notify_adapter import notify

        try:
            notify("要闻提醒 · 自选命中", body)
        except Exception as e:  # noqa: BLE001
            logger.debug("要闻通知发送异常 (不影响轮询): %s", e)

    def status(self) -> dict:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "last_error": self._last_error,
            "cooldown_symbols": len(self._cooldown),
        }
