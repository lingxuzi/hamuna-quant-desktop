"""QMT 导出用的 matplotlib-free 纯 numpy shim — 复刻 ``app/backtest/matrix.py`` 的算子语义。

这份文件会被原样内联进导出的 QMT 脚本，因此必须：

1. **Python 3.6 兼容**（QMT 内嵌 3.6）——不能用 dataclass / f-string 调试语法 /
   任何下标式类型注解（``frozenset[str]`` 在 3.6 会 TypeError）。
2. **只依赖 numpy**——QMT 环境有 numpy/pandas，没有 polars/numba。
3. 语义与 ``app/backtest/matrix.py`` 逐格一致，由 ``tests/test_qmt_shim_parity.py`` 对拍。

核心语义：所有滚动/平移算子都按「有效观测」推进而非按日历行推进 —— 停牌日
（close 为 NaN）不算一次观测。这是矩阵路径与 polars 路径一致的前提。
"""
import numpy as np


# ── 数据结构 ──────────────────────────────────────────────
class MarketDataMatrix(object):
    """与仓库同名对象的字段兼容子集。"""

    def __init__(self, open_, high, low, close, volume, tradable,
                 limit_up_locked, limit_down_locked, fields, symbols,
                 names, trading_dates):
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.tradable = tradable
        self.limit_up_locked = limit_up_locked
        self.limit_down_locked = limit_down_locked
        self.fields = fields
        self.symbols = symbols
        self.names = names
        self.trading_dates = trading_dates

    @property
    def shape(self):
        return self.open.shape

    def field(self, name):
        if name == "open":
            return self.open
        if name == "high":
            return self.high
        if name == "low":
            return self.low
        if name == "close":
            return self.close
        if name == "volume":
            return self.volume
        if name in self.fields:
            return self.fields[name]
        raise ValueError("MarketDataMatrix missing field: %s" % name)


class SignalMatrix(object):
    """与仓库同名对象的结构兼容子集。"""

    def __init__(self, entry, exit_, score, entry_signal_code,
                 exit_signal_code, entry_signal_ids, exit_signal_ids):
        self.entry = entry
        self.exit = exit_
        self.score = score
        self.entry_signal_code = entry_signal_code
        self.exit_signal_code = exit_signal_code
        self.entry_signal_ids = entry_signal_ids
        self.exit_signal_ids = exit_signal_ids

    @property
    def shape(self):
        return self.entry.shape


def make_signal_matrix(shape, entry=None, exit=None, score=None,
                       entry_signal_code=None, exit_signal_code=None,
                       entry_signal_ids=(), exit_signal_ids=()):
    """与 ``app/backtest/matrix.py:make_signal_matrix`` 同签名同默认值。"""
    return SignalMatrix(
        _coerce(entry, shape, np.uint8, 0),
        _coerce(exit, shape, np.uint8, 0),
        _coerce(score, shape, np.float32, 0.0),
        _coerce(entry_signal_code, shape, np.int16, -1),
        _coerce(exit_signal_code, shape, np.int16, -1),
        tuple(entry_signal_ids),
        tuple(exit_signal_ids),
    )


def _coerce(values, shape, dtype, fill):
    if values is None:
        return np.full(shape, fill, dtype=dtype)
    array = np.asarray(values, dtype=dtype)
    if array.shape != shape:
        raise ValueError("signal array shape %r != market shape %r"
                         % (array.shape, shape))
    return array


# ── 有效观测索引 ──────────────────────────────────────────
def _asset_rows(valid):
    """按资产切出有效行号 —— 等价于仓库的 ``ValidBarIndex``。

    返回 ``(rows, offsets)``：``rows[offsets[n]:offsets[n+1]]`` 是资产 n
    的有效行号（升序）。纯 numpy，无 numba。
    """
    _, asset_len = valid.shape
    flat = np.asarray(valid, dtype=bool)
    offsets = np.zeros(asset_len + 1, dtype=np.int64)
    for asset_id in range(asset_len):
        offsets[asset_id + 1] = offsets[asset_id] + int(flat[:, asset_id].sum())
    rows = np.empty(int(offsets[-1]), dtype=np.int32)
    cursor = 0
    for asset_id in range(asset_len):
        column = np.flatnonzero(flat[:, asset_id])
        rows[cursor:cursor + len(column)] = column
        cursor += len(column)
    return rows, offsets


def _all_valid(valid):
    return bool(np.asarray(valid, dtype=bool).all())


