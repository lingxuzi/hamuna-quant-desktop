"""#302: 裸符号日K同步不再静默 0 行。

上游对无交易所后缀的符号返回 200 空 payload, 本地写 0 行且流程"成功"。
修复后: 裸符号按 instruments 维表补全后缀, 无法识别的显式跳过并告警;
批量同步结束统计 0 行标的 (zero_row_out / API zero_row_symbols)。
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from app.data_providers.repository import DataStore, KlineRepository
from app.services import kline_sync


@pytest.fixture
def no_live(monkeypatch):
    """默认: 全市场代码表不可用 (源不通) → 裸符号退回代码前缀推断。"""
    monkeypatch.setattr(kline_sync, "_live_symbol_index", lambda codes: None)


def _write_instruments(data_dir, symbols: list[str]) -> None:
    out = data_dir / "instruments" / "instruments.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": symbols}).write_parquet(out)


def _daily(symbol: str, day: date) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol], "date": [day], "open": [10.0], "high": [11.0],
        "low": [9.0], "close": [10.5], "volume": [100.0], "amount": [1050.0],
    })


class _Provider:
    """自定义源: 只返回 000001.SZ 的数据 (600000 上游 200 空 payload 场景)。"""

    def __init__(self, chunks):
        self._chunks = chunks
        self.requested_symbols: list[str] | None = None

    def iter_daily(self, symbols, *args, **kwargs):
        self.requested_symbols = list(symbols)
        yield from self._chunks


def _route_provider(monkeypatch, provider):
    """把日K生效源路由到假 provider。

    新契约: 日K 只走 provider 链 (生效源 → 内置默认源), 没有 TickFlow 档位;
    生效源名由 preferences.get_data_provider("daily") 决定。
    """
    from app.data_providers import custom
    monkeypatch.setattr(kline_sync.preferences, "get_data_provider", lambda dataset: "fuyao")
    monkeypatch.setattr(
        custom, "resolve_provider",
        lambda name, dataset: (provider, name) if dataset == "daily" else (None, name),
    )


def test_normalize_maps_bare_via_instruments_and_dedups(tmp_path, no_live):
    _write_instruments(tmp_path, ["600000.SH", "000001.SZ"])

    out, resolved, skipped = kline_sync._normalize_bare_symbols(
        ["600000", "000001.SZ", "600000.SH"], tmp_path
    )

    assert out == ["600000.SH", "000001.SZ"]
    assert resolved == {"600000": "600000.SH"}
    assert skipped == []


def test_normalize_without_instruments_infers_suffix_by_code_prefix(tmp_path, caplog, no_live):
    """维表缺失 + 代码表不可用时按代码前缀推断, 不能把整批裸符号跳过。"""
    out, resolved, skipped = kline_sync._normalize_bare_symbols(["600000", "000001.SZ"], tmp_path)

    assert out == ["600000.SH", "000001.SZ"]
    assert resolved == {"600000": "600000.SH"}
    assert skipped == []
    assert any("维表不可用" in r.message for r in caplog.records)


def test_normalize_prefers_live_code_table_over_prefix(tmp_path, monkeypatch):
    """全市场代码表是权威口径: 000001 归上证, 不按前缀猜成深市平安银行。"""
    monkeypatch.setattr(
        kline_sync, "_live_symbol_index",
        lambda codes: {"000001": "000001.SH", "600000": "600000.SH"},
    )

    out, resolved, skipped = kline_sync._normalize_bare_symbols(["000001", "600000"], tmp_path)

    assert out == ["000001.SH", "600000.SH"]
    assert resolved == {"000001": "000001.SH", "600000": "600000.SH"}
    assert skipped == []


def test_normalize_skips_codes_absent_from_live_table(tmp_path, monkeypatch, caplog):
    """代码表里没有的代码 → 显式跳过, 不用前缀规则编一个后缀。"""
    monkeypatch.setattr(kline_sync, "_live_symbol_index", lambda codes: {})

    out, resolved, skipped = kline_sync._normalize_bare_symbols(["830001", "600000"], tmp_path)

    assert out == []
    assert resolved == {}
    assert skipped == ["830001", "600000"]
    assert any("不在全市场代码表内" in r.message for r in caplog.records)


def test_normalize_disambiguates_by_code_prefix(tmp_path, no_live):
    """同代码多交易所(000001.SZ 平安 / 000001.SH 上证)按前缀消歧。"""
    _write_instruments(tmp_path, ["000001.SZ", "000001.SH", "600000.SH"])

    out, resolved, skipped = kline_sync._normalize_bare_symbols(["000001", "600000"], tmp_path)

    assert out == ["000001.SZ", "600000.SH"]
    assert resolved == {"000001": "000001.SZ", "600000": "600000.SH"}
    assert skipped == []


def test_normalize_infers_bj_and_shenzhen_codes(tmp_path, no_live):
    """北交所/深市/ETF 裸代码按前缀补出正确后缀。"""
    out, _resolved, skipped = kline_sync._normalize_bare_symbols(
        ["830001", "300750", "159558", "510300"], tmp_path,
    )

    assert out == ["830001.BJ", "300750.SZ", "159558.SZ", "510300.SH"]
    assert skipped == []


def test_normalize_skips_undecidable_bare(tmp_path, no_live):
    """判不出交易所的代码仍显式跳过, 不带着已知无效的符号发请求。"""
    out, _resolved, skipped = kline_sync._normalize_bare_symbols(["ABCDEF", "600000"], tmp_path)

    assert out == ["600000.SH"]
    assert skipped == ["ABCDEF"]


def test_sync_reports_zero_rows_in_caller_spelling(monkeypatch, tmp_path, caplog, no_live):
    """裸符号无维表可补 → 跳过; 带后缀但上游空 payload → 0 行; 均按原始写法回报。"""
    caplog.set_level(logging.WARNING, logger="app.services.kline_sync")
    repo = KlineRepository(DataStore(tmp_path))
    provider = _Provider([_daily("000001.SZ", date(2026, 9, 10))])
    _route_provider(monkeypatch, provider)
    zero: list[str] = []

    written = kline_sync.sync_and_persist_daily_batch(
        ["600000", "000001.SZ", "999999.SZ"], repo, object(),
        start_date=datetime(2026, 9, 1), end_date=datetime(2026, 9, 10),
        zero_row_out=zero,
    )

    assert written == 1
    assert zero == ["600000", "999999.SZ"]  # 0 行标的 (按调用方原始写法)
    assert any("入库 0 行" in r.message for r in caplog.records)


def test_sync_daily_path_normalizes_and_reports_zero(monkeypatch, tmp_path, no_live):
    _write_instruments(tmp_path, ["600000.SH"])
    repo = KlineRepository(DataStore(tmp_path))
    provider = _Provider([])   # 上游 200 空 payload → 全部 0 行
    _route_provider(monkeypatch, provider)
    capset = SimpleNamespace(has=lambda cap: True, limits=lambda cap: None)
    zero: list[str] = []

    written = kline_sync.sync_and_persist_daily_batch(
        ["600000", "000001.SH"], repo, capset,
        start_date=datetime(2026, 9, 1), end_date=datetime(2026, 9, 10),
        zero_row_out=zero,
    )

    assert written == 0
    # 发往上游前已完成规范化: 600000 → 600000.SH
    assert provider.requested_symbols == ["600000.SH", "000001.SH"]
    # 回报用调用方原始写法
    assert zero == ["600000", "000001.SH"]


def test_api_sync_batch_returns_zero_row_symbols(monkeypatch, tmp_path, no_live):
    from app.api import kline as kline_api

    _write_instruments(tmp_path, ["600000.SH"])
    repo = KlineRepository(DataStore(tmp_path))
    _route_provider(monkeypatch, _Provider([]))   # 上游空 payload → 0 行
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        repo=repo, capabilities=SimpleNamespace(has=lambda cap: True, limits=lambda cap: None),
    )))

    resp = kline_api.sync_batch(request, symbols=["600000", "000001.SH"], days=250)

    assert resp["rows_written"] == 0
    assert resp["zero_row_symbols"] == ["600000", "000001.SH"]
