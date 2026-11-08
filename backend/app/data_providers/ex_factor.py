"""除权因子共享算法 — 交易所公式 + 涨跌停自检。

tdxquant / easytdx 两个插件的事件来源字段名与单位不同(每10股 vs 每股),
但「事件 → 单事件比值」这一步必须完全一致: 它是涉钱计算, 复制两份必然漂移。
各插件只负责把自家字段归一成 `events`(`date/dividend/bonus/allot/allot_price`,
每股口径), 其余交给本模块。

单位契约 (CONTRIBUTING §3, 不可凭字段名推断):
  - dividend 每股派息(元), bonus 每股送转(股), allot 每股配股(股), allot_price 配股价(元/股)。
  - prev_close 为除权日之前的最后一个交易日原始收盘价。
  - ex_factor 为单事件比值 (非累积), 累积链由 indicators.pipeline._apply_adj_factor 构建。

ponytail: 只放公式 + 自检 + 事件结构, 不做通用因子引擎; 需要跨源一致性校验时再加。
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta

logger = logging.getLogger(__name__)


def ref_price(
    prev_close: float, dividend: float, bonus: float, allot: float, allot_price: float
) -> float | None:
    """交易所除权参考价: (P - D + AR·AP) / (1 + S + AR), 四舍五入(half-up)保留 2 位。

    half-up 是交易所口径; 银行家舍入会让约半数事件在第 2 位小数上偏离(对拍实证)。
    """
    denom = 1.0 + bonus + allot
    if denom <= 0:
        return None
    x = (prev_close - dividend + allot * allot_price) / denom
    return math.floor(x * 100 + 0.5) / 100


def price_limit(symbol: str) -> float:
    """按代码前缀给涨跌停幅度(自检容差用): 创业板/科创板 20%, 北交所 30%, 主板 10%。"""
    code = symbol.split(".")[0]
    if code.startswith(("300", "301", "302", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "92")):
        return 0.30
    return 0.10


def factor_rows(
    symbol: str,
    events: list[dict],
    closes: dict[date, float],
    start_d: date | None,
    end_d: date,
    *,
    future_days: int = 0,
) -> list[dict]:
    """事件 + 收盘价序列 → 内部契约行(symbol/trade_date/ex_factor + 明细列)。

    events: [{date: date, dividend: float, bonus: float, allot: float, allot_price: float}]
            已是每股口径, 窗口过滤由调用方或此处按 start_d/end_d 完成。
    closes: {交易日: 原始收盘价}, 用于取除权日前收盘与涨跌停自检。
    future_days: 未来已公告事件窗口(无前收盘自然跳过, 滚动增量轮补上)。

    涨跌停自检: 错误因子会令复权后除权日涨跌幅超出板限 → 剔除并告警。
    """
    if not events or not closes:
        return []
    days = sorted(closes)
    horizon = end_d + timedelta(days=future_days) if future_days else end_d
    out: list[dict] = []

    def _f(value) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    for ev in events:
        exd = ev.get("date")
        if exd is None or not isinstance(exd, date) or exd > horizon:
            continue
        if start_d is not None and exd < start_d:
            continue
        dividend = _f(ev.get("dividend"))
        bonus = _f(ev.get("bonus"))
        allot = _f(ev.get("allot"))
        allot_price = ev.get("allot_price")
        prev_days = [d for d in days if d < exd]
        if not prev_days:
            continue  # 日K窗口未覆盖(远古事件或未来已公告事件)
        p = closes[prev_days[-1]]
        ref = ref_price(p, dividend, bonus, allot, _f(allot_price))
        if ref is None or ref <= 0:
            logger.warning(
                "%s 除权因子: %s 参考价无法计算(P=%s D=%s S=%s AR=%s AP=%s), 跳过",
                symbol, exd, p, dividend, bonus, allot, allot_price,
            )
            continue
        factor = p / ref
        ex_days = [d for d in days if d >= exd]
        if ex_days:
            ret = closes[ex_days[0]] / (p / factor) - 1.0
            if abs(ret) > price_limit(symbol) + 0.02:
                logger.warning(
                    "%s 除权因子自检剔除 %s: 复权后除权日涨跌幅 %.1f%% 超出涨跌停",
                    symbol, exd, ret * 100,
                )
                continue
        out.append({
            "symbol": symbol, "trade_date": exd, "ex_factor": factor,
            "dividend": dividend, "bonus": bonus, "allot": allot,
            "allot_price": allot_price, "prev_close": p,
        })
    return out