"""盘中增量 enriched 的「昨日递推状态」必须排除今天。

get_enriched_history(today, N) 的窗口是 `date <= today`; 盘中 today 分区已被前几轮
实时落盘, 窗口最后一根就是今天。直接取最后一根当昨日值, 交叉类信号
(signal_ma5_breakout / signal_macd_golden) 的 _prev_* 退化成今日自身 → 当日恒 false
并写盘, 该日信号全丢。
"""
from __future__ import annotations

from datetime import date

import polars as pl

from app.services.quote_service import _latest_bar_before

TODAY = date(2026, 11, 19)


def _history() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": ["000001.SZ"] * 3,
            "date": [date(2026, 11, 14), date(2026, 11, 18), TODAY],
            "close": [10.0, 10.5, 11.0],
        }
    )


def test_excludes_today_bar():
    out = _latest_bar_before(_history(), TODAY)
    assert out.height == 1
    assert out["date"][0] == date(2026, 11, 18)
    assert out["close"][0] == 10.5


def test_weekend_gap_uses_last_trading_day():
    # today = 周一, 历史里最近的是上周五 — 仍是「昨天」
    out = _latest_bar_before(_history().drop("close"), date(2026, 12, 1))
    assert out["date"].max() == TODAY


def test_empty_history_returns_empty():
    assert _latest_bar_before(pl.DataFrame(), TODAY).is_empty()


def test_no_earlier_bar_returns_empty():
    # 首日上线: 历史里只有今天, 没有可用的昨日值 → 必须走全量回退, 不能拿今天当昨日
    only_today = _history().filter(pl.col("date") == TODAY)
    assert _latest_bar_before(only_today, TODAY).is_empty()