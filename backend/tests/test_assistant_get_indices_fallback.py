"""get_indices 回落路径: 实时缓存为空时必须读本地指数日线, 不能空手返回。

盘前 / 节假日 / 未开实时开关时 quote_service 缓存为空, 旧实现直接返回
{"count": 0, "note": "指数实时缓存为空。"} —— 模型拿不到点位只能凭记忆编。
"""
from __future__ import annotations

import polars as pl
import pytest

from app.custom.assistant import tools as assistant_tools


class _StubRepo:
    def execute_all(self, sql: str, params: list | None = None) -> list[tuple]:
        # (symbol, name, date, last_price, prev_close, change_pct)
        return [
            ("000001.SH", "上证指数", "2026-11-18", 3777.0, 3745.0, (3777.0 - 3745.0) / 3745.0 * 100),
            ("399001.SZ", "深证成指", "2026-11-18", 12000.0, 12000.0, 0.0),
        ]


class _EmptyQuoteService:
    def get_index_quotes(self, symbols=None) -> pl.DataFrame:
        return pl.DataFrame()


def test_falls_back_to_local_index_daily_when_cache_empty() -> None:
    ctx = assistant_tools.ToolContext.build(repo=_StubRepo(), quote_service=_EmptyQuoteService())
    result = assistant_tools._get_indices({}, ctx)

    assert result["source"] == "index_daily"
    assert result["count"] == 2
    rows = {r["symbol"]: r for r in result["rows"]}
    assert rows["000001.SH"]["name"] == "上证指数"
    assert rows["000001.SH"]["last_price"] == pytest.approx(3777.0)
    # change_pct 与实时缓存同口径: 百分数
    assert rows["000001.SH"]["change_pct"] == pytest.approx(0.8545, abs=1e-3)


def test_prefers_realtime_cache_when_available() -> None:
    class _LiveQuoteService:
        def get_index_quotes(self, symbols=None) -> pl.DataFrame:
            return pl.DataFrame({
                "symbol": ["000001.SH"], "name": ["上证指数"],
                "last_price": [3777.0], "change_pct": [0.85],
            })

    ctx = assistant_tools.ToolContext.build(repo=_StubRepo(), quote_service=_LiveQuoteService())
    result = assistant_tools._get_indices({}, ctx)

    assert result["source"] == "realtime"
    assert result["rows"][0]["change_pct"] == 0.85


def test_no_data_at_all_is_reported_not_crashed() -> None:
    class _NoopRepo:
        def execute_all(self, sql: str, params: list | None = None) -> list[tuple]:
            return []

    ctx = assistant_tools.ToolContext.build(repo=_NoopRepo())
    result = assistant_tools._get_indices({}, ctx)
    assert result["count"] == 0
    assert "为空" in result["note"]