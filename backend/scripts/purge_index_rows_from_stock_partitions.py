"""一次性清理: 指数行情混入股票分区。

背景
----
quote_service 的实时轮询会把基准指数 (000002.SH 上证A指 / 399107.SZ 深证A指 /
399102.SZ 创业板综) 与核心四只一起显式拉取, 但分流时只按 `get_index_symbol_set()`
判定——该维表只有 101 行, 上述基准指数不在其中, 于是被当成股票写进
`kline_daily` / `kline_daily_enriched`。

后果: 指数点位 (3777 之类) 混进个股分区。任何全市场扫描 / 筛选 / 回测 / AI 助手
取数都会把这两只当成"个股"参与选股与统计, 而且它们的 volume 口径是股不是手,
amount 在部分行是错的量级。

清理规则
--------
只在 `symbol` 属于 `kline_index_daily` 已确认的指数代码、且不在股票维表
`instruments` 中时删除该行——两个条件同时成立才动, 避免误删真个股。

幂等: 重跑无副作用。用 atomic_write_parquet 原地替换, 不新增文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

from app.services.fs_utils import atomic_write_parquet


def _index_codes(data_dir: Path) -> set[str]:
    idx = data_dir / "kline_index_daily"
    if not idx.exists():
        return set()
    codes: set[str] = set()
    for part in idx.glob("date=*/part.parquet"):
        codes.update(pl.read_parquet(part, columns=["symbol"])["symbol"].unique().to_list())
    return codes


def _stock_codes(data_dir: Path) -> set[str]:
    inst = data_dir / "instruments" / "instruments.parquet"
    if not inst.exists():
        return set()
    return set(pl.read_parquet(inst, columns=["symbol"])["symbol"].unique().to_list())


def clean(data_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    """返回 {table: 删除行数}。"""
    index_codes = _index_codes(data_dir)
    stock_codes = _stock_codes(data_dir)
    # 两个条件同时成立才删除: 出现在指数日线, 且不是股票维表里的个股
    polluted = index_codes - stock_codes
    if not polluted:
        return {}

    stats: dict[str, int] = {}
    for table in ("kline_daily", "kline_daily_enriched"):
        removed = 0
        for part in sorted((data_dir / table).glob("date=*/part.parquet")):
            df = pl.read_parquet(part)
            if "symbol" not in df.columns:
                continue
            bad = df.filter(pl.col("symbol").is_in(list(polluted)))
            if bad.is_empty():
                continue
            removed += bad.height
            kept = df.filter(~pl.col("symbol").is_in(list(polluted)))
            if not dry_run and not kept.is_empty():
                atomic_write_parquet(kept, part)
        if removed:
            stats[table] = removed
    return stats


if __name__ == "__main__":
    data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "../data")
    dry = "--dry-run" in sys.argv
    result = clean(data_dir, dry_run=dry)
    print(("DRY-RUN " if dry else "") + f"purged: {result}")
