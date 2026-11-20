"""Polars parquet helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl


def has_date_partitions(dataset_dir: Path | str) -> bool:
    """数据集目录里是否有 date=* 分区 (读端只认分区)。"""
    try:
        return any(
            child.is_dir() and child.name.startswith("date=")
            for child in Path(dataset_dir).iterdir()
        )
    except OSError:
        return False


def dataset_glob(dataset_dir: Path | str) -> str:
    """数据集目录 → 只收 date=* 分区的 glob。

    数据集根目录下散落的文件 (备份 / 中间产物) 不是分区: polars 与 DuckDB 都会
    把它当成分区数据一起读进来, 同一 (date, symbol) 出现两次 → 结果悄悄多算。
    目录里没有 date= 分区 (单文件型数据集) 时退回全目录 glob。
    """
    path = Path(dataset_dir)
    if has_date_partitions(path):
        return (path / "date=*" / "**" / "*.parquet").as_posix()
    return (path / "**" / "*.parquet").as_posix()

def partition_only_glob(glob: str) -> str:
    """数据集 glob → 只收 date=* 分区的版本 (DuckDB read_parquet 用)。

    数据集根目录下散落的文件 (备份 / 中间产物) 不是分区: read_parquet 会把它们
    当成分区数据一起读进来, 同一 (date, symbol) 出现两次, 查询结果悄悄多算。
    """
    parts = glob.split("/")
    if not parts[-1].endswith(".parquet"):
        return glob
    cut = next((i for i, part in enumerate(parts) if any(c in part for c in "*?[")), len(parts))
    if cut == 0 or cut >= len(parts):
        return glob
    head = "/".join(parts[:cut])
    if not has_date_partitions(head):
        return glob
    return f"{head}/date=*/**/*.parquet"


DAILY_STORAGE_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "quote_ts": pl.Int64,
}

ENRICHED_STORAGE_SCHEMA: dict[str, pl.DataType] = {
    "symbol": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
    "amount": pl.Float64,
    "raw_close": pl.Float64,
    "raw_high": pl.Float64,
    "raw_low": pl.Float64,
    "turnover_rate": pl.Float64,
    "consecutive_limit_ups": pl.UInt32,
    "consecutive_limit_downs": pl.UInt32,
    "quote_ts": pl.Int64,
}


def scan_parquet_compat(source: Any, **kwargs: Any) -> pl.LazyFrame:
    """Scan partitioned parquet while tolerating additive schema changes."""
    kwargs.setdefault("missing_columns", "insert")
    kwargs.setdefault("extra_columns", "ignore")
    return pl.scan_parquet(source, **kwargs)


def scan_daily_parquet(source: Any, **kwargs: Any) -> pl.LazyFrame:
    kwargs.setdefault("schema", DAILY_STORAGE_SCHEMA)
    kwargs.setdefault("cast_options", pl.ScanCastOptions(integer_cast="allow-float"))
    return scan_parquet_compat(source, **kwargs)


def scan_enriched_parquet(source: Any, **kwargs: Any) -> pl.LazyFrame:
    kwargs.setdefault("schema", ENRICHED_STORAGE_SCHEMA)
    kwargs.setdefault("cast_options", pl.ScanCastOptions(integer_cast="allow-float"))
    return scan_parquet_compat(source, **kwargs)
