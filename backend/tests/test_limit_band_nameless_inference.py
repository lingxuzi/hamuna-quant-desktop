"""无名称标的的风险警示带宽自证。

维表 (instruments) 是同步当日的快照: 退市/停牌标的从维表消失 → 名称与 ST
标记一起丢失, 涨跌停带退化成板块带, 真 ST 的 5% 封板被当成普通波动
(实测 26 只退市股、187 行真封板判错)。

修法: 名称不可得时用价格自证 —— 封在风险警示带的限价上 (收盘==最高==风险警示
涨停价, 或 收盘==最低==跌停价) 说明当日带宽就是这个带。

防误判的三条约束:
1. 只对「维表无名称且名称历史也没有」的标的生效 —— 全市场套用会误判
   (实测 5321 行非 ST 标的收盘恰好 +5% 且收在最高)。
2. 同一标的至少 2 次封板才认定 —— 单次巧合不算。
3. 风险警示带与板块带同值时不猜 (主板 2026-07-06 之后风险警示股也是 10% 带)。
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators.pipeline import compute_all, compute_limit_signals


@pytest.fixture(autouse=True)
def _seeded_signals(seeded_data_dir) -> None:
    """内置涨跌停信号 (signal_limit_*) 运行时住在 data/ 里, 干净环境 (CI) 没有 —— 种进临时目录。"""


def _days(symbol: str, closes: list[float], start: date = date(2024, 1, 2)) -> pl.DataFrame:
    """按收盘价序列造连续交易日: 收=高=低 (封板形态), 首日作基准。"""
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


def _limits(df: pl.DataFrame, instruments: pl.DataFrame, **kwargs) -> pl.DataFrame:
    return compute_limit_signals(df, instruments, **kwargs).sort("date")


def _other_inst() -> pl.DataFrame:
    """维表里有别的标的, 但没有这只 (退市标的从维表消失的情形)。"""
    return pl.DataFrame({"symbol": ["600000.SH"], "name": ["平安银行"]})


def test_nameless_symbol_sealing_five_pct_twice_is_limit_up():
    """无名称标的连续封在 5% 涨停价 → 认定风险警示带, 判涨停。"""
    df = _days("600001.SH", [10.00, 10.50, 11.03])
    out = _limits(df, _other_inst())

    assert out["signal_limit_up"].to_list() == [None, True, True]
    # 5% 涨停价: 昨收 10.00 → 10.50, 判定价含 0.005 容差
    assert out["limit_up_price"].to_list()[1] == pytest.approx(10.495)
    assert out["consecutive_limit_ups"].to_list()[-1] == 2


def test_single_seal_is_not_enough():
    """只封过一次 → 巧合, 不认定风险警示带 (保持板块带)。"""
    df = _days("600001.SH", [10.00, 10.50])
    out = _limits(df, _other_inst())

    assert out["signal_limit_up"].to_list()[-1] is False
    assert out["limit_up_price"].to_list()[-1] == pytest.approx(10.995)


def test_named_non_st_symbol_is_not_affected():
    """有名称的非 ST 标的收盘恰好 +5% 且收在最高 → 不是涨停 (全市场误判防线)。"""
    df = _days("600001.SH", [10.00, 10.50, 11.03])
    inst = pl.DataFrame({"symbol": ["600001.SH"], "name": ["普通测试"]})
    out = _limits(df, inst)

    assert out["signal_limit_up"].to_list() == [None, False, False]
    # 板块带 10%: 昨收 10.00 → 涨停价 11.00
    assert out["limit_up_price"].to_list()[1] == pytest.approx(10.995)


def test_named_st_symbol_keeps_five_pct_band():
    """有名称的 ST 标的仍走名称判定 (自证规则不介入)。"""
    df = _days("600001.SH", [10.00, 10.50])
    inst = pl.DataFrame({"symbol": ["600001.SH"], "name": ["*ST测试"]})
    out = _limits(df, inst)

    assert out["signal_limit_up"].to_list()[-1] is True


def test_null_name_in_dimension_table_uses_inference():
    """维表里有这只但名称为 null → 同样走价格自证。"""
    df = _days("600001.SH", [10.00, 10.50, 11.03])
    inst = pl.DataFrame({"symbol": ["600001.SH"], "name": [None]})
    out = _limits(df, inst)

    assert out["signal_limit_up"].to_list()[-1] is True
    assert out["limit_up_price"].to_list()[1] == pytest.approx(10.495)


def test_name_history_blocks_inference():
    """名称历史里有名称 → 不是「无名称」, 不走自证。"""
    df = _days("600001.SH", [10.00, 10.50, 11.03])
    hist = pl.DataFrame({
        "symbol": ["600001.SH", "600001.SH"],
        "name": ["普通测试", "普通测试"],
        "as_of": [date(2024, 1, 1), date(2024, 6, 1)],
    })
    out = _limits(df, _other_inst(), name_history=hist)

    assert out["signal_limit_up"].to_list()[-1] is False


def test_growth_board_symbol_keeps_twenty_pct_band():
    """创业板 20% 带与风险警示带同值 → 无从区分, 不猜。"""
    df = _days("300001.SZ", [10.00, 12.00, 14.40])
    out = _limits(df, _other_inst())

    assert out["signal_limit_up"].to_list()[-1] is True
    # 20% 带: 10.00 → 12.00 (涨停价 11.995)
    assert out["limit_up_price"].to_list()[1] == pytest.approx(11.995)


def test_limit_down_seal_is_detected():
    """封在 5% 跌停价 → 判跌停。"""
    df = _days("600001.SH", [10.00, 9.50, 9.03])
    out = _limits(df, _other_inst())

    assert out["signal_limit_down"].to_list() == [None, True, True]
    assert out["consecutive_limit_downs"].to_list()[-1] == 2


# ── 维表缺该标的时也要算涨跌停 ──────────────────────────────

def test_empty_instrument_table_still_computes_limits():
    """维表过滤后为空 (批次里全是维表缺名标的) → 不能整段跳过。

    跳过会让这些标的的涨跌停列全 null、连板数永远 0, 而它们的行情还在。
    """
    df = _days("600001.SH", [10.00, 10.50, 11.03])
    out = _limits(df, pl.DataFrame({"symbol": [], "name": []}))

    assert out["signal_limit_up"].to_list() == [None, True, True]
    assert out["consecutive_limit_ups"].to_list()[-1] == 2


def test_instrument_table_without_columns_still_computes_limits():
    """维表是无列的空表 (pl.DataFrame()) → 归一成有 symbol 列的空表, 不崩。"""
    df = _days("600001.SH", [10.00, 10.50, 11.03])
    out = _limits(df, pl.DataFrame())

    assert out["signal_limit_up"].to_list() == [None, True, True]


def test_compute_all_with_empty_instruments_keeps_limit_columns():
    """compute_all: 维表为空 ≠ 不算涨跌停 (整段跳过的回归)。"""
    df = _days("600001.SH", [10.00, 10.50, 11.03])
    out = compute_all(df, instruments=pl.DataFrame())

    assert "limit_up_price" in out.columns
    assert "consecutive_limit_ups" in out.columns
    assert out["consecutive_limit_ups"].to_list()[-1] == 2
