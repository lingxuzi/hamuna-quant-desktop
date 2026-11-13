"""标的维表同步: provider 不提供股本时不覆盖已有值。

回归背景: 部分行情源的 get_instruments 只返回代码/名称/市场/类型, 没有
股本与交易规则字段。旧实现整表覆盖后 float_shares/total_shares 变 null,
enriched 的 turnover_rate 随之全空, 依赖换手率与流通市值的策略 (起爆/5连阳)
直接筛出 0 只 —— 同一个策略换数据源结果就不同。
"""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from app.services import instrument_sync as isync


def _write_existing(data_dir: Path) -> None:
    out = data_dir / "instruments"
    out.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ"],
        "name": ["浦发银行", "平安银行"],
        "code": ["600000", "000001"],
        "exchange": ["SH", "SZ"],
        "region": ["CN", "CN"],
        "type": ["stock", "stock"],
        "listing_date": ["1999-11-10", "1991-04-03"],
        "total_shares": [2.9e10, 1.9e10],
        "float_shares": [2.9e10, 1.9e10],
        "tick_size": [0.01, 0.01],
        "limit_up": [11.0, 12.0],
        "limit_down": [9.0, 10.0],
    }).write_parquet(out / "instruments.parquet")


def _provider_rows(*symbols: str) -> list[dict]:
    """模拟只提供标识字段的行情源 (无股本/交易规则)。"""
    return [
        {
            "symbol": s, "name": s, "code": s.split(".")[0],
            "exchange": s.split(".")[1], "region": "CN", "type": "stock",
            "listing_date": None, "total_shares": None, "float_shares": None,
            "tick_size": None, "limit_up": None, "limit_down": None,
        }
        for s in symbols
    ]


def test_share_fields_restored_from_existing_table(tmp_path, monkeypatch) -> None:
    _write_existing(tmp_path)
    monkeypatch.setattr(
        isync, "fetch_instruments_via_provider",
        lambda *_: _provider_rows("600000.SH", "000001.SZ"),
    )

    assert isync.sync_instruments(tmp_path) == 2

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    by_symbol = {r["symbol"]: r for r in df.to_dicts()}
    assert by_symbol["600000.SH"]["float_shares"] == pytest.approx(2.9e10)
    assert by_symbol["600000.SH"]["total_shares"] == pytest.approx(2.9e10)
    assert by_symbol["600000.SH"]["tick_size"] == pytest.approx(0.01)
    assert by_symbol["000001.SZ"]["float_shares"] == pytest.approx(1.9e10)


def test_new_symbol_keeps_null_shares_instead_of_faking(tmp_path, monkeypatch) -> None:
    """新上市标的没有历史值 → 保持 null, 不伪造股本。"""
    _write_existing(tmp_path)
    monkeypatch.setattr(
        isync, "fetch_instruments_via_provider",
        lambda *_: _provider_rows("600000.SH", "301999.SZ"),
    )

    isync.sync_instruments(tmp_path)

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    new = df.filter(pl.col("symbol") == "301999.SZ")
    assert new.height == 1
    assert new["float_shares"][0] is None
    assert new["total_shares"][0] is None


def test_provider_fields_win_when_present(tmp_path, monkeypatch) -> None:
    """provider 自带股本时以 provider 为准 (不复用旧值)。"""
    _write_existing(tmp_path)
    rows = _provider_rows("600000.SH")
    rows[0]["float_shares"] = 1.0e9
    rows[0]["total_shares"] = 2.0e9
    monkeypatch.setattr(isync, "fetch_instruments_via_provider", lambda *_: rows)

    isync.sync_instruments(tmp_path)

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert df["float_shares"][0] == pytest.approx(1.0e9)
    assert df["total_shares"][0] == pytest.approx(2.0e9)


def test_no_existing_table_is_tolerated(tmp_path, monkeypatch) -> None:
    """首次同步且无历史维表: 不报错, 字段保持 null。"""
    monkeypatch.setattr(
        isync, "fetch_instruments_via_provider",
        lambda *_: _provider_rows("600000.SH"),
    )

    assert isync.sync_instruments(tmp_path) == 1

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert df["float_shares"][0] is None


def test_partial_provider_fields_filled_per_row(tmp_path, monkeypatch) -> None:
    """provider 只给部分标的股本时, 也要逐行补全 (不能整列跳过)。"""
    _write_existing(tmp_path)
    rows = _provider_rows("600000.SH", "000001.SZ")
    rows[0]["float_shares"] = 5.0e8  # 第一只有值, 第二只仍为空
    rows[0]["total_shares"] = 6.0e8
    monkeypatch.setattr(isync, "fetch_instruments_via_provider", lambda *_: rows)

    isync.sync_instruments(tmp_path)

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    by_symbol = {r["symbol"]: r for r in df.to_dicts()}
    assert by_symbol["600000.SH"]["float_shares"] == pytest.approx(5.0e8)
    assert by_symbol["000001.SZ"]["float_shares"] == pytest.approx(1.9e10)


