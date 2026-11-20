"""数据集根目录里的散落文件不能被当成数据读进来。

背景: 备份文件落在数据集根目录时, read_parquet / pyarrow dataset 会把它当成分区
数据读进来, 同一 (date, symbol) 出现两次 → DuckDB 查询悄悄多算, 矩阵构建直接
失败 (实测报 "MarketDataMatrix requires unique date/symbol rows")。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from app.backtest.matrix import load_market_data_matrix_from_parquet
from app.data_providers.repository import _rewrite_view_glob
from app.parquet import dataset_glob, partition_only_glob


def test_partition_only_glob_narrows_date_partitioned_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "kline_daily"
    (dataset / "date=2024-01-02").mkdir(parents=True)
    glob = f"{dataset}/**/*.parquet"
    assert partition_only_glob(glob) == f"{dataset}/date=*/**/*.parquet"


def test_partition_only_glob_keeps_single_file_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "adj_factor"
    dataset.mkdir()
    glob = f"{dataset}/**/*.parquet"
    assert partition_only_glob(glob) == glob


def test_dataset_glob_narrows_date_partitioned_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "kline_daily"
    (dataset / "date=2024-01-02").mkdir(parents=True)
    assert dataset_glob(dataset) == f"{dataset}/date=*/**/*.parquet"


def test_dataset_glob_keeps_single_file_dataset(tmp_path: Path) -> None:
    dataset = tmp_path / "adj_factor"
    dataset.mkdir()
    assert dataset_glob(dataset) == f"{dataset}/**/*.parquet"


def test_rewrite_view_glob_narrows_read_parquet(tmp_path: Path) -> None:
    dataset = tmp_path / "kline_daily"
    (dataset / "date=2024-01-02").mkdir(parents=True)
    sql = f"CREATE VIEW v AS SELECT * FROM read_parquet('{dataset}/**/*.parquet')"
    assert (
        _rewrite_view_glob(sql)
        == f"CREATE VIEW v AS SELECT * FROM read_parquet('{dataset}/date=*/**/*.parquet')"
    )


def test_matrix_ignores_stray_file_in_dataset_root(tmp_path: Path) -> None:
    root = tmp_path / "kline_daily_enriched"
    day = date(2024, 1, 2)
    partition = root / f"date={day.isoformat()}"
    partition.mkdir(parents=True)
    row = pl.DataFrame(
        {
            "symbol": ["600001.SH"],
            "date": [day],
            "open": [10.0],
            "high": [10.0],
            "low": [10.0],
            "close": [10.0],
            "raw_close": [10.0],
            "volume": [1000.0],
        }
    )
    row.write_parquet(partition / "part.parquet")
    # 同一行的备份落在数据集根目录: 旧实现读成两行, 矩阵构建失败
    row.write_parquet(root / "rows.bak.parquet")

    market = load_market_data_matrix_from_parquet(
        root,
        day,
        day,
        field_columns={"raw_close"},
    )
    assert market.symbols == ("600001.SH",)