# ── 平移 ──────────────────────────────────────────────────
def valid_shift(values, periods=1, valid_mask=None, **kwargs):
    """按有效观测平移；停牌行不占位。"""
    del kwargs
    source = np.asarray(values, dtype=np.float32)
    if valid_mask is None:
        valid = np.isfinite(source)
    else:
        valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    if periods == 0:
        out = np.full(source.shape, np.nan, dtype=np.float32)
        out[valid] = source[valid]
        return out

    distance = abs(int(periods))
    if _all_valid(valid):
        out = np.full(source.shape, np.nan, dtype=np.float32)
        if distance < source.shape[0]:
            if periods > 0:
                out[distance:, :] = source[:-distance, :]
            else:
                out[:-distance, :] = source[distance:, :]
        return out

    rows, offsets = _asset_rows(valid)
    out = np.full(source.shape, np.nan, dtype=np.float32)
    for asset_id in range(source.shape[1]):
        start = int(offsets[asset_id])
        stop = int(offsets[asset_id + 1])
        index = rows[start:stop]
        if periods > 0:
            if len(index) > distance:
                out[index[distance:], asset_id] = source[index[:-distance], asset_id]
        else:
            if len(index) > distance:
                out[index[:-distance], asset_id] = source[index[distance:], asset_id]
    return out


# ── 滚动归约 ──────────────────────────────────────────────
_REDUCE_MIN = 0
_REDUCE_MAX = 1
_REDUCE_MEAN = 2
_REDUCE_STD = 3
_REDUCE_SUM = 4


def _rolling_reduce(values, valid_mask, window, operation, ddof=0):
    source = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    window = int(window)
    if window <= 0:
        raise ValueError("rolling window must be positive")

    rows, offsets = _asset_rows(valid)
    out = np.full(source.shape, np.nan, dtype=np.float32)
    denominator = float(window - ddof)
    for asset_id in range(source.shape[1]):
        start = int(offsets[asset_id])
        stop = int(offsets[asset_id + 1])
        index = rows[start:stop]
        if len(index) < window:
            continue
        series = source[index, asset_id].astype(np.float64)
        result = _window_apply(series, window, operation, denominator)
        out[index[window - 1:], asset_id] = result
    return out


def _windows(series, window):
    """只读滑动窗口视图（``as_strided``，兼容 QMT 的老 numpy）。"""
    count = len(series) - window + 1
    stride = series.strides[0]
    return np.lib.stride_tricks.as_strided(
        series, shape=(count, window), strides=(stride, stride), writeable=False)


def _window_apply(series, window, operation, denominator):
    """对一维有效序列做滚动归约，返回长度 ``len(series)-window+1``。"""
    if operation == _REDUCE_MIN:
        return _windows(series, window).min(axis=1).astype(np.float32)
    if operation == _REDUCE_MAX:
        return _windows(series, window).max(axis=1).astype(np.float32)
    view = _windows(series, window)
    total = view.sum(axis=1)
    if operation == _REDUCE_SUM:
        return total.astype(np.float32)
    mean = total / float(window)
    if operation == _REDUCE_MEAN:
        return mean.astype(np.float32)
    # 与仓库同口径：围绕 float64 均值累加平方差，而不是 E[x²]-mean²（后者相消）
    squared = ((view - mean[:, None]) ** 2).sum(axis=1)
    return np.sqrt(squared / denominator).astype(np.float32)


def valid_rolling_mean(values, valid_mask, window, **kwargs):
    del kwargs
    return _rolling_reduce(values, valid_mask, window, _REDUCE_MEAN)


def valid_rolling_std(values, valid_mask, window, ddof=0, **kwargs):
    del kwargs
    return _rolling_reduce(values, valid_mask, window, _REDUCE_STD, ddof=ddof)


def valid_rolling_sum(values, valid_mask, window, **kwargs):
    del kwargs
    return _rolling_reduce(values, valid_mask, window, _REDUCE_SUM)


def valid_rolling_max(values, valid_mask, window, **kwargs):
    del kwargs
    return _rolling_reduce(values, valid_mask, window, _REDUCE_MAX)


def valid_rolling_min(values, valid_mask, window, **kwargs):
    del kwargs
    return _rolling_reduce(values, valid_mask, window, _REDUCE_MIN)


