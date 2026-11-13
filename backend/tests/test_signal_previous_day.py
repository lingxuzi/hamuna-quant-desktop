"""交叉类信号必须有「昨日值」才算得出来。

当日(实时)路径依赖 compute_enriched_today 把上一交易日的指标 join 成
``_prev_*`` 列; 这一步没生效时 build_expressions_prev 会整条跳过该信号,
帧上缺列, 消费端口径下该信号当日恒 false —— 与「今天确实没金叉」不可区分。
线上表现: 盘后 enrich 的快照里 MA5/MA20 金叉等信号全市场为 0, 按 as_of
重算同一交易日却有一百多只命中。
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators import pipeline
from app.indicators.pipeline import compute_enriched_today, custom_signals_missing_today
from app.strategy import custom_signals

GOLDEN = {
    "id": "ma_golden_5_20", "name": "MA5上穿MA20", "kind": "both",
    "enabled": True, "timeframe": "daily", "column": "signal_ma_golden_5_20",
    "conditions": [
        {"left": "ma5", "op": ">", "right": "field:ma20", "leftDays": 0, "rightDays": 0},
        {"left": "ma5", "op": "<=", "right": "field:ma20", "leftDays": 1, "rightDays": 1},
    ],
}
TODAY = date(2026, 11, 13)
# 今日 ma5 = (38.0+9.43)/5 = 9.486, 今日 ma20 = (175+9.43)/20 = 9.2215 → 今日 ma5 > ma20;
# 昨日 ma5 9.48 < 昨日 ma20 9.60 → 金叉成立。



@pytest.fixture
def golden_caches(monkeypatch):
    prev_fields = custom_signals.prev_day_fields([GOLDEN])
    monkeypatch.setattr(
        pipeline, "_custom_signal_exprs_today",
        custom_signals.build_expressions_prev([GOLDEN], prev_fields),
    )
    monkeypatch.setattr(pipeline, "_custom_prev_fields_cache", prev_fields)
    custom_signals.missing_today_signals.clear()
    yield
    custom_signals.missing_today_signals.clear()


def _live_state() -> pl.DataFrame:
    base = {
        "ema5": [9.5], "ema10": [9.6], "ema20": [9.7], "ema30": [9.8], "ema60": [9.9],
        "macd_dea": [-0.1], "kdj_k": [40.0], "kdj_d": [45.0], "atr_14": [0.2],
        "close": [9.43], "high": [9.5], "low": [9.3], "annual_vol_20d": [0.3],
        "_ema12": [9.6], "_ema26": [9.7], "_adj_factor": [1.0],
        "_vol_19d_pct_sum": [0.0], "_vol_19d_pct_sq_sum": [0.0],
        "_prev_consec_up": pl.Series([0], dtype=pl.UInt32),
        "_prev_consec_down": pl.Series([0], dtype=pl.UInt32),
        "_ma5_partial_sum": [38.0], "_ma10_partial_sum": [94.0],
        "_ma20_partial_sum": [175.0], "_ma30_partial_sum": [290.0],
        "_ma60_partial_sum": [664.0], "_boll_partial_sum": [188.0],
        "_boll_partial_sq_sum": [1770.0], "_high_59d": [9.9], "_low_59d": [9.1],
        "_close_3d_ago": [9.3], "_close_5d_ago": [9.35], "_close_10d_ago": [9.4],
        "_close_20d_ago": [9.5], "_close_30d_ago": [9.6], "_close_60d_ago": [9.9],
        "_vol_ma5_partial_sum": [4000.0], "_vol_ma10_partial_sum": [9000.0],
        "_vol_ma5_prev_sum": [5000.0], "_kdj_8d_low": [9.2], "_kdj_8d_high": [9.6],
        "_window_len": [59],
        "_rsi_avg_gain_6": [0.01], "_rsi_avg_loss_6": [0.02],
        "_rsi_avg_gain_14": [0.01], "_rsi_avg_loss_14": [0.02],
        "_rsi_avg_gain_24": [0.01], "_rsi_avg_loss_24": [0.02],
    }
    return pl.DataFrame({"symbol": ["600001.SH"], **base})


def _today_ohlcv() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600001.SH"], "date": [TODAY],
        "open": [9.43], "high": [9.5], "low": [9.3], "close": [9.43],
        "volume": [1000.0], "amount": [9430.0],
    })


def _previous_enriched() -> pl.DataFrame:
    # 昨日 ma5 < ma20, 今日 ma5 > ma20 (9.486 > 9.6715? 不成立前先看 ma20) → 见 _live_state 注释
    return pl.DataFrame({
        "symbol": ["600001.SH"], "date": [TODAY - timedelta(days=1)],
        "ma5": [9.48], "ma10": [9.50], "ma20": [9.60], "ma60": [9.9],
    })


def test_golden_cross_true_when_previous_day_joined(golden_caches):
    result = compute_enriched_today(
        live_agg=_live_state(),
        prev_enriched=_previous_enriched(),
        today_ohlcv=_today_ohlcv(),
        instruments=None,
    )
    assert result["signal_ma_golden_5_20"].item() is True
    assert custom_signals_missing_today() == []


def test_missing_previous_day_is_visible_not_silently_false(golden_caches):
    """昨日值缺席时不静默: 信号列缺失, 且 missing_today_signals 记名供诊断/补算。"""
    custom_signals.missing_today_signals.clear()
    custom_signals.build_expressions_prev([GOLDEN], set())
    assert custom_signals_missing_today() == ["signal_ma_golden_5_20"]
    result = compute_enriched_today(
        live_agg=_live_state(),
        prev_enriched=pl.DataFrame(),
        today_ohlcv=_today_ohlcv(),
        instruments=None,
    )
    assert "signal_ma_golden_5_20" not in result.columns
