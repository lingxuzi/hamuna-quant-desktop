"""截面工具必须确定、不能因数据里的并列值整批抛异常。

这两个工具是二次开发接口的候选 (目前无内部调用点), 一旦接出去, 行为不确定
(rank method="random") 或并列值直接 DuplicateError 都是线上事故:
同一份数据两次跑出不同因子值, 参数优化不可比; 低波动日整批因子回测失败。
"""
from __future__ import annotations

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine


def _panel() -> pl.DataFrame:
    # 同日大量并列: 全市场跌停/一字板日的真实形态
    return pl.DataFrame({
        "date": ["2026-11-18"] * 8,
        "x": [1.0, 1.0, 1.0, 1.0, 2.0, 3.0, 4.0, 5.0],
    })


def test_rank_is_deterministic_with_ties():
    first = BacktestEngine.cross_section_rank(_panel(), "x")["x_rank"].to_list()
    for _ in range(5):
        assert BacktestEngine.cross_section_rank(_panel(), "x")["x_rank"].to_list() == first
    # average 秩: 4 个并列最小值得 (1+2+3+4)/4 = 2.5
    assert first[:4] == [2.5] * 4
    assert first[4:] == [5.0, 6.0, 7.0, 8.0]


def test_qcut_does_not_raise_on_duplicate_quantiles():
    out = BacktestEngine.cross_section_qcut(_panel(), "x", 5)
    labels = out["_group"].to_list()
    assert out.height == 8
    # 并列把分位点压重复 → 实际组数少于 5, 但每行都有标签 (无 null)
    assert all(label is not None for label in labels)
    assert len(set(labels)) < 5


def test_qcut_still_works_without_ties():
    panel = pl.DataFrame({"date": ["2026-11-18"] * 5, "x": [1.0, 2.0, 3.0, 4.0, 5.0]})
    out = BacktestEngine.cross_section_qcut(panel, "x", 5)
    assert sorted(out["_group"].to_list()) == ["Q1", "Q2", "Q3", "Q4", "Q5"]