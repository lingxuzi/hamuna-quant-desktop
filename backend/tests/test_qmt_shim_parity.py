"""QMT shim 与仓库矩阵算子逐 feature 对拍 —— 导出代码口径不漂移的唯一硬保证。"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from app.backtest import matrix as real_matrix
from app.services import qmt_shim

_FEATURES = (
    "prev_close",
    "change_pct",
    "change_amount",
    "amplitude",
    "momentum_5d",
    "momentum_20d",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "ma5_bias",
    "ema12",
    "ema26_bias",
    "macd_dif",
    "macd_dea",
    "macd_hist",
    "rsi_14",
    "rsi_6",
    "vol_ratio_5d",
    "vol_ratio_10d",
    "vol_ma5",
    "vol_ma10",
    "vol_trend_5_10",
    "boll_upper",
    "boll_lower",
    "high_60d",
    "low_60d",
    "annual_vol_20d",
    "close_position",
    "gap_return",
    "intraday_return",
    "distance_to_high_60d",
    "distance_from_low_60d",
    "kdj_k",
    "kdj_d",
    "kdj_j",
    "atr_14",
    "atr_pct",
    "turnover_rate",
    "price_limit_pct",
    "consecutive_limit_ups",
    "limit_up_count_60d",
)

_SYMBOLS = ("600000.SH", "000001.SZ", "300750.SZ", "688981.SH")
_NAMES = ("浦发银行", "平安银行", "宁德时代", "中芯国际")
_FIELD_COLUMNS = {"turnover_rate", "consecutive_limit_ups", "price_limit_pct"}


def _panel(days: int = 90) -> pl.DataFrame:
    """带停牌缺口的合成面板：每个标的随机断档，且含 ST 名称变体。"""
    rows = []
    start = date(2026, 1, 5)
    for offset in range(days):
        trade_date = start + timedelta(days=offset)
        for asset_id, symbol in enumerate(_SYMBOLS):
            # 资产 1 每 7 天停 2 天，资产 2 后半段整段停牌
            if asset_id == 1 and offset % 7 in (3, 4):
                continue
            if asset_id == 2 and offset >= 60:
                continue
            base = 10.0 + asset_id * 5.0
            close = base * (1.0 + 0.01 * np.sin(offset * 0.7 + asset_id))
            rows.append({
                "symbol": symbol,
                "date": trade_date,
                "open": close * 1.003,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": 1000.0 + offset * 7 + asset_id * 13,
                "name": _NAMES[asset_id],
                "turnover_rate": 1.5 + offset * 0.05 + asset_id,
                "consecutive_limit_ups": float(offset % 4 == 0),
            })
    return pl.DataFrame(rows).sort(["symbol", "date"])


def _shim_market(real: real_matrix.MarketDataMatrix) -> qmt_shim.MarketDataMatrix:
    fields = dict(real.fields)
    # 涨跌停矩阵：shim 用日期整数，仓库用 date
    trading_dates = [
        int(str(value).replace("-", "")[:8]) for value in
        sorted({row for row in _dates(real)})
    ]
    fields["price_limit_pct"] = qmt_shim._price_limit_pct_matrix(
        qmt_shim.MarketDataMatrix(
            real.open, real.high, real.low, real.close, real.volume,
            real.tradable, real.limit_up_locked, real.limit_down_locked,
            {}, real.symbols, real.names, trading_dates,
        ),
        trading_dates,
    )
    valid = np.isfinite(real.close)
    fields["price_limit_pct"] = np.where(
        valid, fields["price_limit_pct"], np.nan).astype(np.float32)
    return qmt_shim.MarketDataMatrix(
        real.open, real.high, real.low, real.close, real.volume,
        real.tradable, real.limit_up_locked, real.limit_down_locked,
        fields, real.symbols, real.names, trading_dates,
    )


def _dates(market: real_matrix.MarketDataMatrix):
    """从 timestamp_labels 还原交易日（每根 K 线的日期部分）。"""
    return [label[:10] for label in market.timestamp_labels]


def _build():
    panel = _panel()
    real = real_matrix.build_market_data_matrix(panel, field_columns=_FIELD_COLUMNS)
    return real, _shim_market(real)


@pytest.mark.parametrize("feature", _FEATURES)
def test_shim_feature_matches_repository(feature: str) -> None:
    real, shim = _build()
    expected = real_matrix.matrix_feature(real, feature)
    actual = qmt_shim.matrix_feature(shim, feature)
    assert actual.shape == expected.shape
    np.testing.assert_allclose(
        actual.astype(np.float64), expected.astype(np.float64),
        rtol=1e-5, atol=1e-6, equal_nan=True,
        err_msg=f"feature diverged: {feature}",
    )


def _shift_cases():
    return [
        ("shift+1 all valid", 1, None),
        ("shift+1 with gaps", 1, "gaps"),
        ("shift+3 with gaps", 3, "gaps"),
        ("shift-1 with gaps", -1, "gaps"),
        ("shift+5 volume mask", 5, "volume"),
    ]


@pytest.mark.parametrize("label,periods,mode", _shift_cases())
def test_valid_shift_matches_repository(label, periods, mode):
    del label
    real, shim = _build()
    if mode is None:
        values = real.close.copy()
        mask = np.ones_like(real.close, dtype=bool)
        s_values = shim.close.copy()
        s_mask = np.ones_like(shim.close, dtype=bool)
    elif mode == "gaps":
        values = real.close
        mask = np.isfinite(real.close)
        s_values = shim.close
        s_mask = np.isfinite(shim.close)
    else:
        values = real.volume
        mask = np.isfinite(real.close) & np.isfinite(real.volume)
        s_values = shim.volume
        s_mask = np.isfinite(shim.close) & np.isfinite(shim.volume)
    expected = real_matrix.valid_shift(values, periods, mask)
    actual = qmt_shim.valid_shift(s_values, periods, s_mask)
    np.testing.assert_allclose(actual, expected, rtol=1e-6, equal_nan=True)


@pytest.mark.parametrize("window,ddof", [(5, 0), (20, 0), (20, 1), (60, 1)])
def test_valid_rolling_std_matches_repository(window, ddof):
    real, _shim = _build()
    valid = np.isfinite(real.close)
    expected = real_matrix.valid_rolling_std(real.close, valid, window, ddof=ddof)
    actual = qmt_shim.valid_rolling_std(real.close, valid, window, ddof=ddof)
    np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=1e-6, equal_nan=True)


@pytest.mark.parametrize("span", [3, 12, 26])
def test_valid_ewm_matches_repository(span):
    real, _shim = _build()
    valid = np.isfinite(real.close)
    expected = real_matrix.valid_ewm_adjust_false(real.close, valid, span=span)
    actual = qmt_shim.valid_ewm_adjust_false(real.close, valid, span=span)
    np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=1e-6, equal_nan=True)


def test_price_limit_matrix_matches_repository() -> None:
    """ST 5% 带宽 / 创业板注册制前 10% 两条历史门控都要对齐。"""
    from app.price_limits import (
        GEM_20PCT_LIMIT_START_DATE,
        MAIN_BOARD_ST_LIMIT_CHANGE_DATE,
        numpy_price_limit_matrix,
    )

    dates = [
        GEM_20PCT_LIMIT_START_DATE - timedelta(days=1),
        GEM_20PCT_LIMIT_START_DATE,
        MAIN_BOARD_ST_LIMIT_CHANGE_DATE,
    ]
    symbols = ("600000.SH", "300750.SZ", "688981.SH", "830799.BJ")
    names = ("*ST浦发", "宁德时代", "中芯国际", "艾融软件")
    expected = numpy_price_limit_matrix(dates, symbols, names)

    market = qmt_shim.MarketDataMatrix(
        np.zeros((len(dates), len(symbols)), dtype=np.float32),
        np.zeros((len(dates), len(symbols)), dtype=np.float32),
        np.zeros((len(dates), len(symbols)), dtype=np.float32),
        np.zeros((len(dates), len(symbols)), dtype=np.float32),
        np.zeros((len(dates), len(symbols)), dtype=np.float32),
        None, None, None, {}, symbols, names,
        [int(value.strftime("%Y%m%d")) for value in dates],
    )
    actual = qmt_shim._price_limit_pct_matrix(
        market, [int(value.strftime("%Y%m%d")) for value in dates])
    np.testing.assert_allclose(actual, expected, rtol=0)


def test_limit_price_matches_repository() -> None:
    from app.price_limits import numpy_limit_price

    previous = np.array([9.99, 10.005, 3.33, 128.77], dtype=np.float32)
    pct = np.array([0.10, 0.10, 0.05, 0.20], dtype=np.float32)
    for up in (True, False):
        expected = numpy_limit_price(previous, pct, up=up)
        actual = qmt_shim.limit_price(previous, pct, up=up)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-9)


def test_make_signal_matrix_defaults_match_repository() -> None:
    shape = (5, 3)
    expected = real_matrix.make_signal_matrix(shape)
    actual = qmt_shim.make_signal_matrix(shape)
    for name in ("entry", "exit", "score", "entry_signal_code", "exit_signal_code"):
        np.testing.assert_array_equal(
            getattr(actual, name), getattr(expected, name))
        assert getattr(actual, name).dtype == getattr(expected, name).dtype


def test_alias_names_exist() -> None:
    """策略里 ``import valid_shift as shift`` 之类写法剥离 import 后必须有名字。"""
    assert qmt_shim.shift is qmt_shim.valid_shift
    assert qmt_shim.ewm_adjust_false is qmt_shim.valid_ewm_adjust_false

def test_basic_filter_mask_matches_repository() -> None:
    from app.backtest.matrix import build_basic_filter_mask

    real, shim = _build()
    configs = [
        {"enabled": True, "price_min": 10.0, "price_max": 30.0},
        {"enabled": True, "exclude_st": True},
        {"enabled": True, "boards": ["沪主板", "创业板"]},
        {"enabled": True, "turnover_min": 2.0},
        {"enabled": False},
    ]
    for config in configs:
        expected = build_basic_filter_mask(real, config)
        actual = qmt_shim.build_basic_filter_mask(shim, config)
        np.testing.assert_array_equal(actual, expected, err_msg=str(config))


@pytest.mark.parametrize("order_by,descending", [(None, True), ("change_pct", True), ("change_pct", False)])
def test_matrix_score_matches_repository(order_by, descending) -> None:
    from app.backtest.matrix import build_matrix_score

    real, shim = _build()
    universe = np.isfinite(real.close)
    scoring = {"momentum_20d": 0.5, "vol_ratio_5d": 0.3, "change_pct": 0.2}
    fallback = np.zeros(real.shape, dtype=np.float32)
    expected = build_matrix_score(
        real, universe, scoring, order_by, descending, fallback=fallback)
    actual = qmt_shim.build_matrix_score(
        shim, universe, scoring, order_by, descending, fallback=fallback)
    np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=1e-5, equal_nan=True)


def test_matrix_score_low_direction_matches_repository() -> None:
    from app.backtest.matrix import build_matrix_score

    real, shim = _build()
    universe = np.isfinite(real.close)
    scoring = {"annual_vol_20d": 1.0}
    fallback = np.zeros(real.shape, dtype=np.float32)
    expected = build_matrix_score(
        real, universe, scoring, None, True, fallback=fallback,
        directions={"annual_vol_20d": "low"})
    actual = qmt_shim.build_matrix_score(
        shim, universe, scoring, None, True, fallback=fallback,
        directions={"annual_vol_20d": "low"})
    np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=1e-5, equal_nan=True)
