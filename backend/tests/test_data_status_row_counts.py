"""数据页行数统计: 日K / 日K增强 必须报真实行数, 不能写死 0。

背景 (2026-11-12): 页面顶部显示「日K 0 行」、行情表 日K / 日K·增强 的行数列
长期是 0 —— 后端两个聚合器把 rows 硬编码成 0 (当时为躲开一次全表扫描),
看着像数据丢了。count(*) 只读 parquet footer 不读数据列, 不该省。
"""
from __future__ import annotations

from datetime import date

import polars as pl

from app.api import data as data_api
from app.data_providers.repository import DataStore, KlineRepository

DAY = date(2026, 7, 15)


def _frame(n: int = 3) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [f"{i:06d}.SZ" for i in range(n)],
        "date": [DAY] * n,
        "open": [10.0] * n,
        "high": [10.0] * n,
        "low": [10.0] * n,
        "close": [10.0] * n,
        "volume": [100.0] * n,
        "amount": [1000.0] * n,
    })


def test_daily_aggregate_reports_real_row_count(tmp_path):
    repo = KlineRepository(DataStore(tmp_path))
    repo.append_daily(_frame(3))
    repo.rebuild_views()  # 空目录启动时视图没挂上, 落盘后重挂

    stats = data_api._safe_aggregate_daily(repo)

    assert stats is not None
    assert stats["rows"] == 3, "日K 行数不能写死 0"
    assert stats["trading_days"] == 1


def test_enriched_aggregate_reports_real_row_count(tmp_path):
    repo = KlineRepository(DataStore(tmp_path))
    repo.append_enriched(_frame(2))
    repo.rebuild_views()

    stats = data_api._safe_aggregate_enriched(repo)

    assert stats is not None
    assert stats["rows"] == 2, "日K增强 行数不能写死 0"
    assert stats["trading_days"] == 1
