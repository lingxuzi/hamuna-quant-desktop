"""全量分钟取数边界的容错契约 (minute_refresh 服务的取数边界层)。

全量分钟统一走自定义源: 修复轮 provider.get_intraday_batch(symbols),
稳态增量轮 provider.get_intraday_latest(count) (可选, 未实现自动降级为
仅修复轮)。取数边界 kline_sync.fetch_intraday_custom_batch /
fetch_intraday_custom_latest 把源侧故障收敛成「空轮」:
不抛异常打断服务、不伪造数据、不跨源回退。全程用假 provider, 不发真实请求。
"""
from __future__ import annotations

from datetime import datetime

import polars as pl

from app.services import kline_sync


def _frame(symbols: list[str], *, at: datetime | None = None) -> pl.DataFrame:
    """当日分钟K帧 (北京墙钟 naive)。at 给定非交易时刻 → 脏时间帧。"""
    ts = at or datetime(2026, 8, 28, 9, 31)
    n = len(symbols)
    return pl.DataFrame({
        "symbol": symbols,
        "datetime": [ts] * n,
        "open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n, "close": [1.0] * n,
        "volume": [100.0] * n, "amount": [100.0] * n,
    })


class _FakeProvider:
    """假全量分钟源: 批量/增量端点可注入异常或部分数据。"""

    def __init__(self, *, batch_frame=None, batch_error=None,
                 latest_frame=None, latest_error=None):
        self.batch_frame = batch_frame
        self.batch_error = batch_error
        self.latest_frame = latest_frame
        self.latest_error = latest_error
        self.batch_calls: list[list[str]] = []
        self.latest_calls: list[int] = []

    def get_intraday_batch(self, symbols, count=300, asset_type="stock"):
        self.batch_calls.append(list(symbols))
        if self.batch_error is not None:
            raise self.batch_error
        return self.batch_frame

    def get_intraday_latest(self, symbols=None, count=3):
        self.latest_calls.append(count)
        if self.latest_error is not None:
            raise self.latest_error
        return self.latest_frame


class _MinuteOnlyProvider:
    """只实现 get_minute 的源: 无批量/增量端点 (回退路径 + 降级路径)。"""

    def __init__(self, *, frame=None, chunks=1):
        self.frame = frame
        self.chunks = chunks
        self.minute_calls: list[dict] = []

    def get_minute(self, symbols, *, start_time, end_time, asset_type, freq,
                   on_chunk_done=None):
        self.minute_calls.append({
            "symbols": list(symbols), "start": start_time, "end": end_time,
            "asset_type": asset_type, "freq": freq,
        })
        if on_chunk_done is not None:
            on_chunk_done(self.chunks, self.chunks)
        return self.frame


def test_batch_success_returns_frame_and_one_request():
    symbols = ["600000.SH", "000001.SZ"]
    provider = _FakeProvider(batch_frame=_frame(symbols))

    df, requests = kline_sync.fetch_intraday_custom_batch(provider, "mock_src", symbols)

    assert df["symbol"].to_list() == symbols
    assert requests == 1
    assert provider.batch_calls == [symbols]


def test_batch_source_failure_isolated_to_empty_round():
    """源侧异常 → 整轮按空轮处理 (不抛异常打断服务, 不伪造数据)。"""
    symbols = ["600000.SH", "000001.SZ"]
    provider = _FakeProvider(batch_error=RuntimeError("permanent failure"))

    df, requests = kline_sync.fetch_intraday_custom_batch(provider, "mock_src", symbols)

    assert df.is_empty()
    assert requests == 0


def test_batch_partial_result_is_kept_as_is():
    """源只回一部分标的 → 成功的行照常返回供落盘 (缺失的不补造)。"""
    provider = _FakeProvider(batch_frame=_frame(["600000.SH"]))

    df, requests = kline_sync.fetch_intraday_custom_batch(
        provider, "mock_src", ["600000.SH", "000001.SZ"],
    )

    assert df["symbol"].to_list() == ["600000.SH"]
    assert requests == 1


def test_batch_dirty_datetime_is_rejected():
    """非交易时刻的脏时间 → 契约守卫拦下, 该轮按空轮处理。"""
    provider = _FakeProvider(
        batch_frame=_frame(["600000.SH"], at=datetime(2026, 8, 28, 4, 0)),
    )

    df, requests = kline_sync.fetch_intraday_custom_batch(
        provider, "mock_src", ["600000.SH"],
    )

    assert df.is_empty()
    assert requests == 0


def test_batch_falls_back_to_get_minute_when_source_has_no_batch_endpoint():
    """源未实现 get_intraday_batch → 回退 get_minute 当日窗口 (请求数按分块统计)。"""
    provider = _MinuteOnlyProvider(frame=_frame(["600000.SH"]), chunks=3)

    df, requests = kline_sync.fetch_intraday_custom_batch(
        provider, "mock_src", ["600000.SH"],
    )

    assert df["symbol"].to_list() == ["600000.SH"]
    assert requests == 3
    call = provider.minute_calls[0]
    assert call["symbols"] == ["600000.SH"]
    assert call["asset_type"] == "stock"
    assert call["freq"] == "1m"
    assert call["start"].hour == 0 and call["start"].minute == 0
    assert call["end"] > call["start"]


def test_latest_absent_degrades_to_repair_only_round():
    """源未实现 get_intraday_latest → 返回 None (服务降级为仅修复轮, 不报错)。"""
    provider = _MinuteOnlyProvider(frame=_frame(["600000.SH"]))

    assert kline_sync.fetch_intraday_custom_latest(provider, "mock_src") is None


def test_latest_success_returns_frame_and_one_request():
    provider = _FakeProvider(latest_frame=_frame(["600000.SH"]))

    df, requests = kline_sync.fetch_intraday_custom_latest(provider, "mock_src", count=3)

    assert df["symbol"].to_list() == ["600000.SH"]
    assert requests == 1
    assert provider.latest_calls == [3]


def test_latest_failure_isolated_to_empty_round():
    provider = _FakeProvider(latest_error=RuntimeError("upstream unavailable"))

    df, requests = kline_sync.fetch_intraday_custom_latest(provider, "mock_src")

    assert df.is_empty()
    assert requests == 0