# ── 指数加权 ──────────────────────────────────────────────
def valid_ewm_adjust_false(values, valid_mask, span=None, alpha=None, **kwargs):
    """pandas ``ewm(adjust=False)`` 语义，只在有效观测上推进。"""
    del kwargs
    if alpha is None:
        if span is None or span <= 0:
            raise ValueError("span must be positive when alpha is omitted")
        alpha = 2.0 / (float(span) + 1.0)
    if not 0.0 < float(alpha) <= 1.0:
        raise ValueError("alpha must be in (0, 1]")
    source = np.asarray(values, dtype=np.float32)
    valid = np.asarray(valid_mask, dtype=bool) & np.isfinite(source)
    alpha_value = float(alpha)
    decay = 1.0 - alpha_value

    rows, offsets = _asset_rows(valid)
    out = np.full(source.shape, np.nan, dtype=np.float32)
    for asset_id in range(source.shape[1]):
        start = int(offsets[asset_id])
        stop = int(offsets[asset_id + 1])
        index = rows[start:stop]
        if len(index) == 0:
            continue
        series = source[index, asset_id].astype(np.float64)
        state = np.empty(len(series), dtype=np.float64)
        state[0] = series[0]
        for position in range(1, len(series)):
            state[position] = decay * state[position - 1] + alpha_value * series[position]
        out[index, asset_id] = state.astype(np.float32)
    return out


# ── 比率工具 ──────────────────────────────────────────────
def _ratio(numerator, denominator):
    out = np.full(numerator.shape, np.nan, dtype=np.float32)
    valid = (np.isfinite(numerator) & np.isfinite(denominator)
             & (denominator != 0))
    np.divide(numerator, denominator, out=out, where=valid)
    return out


def _relative(numerator, denominator):
    out = _ratio(numerator, denominator)
    finite = np.isfinite(out)
    out[finite] = out[finite] - np.float32(1.0)
    return out


def _return_over_bars(values, valid_mask, bars):
    previous = valid_shift(values, bars, valid_mask)
    out = np.full(values.shape, np.nan, dtype=np.float32)
    np.divide(values, previous, out=out,
              where=np.isfinite(previous) & (previous != 0))
    finite = np.isfinite(out)
    out[finite] = out[finite] - np.float32(1.0)
    return out


# ── 涨跌停（复刻 app/price_limits.py） ─────────────────────
MAIN_BOARD_ST_LIMIT_CHANGE_DATE = 20260706
GEM_20PCT_LIMIT_START_DATE = 20200824


def board_limit_pct(symbol):
    """板块基础带宽。不含日期、不含 ST。"""
    if symbol.endswith(".BJ"):
        return 0.30
    code = symbol.split(".")[0]
    if code[:3] in ("300", "301", "302") or code[:3] in ("688", "689"):
        return 0.20
    return 0.10


def is_risk_warning_name(name):
    if not name:
        return False
    upper = str(name).upper()
    return ("ST" in upper) or upper.startswith("S")


def _limit_pct_vectors(symbols, names):
    """当期 / 历史 ST / 创业板注册制前 三条带宽向量（与仓库同口径）。"""
    current = np.fromiter(
        (board_limit_pct(str(symbol)) for symbol in symbols),
        dtype=np.float64, count=len(symbols))
    legacy = current.copy()
    for asset_id in range(len(symbols)):
        if (current[asset_id] == 0.10
                and is_risk_warning_name(names[asset_id])):
            legacy[asset_id] = 0.05
    pre_gem = legacy.copy()
    for asset_id in range(len(symbols)):
        symbol = str(symbols[asset_id])
        if symbol.endswith(".BJ"):
            continue
        code = symbol.split(".")[0]
        if code[:3] in ("300", "301", "302"):
            pre_gem[asset_id] = (
                0.05 if is_risk_warning_name(names[asset_id]) else 0.10)
    return pre_gem, legacy, current


def _price_limit_pct_matrix(market, trade_dates):
    """逐（日, 资产）生效带宽矩阵 —— 复刻 ``write_numpy_price_limit_matrix``。"""
    shape = (len(trade_dates), len(market.symbols))
    pre_gem, legacy, current = _limit_pct_vectors(market.symbols, market.names)
    out = np.empty(shape, dtype=np.float32)
    out[:] = current.astype(np.float32, copy=False)
    for time_id, trade_date in enumerate(trade_dates):
        stamp = int(trade_date)
        if stamp < GEM_20PCT_LIMIT_START_DATE:
            out[time_id] = pre_gem.astype(np.float32, copy=False)
        elif stamp < MAIN_BOARD_ST_LIMIT_CHANGE_DATE:
            out[time_id] = legacy.astype(np.float32, copy=False)
    return out


