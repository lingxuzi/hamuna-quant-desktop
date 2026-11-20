"""矩阵涨跌停锁的带宽判定: 不得用「今天的名字」套全历史, 且认注册制无涨跌幅窗口。

instruments.name 是同步当日快照。拿它判全历史, 今天 ST 的票在过去两年都按 ±5%
判定 → 当日实际 ±10% 的 9% 波动被当成涨停锁死; 注册制新股上市前 5 个交易日
根本没有涨跌幅, 却按 ±10% 判出锁死。两者都让回测拒绝成交, 结果与真实市场不符。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest
import polars as pl

from app.backtest.matrix import _limit_lock_matrices, _no_limit_mask


def _limits(n: int = 1):
    return {
        "limit_up": np.full(n, np.nan, dtype=np.float32),
        "limit_down": np.full(n, np.nan, dtype=np.float32),
    }


def _run(close, high, low, dates, symbol, name, no_limit=None):
    close = np.asarray(close, dtype=np.float32).reshape(-1, 1)
    seen = np.ones(close.shape, dtype=bool)
    return _limit_lock_matrices(
        close,
        close.copy(),
        seen,
        list(dates),
        [symbol],
        [name],
        _limits(),
        no_limit=no_limit,
        high=np.asarray(high, dtype=np.float32).reshape(-1, 1),
        low=np.asarray(low, dtype=np.float32).reshape(-1, 1),
    )


def test_non_st_day_not_locked_by_todays_st_name():
    # 603398.SH 现名 *ST沐邦; 2025-03-04 当日实际 ±10%。
    # 前收 18.00: 5% 涨停价 18.995 (会被当日最高 19.00 误判为涨停),
    # 10% 涨停价 20.25。带宽自证必须把该行切回 10% 带 → 不锁。
    up, down = _run(
        close=[18.0, 18.41],
        high=[18.0, 19.00],
        low=[17.9, 18.0],
        dates=[date(2025, 3, 3), date(2025, 3, 4)],
        symbol="603398.SH",
        name="*ST沐邦",
    )
    assert up[1, 0] == 0
    assert down[1, 0] == 0


def test_real_st_limit_kept_when_no_wider_evidence():
    # 真 ST: 涨停日收盘 = 5% 涨停价, 最高价不越界 → 保留 5% 带
    up, _ = _run(
        close=[10.0, 10.5],
        high=[10.0, 10.5],
        low=[10.0, 10.1],
        dates=[date(2025, 3, 3), date(2025, 3, 4)],
        symbol="600000.SH",
        name="*ST某某",
    )
    assert up[1, 0] == 1


def test_registration_new_stock_no_limit_window():
    # 001232.SZ 上市 2026-08-04: 前 5 个交易日无涨跌幅, 涨跌 11.6% 也不算锁死
    dates = [date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6), date(2026, 8, 7)]
    instruments = pl.DataFrame(
        {"symbol": ["001232.SZ"], "listing_date": [date(2026, 8, 4)]}
    )
    seen = np.ones((4, 1), dtype=bool)
    mask = _no_limit_mask(instruments, ["001232.SZ"], dates, seen)
    assert mask is not None and mask.all()

    up, down = _run(
        close=[190.0, 200.0, 176.81, 198.29],
        high=[200.0, 210.0, 180.0, 200.0],
        low=[180.0, 190.0, 176.81, 178.0],
        dates=dates,
        symbol="001232.SZ",
        name="嘉立创",
        no_limit=mask,
    )
    assert up.sum() == 0
    assert down.sum() == 0


def test_no_limit_mask_none_without_listing_date():
    instruments = pl.DataFrame({"symbol": ["000001.SZ"]})
    seen = np.ones((1, 1), dtype=bool)
    assert _no_limit_mask(instruments, ["000001.SZ"], [date(2026, 1, 5)], seen) is None


def test_old_stock_has_no_no_limit_window():
    # 注册制改革前上市的老股不适用无涨跌幅窗口
    instruments = pl.DataFrame({"symbol": ["600000.SH"], "listing_date": [date(2010, 1, 4)]})
    seen = np.ones((1, 1), dtype=bool)
    assert _no_limit_mask(instruments, ["600000.SH"], [date(2026, 1, 5)], seen) is None