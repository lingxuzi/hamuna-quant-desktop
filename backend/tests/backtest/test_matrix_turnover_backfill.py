"""矩阵换手率空格回填: 深市历史分区整列为 null 时不得静默参与筛选。

数据源不提供深市换手率, 维表股本补齐之前落盘的 enriched 分区该列整段为 null。
矩阵侧若原样按 NaN 参与 turnover 条件, 该板块在策略/挖掘里被整段剔除 ——
同一份数据 polars 路径 (compute_limit_signals 逐行回填) 与矩阵路径结论不一致。

已核实沪市真值与 volume*1e4/float_shares 逐行一致 (2023-04-24 / 2025-09-08),
因此回填不引入新口径。
"""
from __future__ import annotations

import numpy as np

from app.backtest.matrix import _fill_turnover_rate_matrix


def test_fills_nan_cells_only():
    target = np.array([[np.nan, 3.0], [np.nan, np.nan]], dtype=np.float32)
    volume = np.array([[1000.0, 1000.0], [1000.0, np.nan]], dtype=np.float32)
    shares = np.array([100_000.0, 200_000.0], dtype=np.float32)
    seen = np.ones((2, 2), dtype=bool)

    _fill_turnover_rate_matrix(target, volume, shares, seen)

    # 已有值 (3.0) 原样保留; 空缺按 volume*1e4/股本; 无成交量/无股本的格子仍为 NaN
    assert target[0, 0] == 100.0
    assert target[0, 1] == 3.0
    assert target[1, 0] == 100.0
    assert np.isnan(target[1, 1])


def test_unseen_cells_not_filled():
    target = np.full((1, 2), np.nan, dtype=np.float32)
    volume = np.array([[1000.0, 1000.0]], dtype=np.float32)
    shares = np.array([100_000.0, 100_000.0], dtype=np.float32)
    seen = np.array([[True, False]])

    _fill_turnover_rate_matrix(target, volume, shares, seen)

    # seen=False 是没有该标的该日行情, 不是数据缺失, 不能凭空造换手率
    assert target[0, 0] == 100.0
    assert np.isnan(target[0, 1])


def test_zero_or_missing_shares_leaves_nan():
    target = np.full((1, 2), np.nan, dtype=np.float32)
    volume = np.array([[1000.0, 1000.0]], dtype=np.float32)
    shares = np.array([0.0, np.nan], dtype=np.float32)
    seen = np.ones((1, 2), dtype=bool)

    _fill_turnover_rate_matrix(target, volume, shares, seen)

    assert np.isnan(target).all()