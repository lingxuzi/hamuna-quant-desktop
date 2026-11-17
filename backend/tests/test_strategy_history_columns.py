"""filter_history 裁剪列后, 实盘口径仍跑得通且基础过滤不失效。

回归: 策略在 filter_history 里 select 只保留自己用的列 (AI 生成的策略常这么写),
引擎拿到的是裁剪后的帧 —— 后续基础过滤读 amount/turnover_rate 会 ColumnNotFoundError
(实盘直接挂), 回测路径却因为走全量 panel 一直正常, 两边口径分叉。
"""
from __future__ import annotations

import dataclasses
from datetime import date

import polars as pl
import pytest

from app.strategy.engine import StrategyDataContext, StrategyDef, StrategyEngine


def _narrowing_strategy(sid: str) -> StrategyDef:
    def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
        return df.select(["symbol", "date", "close"])

    return StrategyDef(
        meta={"id": sid, "params": [], "scoring": {}, "limit": 100},
        basic_filter={"amount_min": 1e8, "turnover_min": 1.0},
        entry_signals=[],
        exit_signals=[],
        stop_loss=None,
        trailing_stop=None,
        trailing_take_profit_activate=None,
        trailing_take_profit_drawdown=None,
        max_hold_days=None,
        filter_fn=None,
        filter_history_fn=filter_history,
        lookback_days=8,
        source="custom",
        execution_backend="python_history_legacy",
    )


def _strategy_without_date(sid: str) -> StrategyDef:
    def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
        return df.select(["symbol", "close"])

    return dataclasses.replace(
        _narrowing_strategy(sid), filter_history_fn=filter_history
    )


def _context(target: date) -> StrategyDataContext:
    return StrategyDataContext(
        asset_type="stock",
        timeframe="1d",
        as_of=target,
        current=None,
        history=pl.DataFrame({
            "symbol": ["A", "B"],
            "date": [target, target],
            "close": [10.0, 10.0],
            "amount": [5e8, 5e8],
            "turnover_rate": [2.0, 0.5],
            "name": ["甲", "乙"],
        }),
    )


def test_narrowed_filter_history_does_not_break_basic_filter():
    """裁掉 amount/turnover_rate 后不报缺列错, 且换手率门槛照常生效 (B 被过滤)。"""
    sid = "custom_narrow_cols"
    engine = StrategyEngine(strategy_dirs=[])
    engine._strategies[sid] = _narrowing_strategy(sid)

    result = engine.run(sid, _context(date(2026, 7, 15)))
    assert [r["symbol"] for r in result.rows] == ["A"]


def test_filter_history_without_date_column_fails_loudly():
    """返回帧缺 date 列 → 明确报错, 不能把整段历史窗口当成当日推荐。"""
    sid = "custom_no_date_col"
    engine = StrategyEngine(strategy_dirs=[])
    engine._strategies[sid] = _strategy_without_date(sid)

    with pytest.raises(ValueError, match="date"):
        engine.run(sid, _context(date(2026, 7, 15)))
