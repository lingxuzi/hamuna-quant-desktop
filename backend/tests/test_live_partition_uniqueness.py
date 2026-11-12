"""实时落盘的当日唯一性契约 (同一 symbol+date 只留一行, 残缺快照不洗掉整天数据)。

背景: 行情快照里同一 symbol 会出现多条 —— 实测 easytdx 全市场分类列表当天返回
5572 条记录但只有 5559 个唯一 symbol (13 只重复)。实时落盘按「当日分区」覆写,
不去重会连锁放大:

  - 分区里出现同一 (symbol, date) 多行 → 回测矩阵要求唯一 → 报
    "MarketDataMatrix requires unique timestamp/symbol rows";
  - enriched 的内存缓存带着重复行进下一轮, 被当作「昨日状态」再 join 一次,
    行数每轮 ×4 (实测 11-10 分区 477042 行 / 5574 只, 倍数全是 4 的幂:
    1/4/16/64/256/1024/4096/16384/65536);
  - 盘中快照会变残缺 (实测「行情刷新」只数从 5574 一路降到 4672), 盲覆写会把
    整天数据洗掉 —— 11-09 的 enriched 分区只剩 23 行。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from app.data_providers.repository import DataStore, KlineRepository
from app.enriched_generation import EnrichedPublication
from app.market_time import cn_today
from app.plugins.easytdx.provider import EasyTdxProvider


def _repo(tmp_path: Path) -> KlineRepository:
    repo = KlineRepository(DataStore(tmp_path))
    repo._instruments_cache = pl.DataFrame({
        "symbol": ["600000.SH", "000001.SZ"],
        "name": ["浦发银行", "平安银行"],
        "total_shares": [1.0, 2.0],
        "float_shares": [1.0, 2.0],
    })
    return repo


def _live_row(symbol: str, close: float, day: date | None = None) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [symbol],
        "date": [day or cn_today()],
        "open": [close],
        "high": [close],
        "low": [close],
        "close": [close],
        "volume": [1000.0],
        "amount": [close * 1000.0],
        "raw_close": [close],
        "raw_high": [close],
        "raw_low": [close],
    })


def _part(tmp_path: Path, table: str) -> pl.DataFrame:
    return pl.read_parquet(
        tmp_path / table / f"date={cn_today().isoformat()}" / "part.parquet"
    )


# ------------------------------------------------------------------ 日K
def test_flush_live_daily_asset_keeps_one_row_per_symbol_date(tmp_path):
    """快照里同一 symbol 出现两条 → 当日分区只能有一行。"""
    repo = _repo(tmp_path)
    repo.flush_live_daily_asset("stock", pl.concat([
        _live_row("600000.SH", 10.0),
        _live_row("600000.SH", 10.0),
    ]))

    df = _part(tmp_path, "kline_daily")
    assert df.height == 1, f"当日日K分区出现重复行: {df.height}"


def test_flush_live_daily_asset_keeps_symbols_missing_from_partial_snapshot(tmp_path):
    """盘中快照变残缺 (本轮只剩 1 只) → 上一轮其余标的的行不能被洗掉。"""
    repo = _repo(tmp_path)
    repo.flush_live_daily_asset("stock", pl.concat([
        _live_row("600000.SH", 10.0),
        _live_row("000001.SZ", 12.0),
    ]))
    repo.flush_live_daily_asset("stock", _live_row("600000.SH", 11.0))

    df = _part(tmp_path, "kline_daily")
    assert df["symbol"].n_unique() == 2, "残缺快照把当天的标的洗掉了"
    # 本轮出现的标的取新值
    assert df.filter(pl.col("symbol") == "600000.SH")["close"].to_list() == [11.0]


# ------------------------------------------------------------------ enriched
def test_flush_live_daily_asset_merges_stream_files_in_partition(tmp_path):
    """回补留下 part-stream-*.parquet 时, 落盘不能产生跨文件重复行。"""
    repo = _repo(tmp_path)
    part_dir = tmp_path / "kline_daily" / f"date={cn_today().isoformat()}"
    part_dir.mkdir(parents=True)
    _live_row("600000.SH", 9.0).write_parquet(part_dir / "part-stream-0.parquet")
    _live_row("000001.SZ", 8.0).write_parquet(part_dir / "part-stream-1.parquet")

    repo.flush_live_daily_asset("stock", _live_row("600000.SH", 10.0))

    df = _part(tmp_path, "kline_daily")
    assert df.height == 2, f"分区出现跨文件重复行: {df.height}"
    assert df["symbol"].n_unique() == 2
    assert df.filter(pl.col("symbol") == "600000.SH")["close"].to_list() == [10.0]
    assert len(list(part_dir.glob("*.parquet"))) == 1, "合并后应只剩一个分区文件"


def test_flush_live_enriched_asset_keeps_one_row_per_symbol_date(tmp_path):
    """enriched 分区与内存缓存都不能带重复行 (缓存带重复 = 下一轮被再放大一次)。"""
    repo = _repo(tmp_path)
    repo.flush_live_enriched_asset("stock", pl.concat([
        _live_row("600000.SH", 10.0),
        _live_row("600000.SH", 10.0),
    ]))

    df = _part(tmp_path, "kline_daily_enriched")
    assert df.height == 1, f"当日 enriched 分区出现重复行: {df.height}"

    cached, cached_date = repo.get_enriched_latest()
    assert cached_date == cn_today()
    assert cached.height == 1, "内存缓存带着重复行, 下一轮会把它当昨日状态再 join 一次"


def test_flush_live_enriched_asset_keeps_symbols_missing_from_partial_snapshot(tmp_path):
    repo = _repo(tmp_path)
    repo.flush_live_enriched_asset("stock", pl.concat([
        _live_row("600000.SH", 10.0),
        _live_row("000001.SZ", 12.0),
    ]))
    repo.flush_live_enriched_asset("stock", _live_row("600000.SH", 11.0))

    df = _part(tmp_path, "kline_daily_enriched")
    assert df["symbol"].n_unique() == 2, "残缺快照把当天的标的洗掉了"


def test_replace_enriched_rows_does_not_persist_duplicate_rows(tmp_path):
    """修补路径同样不能把重复行落盘 (否则两次修补都洗不掉已污染的分区)。"""
    repo = _repo(tmp_path)
    repo.flush_live_enriched_asset("stock", _live_row("600000.SH", 10.0))

    publication = EnrichedPublication(tmp_path, "stock", recover=True)
    frame = pl.concat([
        _live_row("600000.SH", 20.0),
        _live_row("600000.SH", 20.0),
    ])
    repo.replace_enriched_rows(
        "stock", frame, start=cn_today(), symbols=["600000.SH"], publication=publication,
    )
    publication.commit()

    df = _part(tmp_path, "kline_daily_enriched")
    assert df.height == 1, f"修补路径落下了重复行: {df.height}"


# ------------------------------------------------------------------ provider
class _StubClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def quote_list(self, category_name="A", count=6000):
        return list(self.rows)

    def close(self) -> None:
        pass


def _snapshot_row(close: float) -> dict:
    return {
        "market": 1, "code": "600000", "name": "浦发银行", "pre_close": 10.0,
        "open": 10.0, "high": 10.0, "low": 10.0, "close": close,
        "vol": 100.0, "amount": 1000.0, "turnover": 0.1,
    }


def test_get_realtime_keeps_one_record_per_symbol():
    """全市场分类列表偶发同一标的多行 → 快照按 symbol 去重 (保留最后一条)。"""
    provider = EasyTdxProvider()
    provider._client = _StubClient([_snapshot_row(10.0), _snapshot_row(10.5)])

    records = provider.get_realtime()

    assert len(records) == 1, f"快照返回了重复标的: {len(records)} 条"
    assert records[0]["symbol"] == "600000.SH"
    assert records[0]["last_price"] == 10.5
