"""股本回填: 维表同步跑在日K之前 → 股本滞后一轮, 选股一只不出。

回归背景: 管道 Step 0 先同步维表, 当时当日日K/实时快照还没把 turnover_rate
写进 enriched → 无从反推股本 → 新部署的维表股本全空。市值/换手率条件遇空值
一律判 false, 选股结果为 0。本地部署则靠旧维表残留的股本侥幸能选出 211 只。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from app.services import instrument_sync as isync


def _write_instruments(data_dir: Path, symbols: list[str]) -> None:
    out = data_dir / "instruments"
    out.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": symbols,
        "name": symbols,
        "code": [s.split(".")[0] for s in symbols],
        "exchange": [s.split(".")[1] for s in symbols],
        "region": ["CN"] * len(symbols),
        "type": ["stock"] * len(symbols),
        "total_shares": [None] * len(symbols),
        "float_shares": [None] * len(symbols),
        "as_of": ["2026-11-13"] * len(symbols),
    }, schema={
        "symbol": pl.String, "name": pl.String, "code": pl.String,
        "exchange": pl.String, "region": pl.String, "type": pl.String,
        "total_shares": pl.Float64, "float_shares": pl.Float64, "as_of": pl.Date,
    }).write_parquet(out / "instruments.parquet")

def _write_enriched(data_dir: Path, rows: dict[str, tuple[float, float]]) -> None:
    """写当日 enriched 分区: symbol → (volume 手, turnover_rate %)。"""
    part = data_dir / "kline_daily_enriched" / "date=2026-11-13"
    part.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": list(rows),
        "volume": [v for v, _ in rows.values()],
        "turnover_rate": [t for _, t in rows.values()],
    }).write_parquet(part / "part.parquet")

def test_backfill_shares_fills_from_enriched_turnover(tmp_path) -> None:
    _write_instruments(tmp_path, ["600000.SH", "000001.SZ"])
    # float_shares = volume(手) * 10000 / turnover_rate(%)
    _write_enriched(tmp_path, {"600000.SH": (1000.0, 2.0), "000001.SZ": (500.0, 1.0)})

    assert isync.backfill_shares(tmp_path) == 2

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    by_symbol = {r["symbol"]: r for r in df.to_dicts()}
    assert by_symbol["600000.SH"]["float_shares"] == 5_000_000.0
    assert by_symbol["000001.SZ"]["float_shares"] == 5_000_000.0
    # total_shares 同步补齐: 策略的市值条件用 close * total_shares
    assert by_symbol["600000.SH"]["total_shares"] == 5_000_000.0

def test_backfill_shares_keeps_instrument_as_of(tmp_path) -> None:
    """as_of 是「维表同步日」, 决定涨跌停价对哪一天算权威 —— 回填不能改它。"""
    _write_instruments(tmp_path, ["600000.SH"])
    _write_enriched(tmp_path, {"600000.SH": (1000.0, 2.0)})

    assert isync.backfill_shares(tmp_path) == 1

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert df["as_of"].to_list() == [date(2026, 11, 13)]


def test_backfill_shares_is_noop_when_already_filled(tmp_path) -> None:
    _write_instruments(tmp_path, ["600000.SH"])
    _write_enriched(tmp_path, {"600000.SH": (1000.0, 2.0)})
    assert isync.backfill_shares(tmp_path) == 1

    # 第二次: 无新增 → 不写盘, 返回 0
    assert isync.backfill_shares(tmp_path) == 0

def test_backfill_shares_without_enriched_keeps_null(tmp_path) -> None:
    """没有带换手率的 enriched 分区 → 保持 null, 不伪造股本。"""
    _write_instruments(tmp_path, ["600000.SH"])

    assert isync.backfill_shares(tmp_path) == 0
    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert df["float_shares"].null_count() == 1
