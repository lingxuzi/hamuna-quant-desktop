"""盘中 enriched: 源只对部分标的给出比例字段时, 其余标的必须回算而不是留空。

_build_quote_extra 只丢弃「整列全 null」的字段, 部分 null 的列会原样传下来。
compute_enriched_today 若只判「列是否存在」, 这些 null 行当日 change_pct /
amplitude 永远为空, 且每轮实时刷新覆写自己 — 自锁, 不会自愈。
"""
from __future__ import annotations

from datetime import date

import polars as pl

from app.indicators.pipeline import compute_enriched_today

TODAY = date(2026, 11, 19)


def _live_state() -> pl.DataFrame:
    base = {
        "ema5": [9.5, 19.0], "ema10": [9.6, 19.2], "ema20": [9.7, 19.4],
        "ema30": [9.8, 19.6], "ema60": [9.9, 19.8],
        "macd_dea": [-0.1, 0.1], "kdj_k": [40.0, 60.0], "kdj_d": [45.0, 55.0],
        "atr_14": [0.2, 0.4], "close": [9.43, 19.0], "high": [9.5, 19.2],
        "low": [9.3, 18.8], "annual_vol_20d": [0.3, 0.3],
        "_ema12": [9.6, 19.1], "_ema26": [9.7, 19.3],
        "_vol_19d_pct_sum": [0.0, 0.0], "_vol_19d_pct_sq_sum": [0.0, 0.0],
        "_prev_consec_up": pl.Series([0, 0], dtype=pl.UInt32),
        "_prev_consec_down": pl.Series([0, 0], dtype=pl.UInt32),
        "_ma5_partial_sum": [38.0, 95.0], "_ma10_partial_sum": [94.0, 190.0],
        "_ma20_partial_sum": [175.0, 380.0], "_ma30_partial_sum": [290.0, 570.0],
        "_ma60_partial_sum": [664.0, 1140.0], "_boll_partial_sum": [188.0, 380.0],
        "_boll_partial_sq_sum": [1770.0, 7220.0],
        "_high_59d": [9.9, 19.9], "_low_59d": [9.1, 18.1],
        "_close_3d_ago": [9.3, 18.3], "_close_5d_ago": [9.35, 18.5],
        "_close_10d_ago": [9.4, 18.6], "_close_20d_ago": [9.5, 18.7],
        "_close_30d_ago": [9.6, 18.9], "_close_60d_ago": [9.9, 19.5],
        "_vol_ma5_partial_sum": [4000.0, 8000.0], "_vol_ma10_partial_sum": [9000.0, 18000.0],
        "_vol_ma5_prev_sum": [5000.0, 10000.0],
        "_kdj_8d_low": [9.2, 18.2], "_kdj_8d_high": [9.6, 19.6],
        "_window_len": [59, 59],
        "_rsi_avg_gain_6": [0.01, 0.02], "_rsi_avg_loss_6": [0.02, 0.01],
        "_rsi_avg_gain_14": [0.01, 0.02], "_rsi_avg_loss_14": [0.02, 0.01],
        "_rsi_avg_gain_24": [0.01, 0.02], "_rsi_avg_loss_24": [0.02, 0.01],
    }
    return pl.DataFrame({"symbol": ["600001.SH", "000820.SZ"], **base})


def _today_ohlcv() -> pl.DataFrame:
    # 600001 有源给的 change_pct, 000820 同一列是 null (源只给了部分标的)
    return pl.DataFrame({
        "symbol": ["600001.SH", "000820.SZ"],
        "date": [TODAY, TODAY],
        "open": [19.1, 9.43], "high": [19.2, 9.7], "low": [18.8, 9.3],
        "close": [19.2, 9.6], "volume": [1000.0, 1000.0],
        "amount": [19200.0, 9600.0],
        "prev_close": [19.0, 9.43],
        "change_pct": [0.0105, None],
    })


def test_partial_null_change_pct_is_backfilled():
    result = compute_enriched_today(
        live_agg=_live_state(),
        prev_enriched=pl.DataFrame(),
        today_ohlcv=_today_ohlcv(),
        instruments=None,
    )
    row = result.filter(pl.col("symbol") == "000820.SZ")
    # 源值为 null → 按 close/prev_close-1 回算
    got = row["change_pct"][0]
    assert got is not None and abs(got - (9.6 / 9.43 - 1)) < 1e-9

def test_source_value_kept_when_present():
    result = compute_enriched_today(
        live_agg=_live_state(),
        prev_enriched=pl.DataFrame(),
        today_ohlcv=_today_ohlcv(),
        instruments=None,
    )
    assert result.filter(pl.col("symbol") == "600001.SH")["change_pct"][0] == 0.0105

def test_amplitude_present_column_still_backfilled_per_row():
    result = compute_enriched_today(
        live_agg=_live_state(),
        prev_enriched=pl.DataFrame(),
        today_ohlcv=_today_ohlcv().with_columns(pl.Series("amplitude", [None, None])),
        instruments=None,
    ).sort("symbol")
    # 列在但两行都 null → 必须回算 (high-low)/prev_close, 不能留空
    assert result["amplitude"].null_count() == 0
    got = result.filter(pl.col("symbol") == "000820.SZ")["amplitude"][0]
    assert abs(got - (9.7 - 9.3) / 9.43) < 1e-9