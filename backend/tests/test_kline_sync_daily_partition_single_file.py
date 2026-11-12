"""流式日K同步: 一个日期只落一个分区文件。

背景 (2026-11-12): 回补留下 part-stream-*.parquet 碎片后, 同步路径又把每个
chunk 逐个 rename 成 part-stream-{index}.parquet —— 全市场一天能堆出 5578 个
碎片 (云端测试机累计 133 万个文件)。碎片数线性放大 DuckDB 视图注册 (逐个读
footer + union_by_name) 与扫描耗时, 实测视图注册 0.028s → 1.4s。

契约: 同步提交后, 每个 date 分区只允许一个 part.parquet。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from app.data_providers.repository import DataStore, KlineRepository
from app.services import kline_sync

DAY = date(2026, 11, 12)


def _row(symbol: str, close: float) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol], "date": [DAY], "open": [close], "high": [close],
        "low": [close], "close": [close], "volume": [100.0], "amount": [close * 100.0],
    })


def _repo(tmp_path: Path) -> KlineRepository:
    return KlineRepository(DataStore(tmp_path))


def _part_dir(tmp_path: Path) -> Path:
    return tmp_path / "kline_daily" / f"date={DAY.isoformat()}"


def test_new_partition_commits_single_file(tmp_path):
    """3 个 chunk 覆盖同一日期 → 分区只有一个 part.parquet, 无碎片。"""
    repo = _repo(tmp_path)

    written = kline_sync._persist_daily_chunks(
        [_row("600000.SH", 10.0), _row("000001.SZ", 11.0), _row("300750.SZ", 12.0)],
        repo,
    )

    assert written == 3
    files = sorted(_part_dir(tmp_path).glob("*.parquet"))
    assert len(files) == 1, f"分区应只有一个文件, 实际: {[f.name for f in files]}"
    assert files[0].name == "part.parquet"
    df = pl.read_parquet(files[0])
    assert df.height == 3
    assert sorted(df["symbol"].to_list()) == ["000001.SZ", "300750.SZ", "600000.SH"]


def test_duplicate_symbol_across_chunks_keeps_one_row(tmp_path):
    """同一 symbol 出现在多个 chunk → 分区里只留一行 (最新值)。"""
    repo = _repo(tmp_path)

    kline_sync._persist_daily_chunks(
        [_row("600000.SH", 10.0), _row("600000.SH", 12.0)], repo,
    )

    df = pl.read_parquet(_part_dir(tmp_path) / "part.parquet")
    assert df.height == 1, f"同一 symbol 落了多行: {df.height}"
    assert df["close"].to_list() == [12.0]


def test_existing_partition_merges_into_single_file(tmp_path):
    """分区已有数据 → 合并后仍只有一个文件, 历史碎片被清掉。"""
    repo = _repo(tmp_path)
    part_dir = _part_dir(tmp_path)
    part_dir.mkdir(parents=True)
    _row("600000.SH", 9.0).write_parquet(part_dir / "part.parquet")
    _row("000001.SZ", 8.0).write_parquet(part_dir / "part-stream-0.parquet")

    kline_sync._persist_daily_chunks([_row("300750.SZ", 12.0)], repo)

    files = sorted(part_dir.glob("*.parquet"))
    assert len(files) == 1, f"分区应只剩一个文件, 实际: {[f.name for f in files]}"
    df = pl.read_parquet(files[0])
    assert sorted(df["symbol"].to_list()) == ["000001.SZ", "300750.SZ", "600000.SH"]


def test_staging_dir_is_cleaned_up(tmp_path):
    """staging 是私有目录, 提交后必须清空 (崩溃残留由 sweep 兜底)。"""
    repo = _repo(tmp_path)

    kline_sync._persist_daily_chunks([_row("600000.SH", 10.0)], repo)

    staging = repo.store.data_dir / ".daily_sync_staging"
    assert not staging.exists() or not list(staging.glob("*"))
