"""_resolve_universe 维表单一来源 + 指数过滤测试。

标的池 = 自选 + 本地 instruments 维表 (无第三方标的池兜底); 传入 repo 时
过滤自选里的指数 symbol (指数日K走独立存储, 进股票池会污染 kline_daily/kline_minute)。
"""
import polars as pl
import pytest

from app.jobs import daily_pipeline
from app.data_providers.repository import DataStore, KlineRepository


@pytest.fixture()
def repo(tmp_path):
    return KlineRepository(DataStore(tmp_path))


def _write_instruments(tmp_path, symbols):
    out = tmp_path / "instruments" / "instruments.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"symbol": symbols}).write_parquet(out)


def _watchlist(monkeypatch, symbols):
    """打桩自选清单 (自选是标的池的两个来源之一)。"""
    monkeypatch.setattr(daily_pipeline.watchlist, "symbols", lambda: list(symbols))


def test_resolve_universe_excludes_index_symbols(repo, monkeypatch, tmp_path):
    """自选里的指数不进入股票日K/分钟K同步池。"""
    _write_instruments(tmp_path, ["600000.SH"])
    _watchlist(monkeypatch, ["600000.SH", "000001.SH"])
    monkeypatch.setattr(daily_pipeline.settings, "data_dir", tmp_path)
    monkeypatch.setattr(repo, "get_index_symbol_set", lambda: {"000001.SH"})

    universe = daily_pipeline._resolve_universe(object(), repo)
    assert "600000.SH" in universe
    assert "000001.SH" not in universe


def test_resolve_universe_uses_watchlist_and_instruments_only(repo, monkeypatch, tmp_path):
    """维表单一来源: 标的池 = 自选 + instruments 维表, 不引入任何第三方标的池。"""
    _write_instruments(tmp_path, ["000001.SZ"])
    _watchlist(monkeypatch, ["600000.SH"])
    monkeypatch.setattr(daily_pipeline.settings, "data_dir", tmp_path)
    monkeypatch.setattr(repo, "get_index_symbol_set", lambda: set())

    universe = daily_pipeline._resolve_universe(object(), repo)
    assert universe == ["000001.SZ", "600000.SH"]


def test_resolve_universe_watchlist_only_when_no_table(repo, monkeypatch, tmp_path):
    """维表缺失时不伪造标的: 池=自选, 宁缺毋假。"""
    _watchlist(monkeypatch, ["600000.SH"])
    monkeypatch.setattr(daily_pipeline.settings, "data_dir", tmp_path)
    monkeypatch.setattr(repo, "get_index_symbol_set", lambda: set())

    universe = daily_pipeline._resolve_universe(object(), repo)
    assert universe == ["600000.SH"]
