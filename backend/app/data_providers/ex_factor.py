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
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger(__name__)

# 上游 (TDX 协议) 字段全是 float32: 日K收盘价、派息、送转、配股、配股价。
# float32 相对噪声 ~6e-8, 而参考价恰好落在 x.xx5 分界时 (前收与派息都落在
# 3 位小数网格上, 差值压在分界上的概率约两成), 噪声会决定舍入方向 ——
# 参考价差 1 分 → 单事件因子差 0.09% → 除权日限价差 1 分, 收盘贴着限价时
# 会被误判成涨跌停 (实测 556/21665 行 / 470 只标的)。
#
# 还原策略: 只把「float32 噪声量级」的输入还原到十进制网格; 供应商自己按股本
# 比例推出的值 (送转 0.3999975919723511, 相对偏差 ~6e-6) 不还原 ——
# 那是供应商的精度问题, 强行落到网格会把误差放大到 0.2 分。
_CLEAN_REL_TOL = 1e-7

# 各输入的自然网格: 价格 2 位 (分), 事件参数 3 位 (每 10 股口径 ÷10 后的精度)
_PRICE_DIGITS = 2
_EVENT_DIGITS = 3


def _snap_grid(value: float, digits: int) -> float:
    """把 float32 噪声还原到十进制网格; 偏差超过噪声量级则原样保留。"""
    if not value:
        return 0.0
    grid = round(value, digits)
    if abs(value - grid) <= _CLEAN_REL_TOL * max(abs(value), 1.0):
        return grid
    return value


def ref_price(
    prev_close: float, dividend: float, bonus: float, allot: float, allot_price: float
) -> float | None:
    """交易所除权参考价: (P - D + AR·AP) / (1 + S + AR), 四舍五入(half-up)保留 2 位。

    half-up 是交易所口径; 银行家舍入会让约半数事件在第 2 位小数上偏离(对拍实证)。
    输入先还原到十进制网格 (见 _snap_grid) 再用 Decimal 做 half-up ——
    float 路径在 x.xx5 分界上会被 float32 噪声带偏 1 分。
    """
    p = _snap_grid(prev_close, _PRICE_DIGITS)
    d = _snap_grid(dividend, _EVENT_DIGITS)
    s = _snap_grid(bonus, _EVENT_DIGITS)
    ar = _snap_grid(allot, _EVENT_DIGITS)
    ap = _snap_grid(allot_price, _PRICE_DIGITS)
    denom = Decimal(1) + Decimal(str(s)) + Decimal(str(ar))
    if denom <= 0:
        return None
    x = (Decimal(str(p)) - Decimal(str(d)) + Decimal(str(ar)) * Decimal(str(ap))) / denom
    return float(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def recompute_ex_factor(
    prev_close: float | None,
    dividend: float | None,
    bonus: float | None,
    allot: float | None,
    allot_price: float | None,
) -> float | None:
    """按明细列重算单事件因子 (存量归一化用, 与 factor_rows 同口径)。

    明细列是公式的唯一输入, 存量行的 ex_factor 由旧公式算出 → 与公式漂移。
    分子用原始前收 (与 factor_rows 一致), 参考价在 ref_price 内部还原。
    返回 None = 无法重算 (缺明细 / 参考价非法) —— 调用方保留原值。
    """
    if prev_close is None:
        return None
    ref = ref_price(
        prev_close, dividend or 0.0, bonus or 0.0, allot or 0.0, allot_price or 0.0
    )
    if ref is None or ref <= 0:
        return None
    return prev_close / ref


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