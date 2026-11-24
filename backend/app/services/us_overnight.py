"""美股夜盘环境 — 走 easytdx 扩展市场通道 (ex/MacExClient, 端口 7727)。

实测 2026-11-24: 一次全量 0.15s (connect 0.08s + 4 次批量报价 0.05s)。
比腾讯+新浪(4.03s)快一个数量级, 且能拿到腾讯没有的费城半导体与纳指金龙中国。

VIX 不在这里 —— `INTL_INDEX` 与 `MACRO_INDICATOR` 都没有 VIX, 仍走 FRED。
腾讯仍负责恒生指数与欧洲股票 ETF (easytdx 的港股主板的指数不在 INTL_INDEX)。

失败一律返回 None, 由 premarket 回退腾讯/新浪, 绝不抛给调用方。
"""
from __future__ import annotations

import logging
import threading

from app.market_time import cn_now

logger = logging.getLogger(__name__)

# (easytdx 代码, 展示名, 地区)
US_INDICES: tuple[tuple[str, str, str], ...] = (
    ("A_SPX", "标普 500", "美国"),
    ("A_IXIC", "纳斯达克综合", "美国"),
    ("A_DJI", "道琼斯", "美国"),
    ("A_NDX", "纳斯达克 100", "美国"),
    ("A_SOX", "费城半导体", "美国"),
    ("A_HXC", "纳指金龙中国", "美国（中概）"),
)

# 龙头股 — 夜盘情绪的实际载体, 指数涨幅被几只大票决定
US_LEADERS: tuple[tuple[str, str], ...] = (
    ("NVDA", "英伟达"),
    ("AAPL", "苹果"),
    ("MSFT", "微软"),
    ("TSLA", "特斯拉"),
    ("META", "Meta"),
    ("GOOGL", "谷歌 A"),
    ("AMZN", "亚马逊"),
)

# 期指连续合约: 比现货更早反映次日方向
FUTURES: tuple[tuple[str, str], ...] = (
    ("CNY0", "富时中国 A50 期指"),
    ("NK0Y", "日经 225 期指"),
)

# 美债收益率 (单位 %, 不是价格)
TREASURIES: tuple[tuple[str, str], ...] = (
    ("8_ATY", "美国 10 年期国债"),
    ("8_ATSY", "美国 2 年期国债"),
)

_lock = threading.Lock()
_client = None


def _get_client():
    """懒建并复用连接。easytdx 不可用或节点全挂时抛异常, 由 fetch_overnight 兜。"""
    global _client
    if _client is not None:
        return _client
    from easy_tdx.ex.mac_client import MacExClient

    client = MacExClient.from_best_host()
    client.connect()
    _client = client
    return client


def close() -> None:
    """停止时释放连接 (shutdown 钩子调用)。"""
    global _client
    with _lock:
        if _client is not None:
            try:
                _client.close()
            except Exception as e:  # noqa: BLE001 — 关闭失败不影响重建
                logger.debug("easytdx ex 连接关闭异常: %s", e)
            _client = None


def _pct(close: float | None, prev: float | None) -> float | None:
    # close<=0 是「当期尚未成交」的哨兵值, 不能当价格用
    if close is None or close <= 0 or not prev:
        return None
    return round((close / prev - 1) * 100, 2)


def _rows(client, market, specs) -> list[dict]:
    """批量报价 → 归一化行。价格缺失的标的跳过。"""
    from easy_tdx.mac.enums import ExMarket

    mkt = getattr(ExMarket, market)
    quotes = client.goods_quotes([(mkt, code) for code, *_ in specs])
    if not len(quotes):
        return []
    by_code = {r["code"]: r for r in quotes.to_dict("records")}
    out: list[dict] = []
    for spec in specs:
        code, name = spec[0], spec[1]
        r = by_code.get(code)
        # close<=0 表示该指数当期无成交报价 (easytdx 仍回 pre_close, 拿它算涨跌会得到 -100%)
        if not r or not r.get("close") or r["close"] <= 0:
            continue
        out.append({
            "code": code, "name": name,
            "close": float(r["close"]), "previous_close": r.get("pre_close"),
            "open": r.get("open"), "high": r.get("high"), "low": r.get("low"),
            "pct": _pct(r.get("close"), r.get("pre_close")),
        })
    return out


def fetch_overnight() -> dict | None:
    """一次拿全美股夜盘。任一环节失败返回 None (调用方回退腾讯/新浪)。

    返回 {
      indices: [{code,name,region,close,previous_close,pct,open,high,low}],
      leaders: [{code,name,close,previous_close,pct}],
      futures: [...], treasuries: [...] (close 单位是 %), us10y: float|None,
      as_of, source
    }
    """
    with _lock:
        try:
            client = _get_client()
            indices = _rows(client, "INTL_INDEX", US_INDICES)
            for row in indices:
                for code, _name, region in US_INDICES:
                    if code == row["code"]:
                        row["region"] = region
                        break
            leaders = _rows(client, "US_STOCK", US_LEADERS)
            futures = _rows(client, "INTL_INDEX", FUTURES)
            treasuries = _rows(client, "MACRO_INDICATOR", TREASURIES)
        except Exception as e:  # noqa: BLE001
            logger.debug("easytdx 美股夜盘拉取失败: %s", e)
            # 连接可能已坏, 下次重建
            global _client
            _client = None
            return None

    us10y = next((t["close"] for t in treasuries if t["code"] == "8_ATY"), None)
    return {
        "indices": indices,
        "leaders": leaders,
        "futures": futures,
        "treasuries": treasuries,
        "us10y": us10y,
        "as_of": cn_now().strftime("%Y-%m-%d %H:%M"),
        "source": "easytdx",
    }
