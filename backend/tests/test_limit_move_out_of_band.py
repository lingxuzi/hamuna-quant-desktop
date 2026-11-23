"""涨跌幅落在板限之外的行不是涨跌停。

涨跌停日的涨跌幅必然在板限内 —— 这是涨跌停的必要条件。参考价算错时
(除权因子缺失 / 行情脏数据), 收盘价会离限价十万八千里: 实测 26 只退市股里
000004.SZ 2026-06-23 昨收 2.76 收 0.31 (机械除权 -88.8%), 单边判定
(close <= limit_down_price) 把它记成跌停, 连板数跟着错。

修法: 判定价 (limit_up_price / limit_down_price) 在这类行上置 null ——
判定价是内置与自定义涨跌停信号的唯一来源, 置 null 才能让它们连同连板数
一起不成立, 而不是留一个看着像跌停的假价。

容差 1.5 分: 交易所限价与本地四舍五入可能差 1 分, 判定价本身再留 0.005。
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators.pipeline import _compute_limit_signals_today, compute_limit_signals


@pytest.fixture(autouse=True)
def _seeded_signals(seeded_data_dir) -> None:
    """内置涨跌停信号 (signal_limit_*) 运行时住在 data/ 里, 干净环境 (CI) 没有 —— 种进临时目录。"""


def _days(symbol: str, closes: list[float], start: date = date(2024, 1, 2)) -> pl.DataFrame:
    """按收盘价序列造连续交易日: 收=高=低, 首日作基准。"""
    n = len(closes)
    dates = [start + timedelta(days=i) for i in range(n)]
    return pl.DataFrame({
        "symbol": [symbol] * n,
        "date": dates,
        "raw_close": closes,
        "close": closes,
        "raw_high": closes,
        "raw_low": closes,
        "open": closes,
        "high": closes,
        "low": closes,
        "change_pct": [0.0] * n,
        "vol_ratio_5d": [1.0] * n,
        "volume": [1000.0] * n,
        "amount": [10000.0] * n,
    })


def _inst(symbol: str = "600001.SH", name: str = "普通测试") -> pl.DataFrame:
    return pl.DataFrame({"symbol": [symbol], "name": [name]})


def test_ex_rights_row_without_factor_is_not_limit_down():
    """机械除权 -88.8% (无复权因子) → 不是跌停, 连板数不增。"""
    df = _days("600001.SH", [2.76, 0.31])
    out = compute_limit_signals(df, _inst()).sort("date")

    assert out["limit_down_price"].to_list()[-1] is None
    assert out["limit_up_price"].to_list()[-1] is None
    assert out["signal_limit_down"].to_list()[-1] is None
    assert out["signal_limit_up"].to_list()[-1] is None
    assert out["consecutive_limit_downs"].to_list()[-1] == 0


def test_ex_rights_row_upward_is_not_limit_up():
    """机械除权 +790% (无复权因子) → 不是涨停。"""
    df = _days("600001.SH", [0.31, 2.76])
    out = compute_limit_signals(df, _inst()).sort("date")

    assert out["limit_up_price"].to_list()[-1] is None
    assert out["signal_limit_up"].to_list()[-1] is None


def test_normal_limit_up_keeps_price_and_flag():
    """板限内的正常涨停 → 判定价与标记都不变。"""
    df = _days("600001.SH", [10.00, 11.00])
    out = compute_limit_signals(df, _inst()).sort("date")

    assert out["limit_up_price"].to_list()[-1] == pytest.approx(10.995)
    assert out["signal_limit_up"].to_list()[-1] is True
    assert out["consecutive_limit_ups"].to_list()[-1] == 1


def test_limit_up_at_rounding_edge_is_kept():
    """涨停价按分取整后涨跌幅可略超板限 (0.95 → 1.05 = +10.5%) → 仍算涨停。"""
    df = _days("600001.SH", [0.95, 1.05])
    out = compute_limit_signals(df, _inst()).sort("date")

    assert out["limit_up_price"].to_list()[-1] == pytest.approx(1.045)
    assert out["signal_limit_up"].to_list()[-1] is True


def test_st_band_limit_up_is_kept():
    """风险警示带 5%: 收在 +5% → 仍算涨停 (板限按生效带判定, 不是硬编码 10%)。"""
    df = _days("600001.SH", [10.00, 10.50])
    out = compute_limit_signals(df, _inst(name="*ST测试")).sort("date")

    assert out["limit_up_price"].to_list()[-1] == pytest.approx(10.495)
    assert out["signal_limit_up"].to_list()[-1] is True


def test_realtime_path_applies_same_rule():
    """实时路径同口径: 板限外的收盘 → 判定价 null, 不记涨停。"""
    df = _days("600001.SH", [2.76, 0.31])
    today = df.filter(pl.col("date") == date(2024, 1, 3)).with_columns(
        pl.lit(2.76).alias("_prev_close_raw"),
    )
    row = _compute_limit_signals_today(today, _inst()).row(0, named=True)

    assert row["limit_up_price"] is None
    assert row["limit_down_price"] is None
    assert row["consecutive_limit_downs"] == 0


def test_missing_reference_price_does_not_block_judgement():
    """复牌无历史 (原始前收缺失) → 涨跌幅判不了, 但不该据此否定涨跌停。

    判不了 ≠ 不是涨跌停: 权威限价 11.00 + 收盘 11.00 仍是涨停,
    缺参考价时把判定价置 null 会把这类行从涨停名单里抹掉。
    """
    today = date(2024, 1, 3)
    df = pl.DataFrame({
        "symbol": ["600001.SH"],
        "date": [today],
        "open": [11.0], "high": [11.0], "low": [11.0],
        "close": [11.0], "raw_close": [11.0], "raw_high": [11.0], "raw_low": [11.0],
        "volume": [1000.0],
        "_prev_close_raw": [None],
    })
    instruments = pl.DataFrame({
        "symbol": ["600001.SH"], "name": ["复牌股"],
        "limit_up": [11.0], "limit_down": [9.0], "as_of": [today],
    })

    row = _compute_limit_signals_today(df, instruments).row(0, named=True)

    assert row["limit_up_price"] == pytest.approx(10.995)
    assert row["consecutive_limit_ups"] == 1


def _ex_rights_day(
    *,
    prev_raw: float = 16.50,
    factor: float = 1.409052092228864,
    day_open: float = 12.12,
    day_high: float = 12.90,
    day_low: float = 12.07,
    day_close: float = 12.90,
) -> pl.DataFrame:
    """两天: 除权前一日 + 除权日 (复权比例变化 = 因子变化)。

    除权日的参考价由因子反推 (close.shift(1)/adj_today), 算出限价 12.88;
    市场当天封在 12.90 → 交易所规则被违反 (价格不能越过限价)。
    """
    adj_prev = prev_raw / factor
    return pl.DataFrame({
        "symbol": ["600001.SH", "600001.SH"],
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "raw_close": [prev_raw, day_close],
        "close": [adj_prev, day_close],
        "raw_high": [prev_raw, day_high],
        "raw_low": [prev_raw, day_low],
        "open": [prev_raw, day_open],
        "high": [prev_raw, day_high],
        "low": [prev_raw, day_low],
        "change_pct": [0.0, 0.0],
        "vol_ratio_5d": [1.0, 1.0],
        "volume": [1000.0, 2000.0],
        "amount": [10000.0, 20000.0],
    })


def test_ex_rights_limit_up_is_taken_from_market():
    """除权日: 算出的限价被市场证伪 (收盘封在上限) → 用市场给的真限价。

    002560.SZ 2026-05-13 实例: 因子推出参考价 11.71 → 限价 12.88,
    而当天一字封在 12.90 (次日 11.61 = 12.90 × 0.9 正好跌停)。
    """
    df = _ex_rights_day()
    out = compute_limit_signals(df, _inst()).sort("date")

    assert out["limit_up_price"].to_list()[-1] == pytest.approx(12.895)
    assert out["signal_limit_up"].to_list()[-1] is True
    assert out["consecutive_limit_ups"].to_list()[-1] == 1


def test_ex_rights_day_not_closed_at_high_keeps_computed_limit():
    """没有封在上限 (收盘 < 最高价) → 市场没给出真限价 → 判定价仍是算出的那个。"""
    df = _ex_rights_day(day_close=12.80)
    out = compute_limit_signals(df, _inst()).sort("date")

    assert out["limit_up_price"].to_list()[-1] == pytest.approx(12.875)
    assert out["signal_limit_up"].to_list()[-1] is not True


def test_ex_rights_limit_gap_over_one_percent_is_not_trusted():
    """偏离 >1% → 那是脏数据不是舍入 → 不采信市场限价, 判定价置 null。"""
    df = _ex_rights_day(day_high=13.50, day_close=13.50)
    out = compute_limit_signals(df, _inst()).sort("date")

    assert out["limit_up_price"].to_list()[-1] is None
    assert out["signal_limit_up"].to_list()[-1] is None


def test_non_ex_rights_day_above_limit_is_not_flagged():
    """非除权日 (无因子变化) 价格越过算出的限价 → 不算市场证伪, 不造涨停。

    上市初期无涨跌幅窗口的交易日价格本就可以越限, 用市场证伪去兜会造出假涨停。
    """
    df = pl.DataFrame({
        "symbol": ["600001.SH", "600001.SH"],
        "date": [date(2024, 1, 2), date(2024, 1, 3)],
        "raw_close": [10.0, 11.20],
        "close": [10.0, 11.20],
        "raw_high": [10.0, 11.20],
        "raw_low": [10.0, 11.20],
        "open": [10.0, 11.20],
        "high": [10.0, 11.20],
        "low": [10.0, 11.20],
        "change_pct": [0.0, 0.0],
        "vol_ratio_5d": [1.0, 1.0],
        "volume": [1000.0, 2000.0],
        "amount": [10000.0, 20000.0],
    })
    out = compute_limit_signals(df, _inst()).sort("date")

    assert out["limit_up_price"].to_list()[-1] is None
    assert out["signal_limit_up"].to_list()[-1] is None
