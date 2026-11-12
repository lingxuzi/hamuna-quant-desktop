"""回归: enriched 分区必须每 (symbol, date) 只有一行。

背景 (2026-11-12): 日K分区里一天落了多份 parquet 碎片 (历史 part-stream-*
残留) → 输入自带重复 (symbol, date) → 管道 1:1 抄进 enriched 分区 →
回测矩阵构建报 "MarketDataMatrix requires unique date/symbol rows",
启动预热整段失败。合并时统一去重, 污染不再原样写回。
"""
from __future__ import annotations

from datetime import date

import polars as pl

from app.indicators import pipeline

DAY = date(2026, 7, 15)


def _daily_rows(dup: bool) -> pl.DataFrame:
    rows = {
        "symbol": ["600000.SH"],
        "date": [DAY],
        "open": [10.0],
        "high": [10.0],
        "low": [10.0],
        "close": [10.0],
        "volume": [100.0],
        "amount": [1000.0],
        "quote_ts": [0],
    }
    if dup:
        rows = {k: v * 2 for k, v in rows.items()}
    return pl.DataFrame(rows)


def _write_daily(data_dir, dup: bool) -> None:
    out = data_dir / "kline_daily" / f"date={DAY.isoformat()}" / "part.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    _daily_rows(dup).write_parquet(out)


def _write_enriched(data_dir, dup: bool) -> None:
    out = data_dir / "kline_daily_enriched" / f"date={DAY.isoformat()}" / "part.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    _daily_rows(dup).select(["symbol", "date", "close"]).write_parquet(out)


def _fake_compute_enriched(raw: pl.DataFrame, **_kwargs) -> pl.DataFrame:
    return raw.with_columns(
        pl.col("close").alias("raw_close"),
        pl.col("high").alias("raw_high"),
        pl.col("low").alias("raw_low"),
        pl.lit(None, dtype=pl.Float64).alias("turnover_rate"),
        pl.lit(0, dtype=pl.UInt32).alias("consecutive_limit_ups"),
        pl.lit(0, dtype=pl.UInt32).alias("consecutive_limit_downs"),
    )


def _enriched_rows(data_dir) -> pl.DataFrame:
    return pl.read_parquet(
        data_dir / "kline_daily_enriched" / f"date={DAY.isoformat()}" / "part.parquet"
    )


def test_full_rebuild_drops_duplicate_symbol_date(tmp_path, monkeypatch):
    """全量重建: 日K 里的重复行不得原样写进 enriched 分区。"""
    _write_daily(tmp_path, dup=True)
    monkeypatch.setattr(pipeline, "compute_enriched", _fake_compute_enriched)

    pipeline.run_pipeline(data_dir=tmp_path)

    df = _enriched_rows(tmp_path)
    assert df.height == 1, f"enriched 分区应去重, 实际 {df.height} 行"


def test_incremental_recompute_drops_duplicate_symbol_date(tmp_path, monkeypatch):
    """局部重算 (除权因子变更): 合并已有分区时同样去重。"""
    _write_daily(tmp_path, dup=True)
    _write_enriched(tmp_path, dup=True)
    monkeypatch.setattr(pipeline, "compute_enriched", _fake_compute_enriched)

    pipeline.run_pipeline(
        data_dir=tmp_path, symbols=["600000.SH"], new_dates_only=True,
    )

    df = _enriched_rows(tmp_path)
    assert df.height == 1, f"enriched 分区应去重, 实际 {df.height} 行"


def test_merge_keeps_incoming_value_and_drops_listed_symbols():
    """合并语义: incoming 覆盖旧值; drop_symbols 的旧行先剔除。"""
    existing = pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ"],
        "date": [DAY, DAY],
        "close": [1.0, 2.0],
    })
    incoming = pl.DataFrame({
        "symbol": ["600000.SH"],
        "date": [DAY],
        "close": [9.0],
    })

    merged = pipeline._merge_enriched_rows(
        existing, incoming, drop_symbols=["000001.SZ"],
    )

    assert merged.height == 1
    assert merged["close"].to_list() == [9.0]