def limit_price(previous, limit_pct, up=True):
    """整数分半进位 —— 与 ``price_limits.numpy_limit_price`` 一致。"""
    sign = 1 if up else -1
    numerator = np.rint((1.0 + sign * limit_pct) * 100.0).astype(np.int64)
    result = np.full(previous.shape, np.nan, dtype=np.float64)
    finite = np.isfinite(previous)
    cents = np.floor(previous[finite] * 100.0 + 0.5).astype(np.int64)
    result[finite] = (
        ((cents * numerator[finite] + 50) // 100).astype(np.float64) / 100.0)
    return result


# ── 特征入口 ──────────────────────────────────────────────
def matrix_feature(market, name):
    """与 ``matrix.matrix_feature`` 同名同语义，覆盖导出策略实际用到的全部算子。"""
    if name in ("open", "high", "low", "close", "volume"):
        return market.field(name)
    if name in market.fields:
        return market.field(name)

    # 缓存挂在市场对象上：矩阵被回收时缓存一起走，不会像模块级 dict 那样泄漏
    cache = getattr(market, "_feature_cache", None)
    if cache is None:
        cache = {}
        market._feature_cache = cache
    if name in cache:
        return cache[name]
    value = _compute_feature(market, name)
    cache[name] = value
    return value


def _compute_feature(market, name):
    close = market.close
    close_valid = np.isfinite(close)

    if name == "prev_close":
        return valid_shift(close, 1, close_valid)
    if name == "change_pct":
        return _return_over_bars(close, close_valid, 1)
    if name == "change_amount":
        previous = valid_shift(close, 1, close_valid)
        out = np.full(market.shape, np.nan, dtype=np.float32)
        np.subtract(close, previous, out=out, where=np.isfinite(previous))
        return out
    if name == "amplitude":
        previous = valid_shift(close, 1, close_valid)
        out = np.full(market.shape, np.nan, dtype=np.float32)
        np.divide(market.high - market.low, previous, out=out,
                  where=np.isfinite(previous) & (previous > 0))
        return out
    if name.startswith("momentum_") and name.endswith("d"):
        bars = int(name[len("momentum_"):-1])
        return _return_over_bars(close, close_valid, bars)
    if name.startswith("ma") and name.endswith("_bias"):
        period = int(name[2:-len("_bias")])
        return _relative(close, valid_rolling_mean(close, close_valid, period))
    if name.startswith("ema") and name.endswith("_bias"):
        period = int(name[3:-len("_bias")])
        return _relative(close, valid_ewm_adjust_false(
            close, close_valid, span=period))
    if name.startswith("ma") and name[2:].isdigit():
        return valid_rolling_mean(close, close_valid, int(name[2:]))
    if name.startswith("ema") and name[3:].isdigit():
        return valid_ewm_adjust_false(close, close_valid, span=int(name[3:]))
    if name in ("macd_dif", "macd_dea", "macd_hist"):
        return _macd(market, name)
    if name in ("vol_ratio_5d", "vol_ratio_10d"):
        return _vol_ratio(market, 5 if name == "vol_ratio_5d" else 10, close_valid)
    if name in ("vol_ma5", "vol_ma10"):
        window = 5 if name == "vol_ma5" else 10
        volume_valid = close_valid & np.isfinite(market.volume)
        return valid_rolling_mean(market.volume, volume_valid, window)
    if name in ("boll_upper", "boll_lower"):
        middle = valid_rolling_mean(close, close_valid, 20)
        deviation = valid_rolling_std(close, close_valid, 20, ddof=1)
        offset = np.float32(2.0) * deviation
        return middle + offset if name == "boll_upper" else middle - offset
    if name == "high_60d":
        return valid_rolling_max(close, close_valid, 60)
    if name == "low_60d":
        return valid_rolling_min(close, close_valid, 60)
    if name == "annual_vol_20d":
        daily = _return_over_bars(close, close_valid, 1)
        return (valid_rolling_std(daily, np.isfinite(daily), 20, ddof=1)
                * np.float32(252 ** 0.5))
    if name == "close_position":
        return _ratio(close - market.low, market.high - market.low)
    if name == "turnover_rate":
        return market.field("turnover_rate")
    if name == "price_limit_pct":
        return market.field("price_limit_pct")
    if name.startswith("rsi_") and name[4:].isdigit():
        return _rsi(market, int(name[4:]), close_valid)
    if name in ("limit_up_count_20d", "limit_up_count_60d"):
        window = 20 if name == "limit_up_count_20d" else 60
        consecutive = market.field("consecutive_limit_ups")
        hits = np.where(np.isfinite(consecutive) & (consecutive > 0),
                        np.float32(1.0), np.float32(0.0)).astype(np.float32)
        return valid_rolling_sum(hits, close_valid, window)
    if name == "consecutive_limit_ups":
        return market.field("consecutive_limit_ups")
    if name in ("kdj_k", "kdj_d", "kdj_j"):
        return _kdj(market, name, close_valid)
    if name in ("atr_14", "atr_pct"):
        return _atr(market, name, close_valid)
    if name == "distance_to_high_60d":
        return _relative(close, matrix_feature(market, "high_60d"))
    if name == "distance_from_low_60d":
        return _relative(close, matrix_feature(market, "low_60d"))
    if name == "gap_return":
        return _relative(market.open, valid_shift(close, 1, close_valid))
    if name == "intraday_return":
        return _relative(close, market.open)
    if name == "vol_trend_5_10":
        return _relative(matrix_feature(market, "vol_ma5"),
                         matrix_feature(market, "vol_ma10"))
    raise ValueError("unsupported matrix feature: %s" % name)


def _macd(market, name):
    close = market.close
    close_valid = np.isfinite(close)
    dif = (valid_ewm_adjust_false(close, close_valid, span=12)
           - valid_ewm_adjust_false(close, close_valid, span=26))
    dif = dif.astype(np.float32, copy=False)
    dea = valid_ewm_adjust_false(dif, np.isfinite(dif), alpha=2.0 / 10.0)
    if name == "macd_dif":
        return dif
    if name == "macd_dea":
        return dea
    return ((dif - dea) * np.float32(2.0)).astype(np.float32, copy=False)


def _vol_ratio(market, window, close_valid):
    volume_valid = close_valid & np.isfinite(market.volume)
    previous_volume = valid_shift(market.volume, 1, volume_valid)
    previous_mean = valid_rolling_mean(
        previous_volume, np.isfinite(previous_volume), window)
    return _ratio(market.volume, previous_mean)


def _rsi(market, window, close_valid):
    close = market.close
    delta = close - valid_shift(close, 1, close_valid)
    gain = np.where(delta > 0, delta, 0.0).astype(np.float32, copy=False)
    loss = np.where(delta < 0, -delta, 0.0).astype(np.float32, copy=False)
    gain[~close_valid] = np.nan
    loss[~close_valid] = np.nan
    average_gain = valid_ewm_adjust_false(gain, close_valid, alpha=1.0 / window)
    average_loss = valid_ewm_adjust_false(loss, close_valid, alpha=1.0 / window)
    denominator = np.where(average_loss == 0, np.float32(1e-12), average_loss)
    out = np.full(market.shape, np.nan, dtype=np.float32)
    np.divide(average_gain, denominator, out=out, where=np.isfinite(denominator))
    out = np.float32(100.0) - np.float32(100.0) / (np.float32(1.0) + out)
    return out


def _kdj(market, name, close_valid):
    low_valid = close_valid & np.isfinite(market.low)
    high_valid = close_valid & np.isfinite(market.high)
    low_9 = valid_rolling_min(market.low, low_valid, 9)
    high_9 = valid_rolling_max(market.high, high_valid, 9)
    rsv = _ratio(market.close - low_9, high_9 - low_9) * np.float32(100.0)
    k = valid_ewm_adjust_false(rsv, np.isfinite(rsv), alpha=1.0 / 3.0)
    if name == "kdj_k":
        return k
    d = valid_ewm_adjust_false(k, np.isfinite(k), alpha=1.0 / 3.0)
    if name == "kdj_d":
        return d
    return (np.float32(3.0) * k - np.float32(2.0) * d).astype(np.float32, copy=False)


def _atr(market, name, close_valid):
    previous = valid_shift(market.close, 1, close_valid)
    true_range = np.fmax.reduce([
        market.high - market.low,
        np.abs(market.high - previous),
        np.abs(market.low - previous),
    ]).astype(np.float32, copy=False)
    true_range[~close_valid] = np.nan
    atr = valid_ewm_adjust_false(true_range, np.isfinite(true_range), alpha=1.0 / 14.0)
    return atr if name == "atr_14" else _ratio(atr, market.close)


# ── 基础过滤与打分（复刻 matrix.build_basic_filter_mask / build_matrix_score） ──
def _optional_field(market, name):
    values = market.fields.get(name)
    if values is None:
        return np.full(market.shape, np.nan, dtype=np.float32)
    return values


def _required_field_for_bound(market, config, name):
    if config.get(name + "_min") is None and config.get(name + "_max") is None:
        return np.full(market.shape, np.nan, dtype=np.float32)
    return market.field(name)


def _apply_bound(mask, values, config, prefix):
    minimum = config.get(prefix + "_min")
    maximum = config.get(prefix + "_max")
    if minimum is not None and np.isfinite(values).any():
        mask &= values >= float(minimum)
    if maximum is not None and np.isfinite(values).any():
        mask &= values <= float(maximum)


def _symbol_in_boards(symbol, boards):
    for board in boards:
        if board == "沪主板" and symbol.startswith("60"):
            return True
        if board == "深主板" and symbol.startswith(("00", "001")):
            return True
        if board == "创业板" and symbol.startswith(("300", "301", "302")):
            return True
        if board == "科创板" and symbol.startswith(("688", "689")):
            return True
        if board == "北交所" and symbol.endswith(".BJ"):
            return True
    return False


def build_basic_filter_mask(market, config):
    if not config or not config.get("enabled", True):
        return np.ones(market.shape, dtype=bool)
    mask = np.ones(market.shape, dtype=bool)
    close = market.close
    if config.get("price_min") is not None:
        mask &= close >= float(config["price_min"])
    if config.get("price_max") is not None:
        mask &= close <= float(config["price_max"])
    _apply_bound(mask, close * _optional_field(market, "total_shares"), config, "market_cap")
    _apply_bound(mask, close * _optional_field(market, "float_shares"), config, "float_cap")
    _apply_bound(mask, _required_field_for_bound(market, config, "amount"), config, "amount")
    _apply_bound(mask, _optional_field(market, "turnover_rate"), config, "turnover")
    if config.get("exclude_st"):
        asset_mask = np.array(
            [not any(token in name.upper() for token in ("ST", "*ST", "退"))
             for name in market.names],
            dtype=bool,
        )
        mask &= asset_mask[None, :]
    boards = config.get("boards")
    if isinstance(boards, list) and boards:
        board_mask = np.zeros(len(market.symbols), dtype=bool)
        for asset_id in range(len(market.symbols)):
            board_mask[asset_id] = _symbol_in_boards(market.symbols[asset_id], boards)
        mask &= board_mask[None, :]
    return mask


SCORING_DIRECTION_LOW = "low"


def build_matrix_score(market, universe, scoring, order_by, descending,
                       fallback=None, directions=None):
    """与 ``matrix.build_matrix_score`` 同口径（含 ``order_by != score`` 分支）。"""
    weights = dict((name, float(weight)) for name, weight in scoring.items()
                   if float(weight) != 0.0)
    total_weight = sum(weights.values())
    if weights and total_weight > 0:
        score = np.zeros(market.shape, dtype=np.float32)
        all_finite = universe.copy()
        for name in weights:
            values = matrix_feature(market, name)
            finite = np.isfinite(values) & universe
            all_finite &= np.isfinite(values)
            row_min = np.min(np.where(finite, values, np.inf), axis=1)
            row_max = np.max(np.where(finite, values, -np.inf), axis=1)
            row_range = row_max - row_min
            varying_rows = np.isfinite(row_range) & (row_range > 0)
            normalized_weight = np.float32(weights[name] / total_weight)
            scratch = np.zeros(values.shape, dtype=np.float32)
            varying_mask = finite & varying_rows[:, None]
            np.divide(values - row_min[:, None], row_range[:, None],
                      out=scratch, where=varying_mask)
            scratch[finite & ~varying_rows[:, None]] = np.float32(0.5)
            if (directions or {}).get(name) == SCORING_DIRECTION_LOW:
                scratch[finite] = np.float32(1.0) - scratch[finite]
            scratch *= normalized_weight
            score += scratch
        score *= np.float32(100.0)
        score[~universe | ~all_finite] = 0.0
        return score

    if order_by and order_by != "score":
        values = matrix_feature(market, order_by)
        result = np.zeros(market.shape, dtype=np.float32)
        direction = np.float32(1.0 if descending else -1.0)
        valid = universe & np.isfinite(values)
        np.multiply(values, direction, out=result, where=valid)
        return result

    if fallback is None:
        return np.zeros(market.shape, dtype=np.float32)
    return fallback.copy()


# ── 兼容别名（策略里有 ``import ... as shift`` 这类写法） ──
shift = valid_shift
ewm_adjust_false = valid_ewm_adjust_false