def test_daily_price_limits_are_not_inherited_from_old_metadata(tmp_path, monkeypatch):
    _write_existing(tmp_path)
    monkeypatch.setattr(isync, "fetch_instruments_via_provider", lambda *_: _provider_rows("600000.SH"))

    isync.sync_instruments(tmp_path)
    row = pl.read_parquet(tmp_path / "instruments/instruments.parquet").row(0, named=True)
    assert row["float_shares"] == pytest.approx(2.9e10)
    assert row["limit_up"] is None
    assert row["limit_down"] is None


def test_provider_metadata_supplies_price_limits(tmp_path, monkeypatch):
    """provider 维表 ext 的涨跌停/股本字段直接落库 (无档位区分)。"""
    item = {"symbol": "002172.SZ", "name": "澳洋健康", "ext": {
        "limit_up": 6.50, "limit_down": 5.32, "float_shares": 765140730.0,
    }}
    monkeypatch.setattr(isync, "fetch_instruments_via_provider",
                        lambda *_: isync._flatten_instruments([item]))
    isync.sync_instruments(tmp_path)
    row = pl.read_parquet(tmp_path / "instruments/instruments.parquet").row(0, named=True)
    assert row["limit_up"] == 6.50
    assert row["limit_down"] == 5.32
    assert row["float_shares"] == 765140730.0

def test_empty_daily_dir_does_not_break_instrument_sync(tmp_path, monkeypatch):
    """全新部署: kline_daily 目录存在但无分区, 维表同步不得抛错 (DuckDB 空匹配 IOException)。"""
    (tmp_path / "kline_daily").mkdir()
    item = {"symbol": "600519.SH", "name": "贵州茅台", "ext": {}}
    monkeypatch.setattr(isync, "fetch_instruments_via_provider",
                        lambda *_: isync._flatten_instruments([item]))
    assert isync._local_first_trade_dates(tmp_path) == {}
    assert isync.sync_instruments(tmp_path) == 1
    assert (tmp_path / "instruments/instruments.parquet").exists()

def test_float_shares_derived_from_enriched_turnover(tmp_path, monkeypatch):
    """无任何股本源时, 用当日 enriched 的 volume/turnover 反推 float_shares。

    换手率 = volume(手) * 10000 / float_shares → float_shares = volume * 10000 / turnover。
    """
    part = tmp_path / "kline_daily_enriched/date=2026-11-13/part.parquet"
    part.parent.mkdir(parents=True)
    pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ"],
        "volume": [1000.0, 2000.0],
        "turnover_rate": [0.5, None],
    }).write_parquet(part)
    item = {"symbol": "600000.SH", "name": "浦发银行", "ext": {}}
    monkeypatch.setattr(isync, "fetch_instruments_via_provider",
                        lambda *_: isync._flatten_instruments([item]))
    isync.sync_instruments(tmp_path)
    row = pl.read_parquet(tmp_path / "instruments/instruments.parquet").row(0, named=True)
    assert row["float_shares"] == pytest.approx(2.0e7)

def test_derived_float_shares_never_overwrites_provider_value(tmp_path, monkeypatch):
    """provider 已有股本的标的保持原值, 反推只补空缺。"""
    part = tmp_path / "kline_daily_enriched/date=2026-11-13/part.parquet"
    part.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600000.SH"], "volume": [1000.0],
                  "turnover_rate": [0.5]}).write_parquet(part)
    item = {"symbol": "600000.SH", "name": "浦发银行",
            "ext": {"float_shares": 5.0e8}}
    monkeypatch.setattr(isync, "fetch_instruments_via_provider",
                        lambda *_: isync._flatten_instruments([item]))
    isync.sync_instruments(tmp_path)
    row = pl.read_parquet(tmp_path / "instruments/instruments.parquet").row(0, named=True)
    assert row["float_shares"] == 5.0e8

def test_derived_float_shares_also_fills_total_shares(tmp_path, monkeypatch):
    """反推值同时补 total_shares: 市值条件用 close * total_shares, 只补流通股本不够。"""
    part = tmp_path / "kline_daily_enriched/date=2026-11-13/part.parquet"
    part.parent.mkdir(parents=True)
    pl.DataFrame({"symbol": ["600000.SH"], "volume": [1000.0],
                  "turnover_rate": [0.5]}).write_parquet(part)
    item = {"symbol": "600000.SH", "name": "浦发银行", "ext": {}}
    monkeypatch.setattr(isync, "fetch_instruments_via_provider",
                        lambda *_: isync._flatten_instruments([item]))
    isync.sync_instruments(tmp_path)
    row = pl.read_parquet(tmp_path / "instruments/instruments.parquet").row(0, named=True)
    assert row["float_shares"] == pytest.approx(2.0e7)
    assert row["total_shares"] == pytest.approx(2.0e7)
