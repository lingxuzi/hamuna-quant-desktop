"""enriched 缓存刷新必须自己拿 instruments —— 不能靠「谁先加载过」。

事故: _refresh_enriched_impl 直接读 _instruments_cache, 而刷新可能先于
instruments 加载发生 (空表) → compute_limit_signals 整段跳过 → 缓存静默少
signal_limit_* / consecutive_limit_* / turnover_rate / name, 引用这些列的
实盘策略报「引用了不存在的列」。
修复: 走 get_instruments() 懒加载。
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import polars as pl

from app.data_providers.repository import DataStore, KlineRepository
from app.indicators import pipeline

SYMBOLS = ("600001.SH", "000002.SZ")
LAST_DAY = date(2026, 8, 14)
BASE_COLS = ["symbol", "date", "open", "high", "low", "close", "volume", "amount",
             "raw_close", "raw_high", "raw_low"]


def _trading_days(n: int = 90) -> list[date]:
    days: list[date] = []
    d = LAST_DAY
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _base_frame() -> pl.DataFrame:
    """日线基础列 (未复权 == 前复权)。最后一天 600001.SH 涨停 (10%, 主板)。"""
    days = _trading_days()
    rows = []
    for symbol in SYMBOLS:
        for i, day in enumerate(days):
            close = 10.0 + 0.05 * math.sin(i * 0.3)
            if i == len(days) - 2:
                close = 10.0
            if i == len(days) - 1 and symbol == "600001.SH":
                close = 11.0
            rows.append({
                "symbol": symbol, "date": day,
                "open": close, "high": close, "low": close,
                "close": close, "volume": 1000.0, "amount": close * 1000.0,
                "raw_close": close, "raw_high": close, "raw_low": close,
            })
    return pl.DataFrame(rows)


def _seed_data_dir(tmp_path) -> None:
    """按分区布局写 enriched parquet + instruments 维表。"""
    frame = _base_frame()
    enriched_dir = tmp_path / "kline_daily_enriched"
    for day, part in frame.partition_by("date", as_dict=True).items():
        day_value = day[0] if isinstance(day, tuple) else day
        path = enriched_dir / f"date={day_value}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        part.sort("symbol").write_parquet(path)

    inst_dir = tmp_path / "instruments"
    inst_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": list(SYMBOLS),
        "name": ["浦发银行", "万科A"],
        "total_shares": [29_352_000_000.0, 11_000_000_000.0],
        "float_shares": [29_352_000_000.0, 9_700_000_000.0],
    }).write_parquet(inst_dir / "part.parquet")


def test_refresh_loads_instruments_lazily(tmp_path, monkeypatch):
    """instruments 未预热时刷新 enriched, 缓存仍必须带涨跌停/换手率/名称列。"""
    monkeypatch.setattr(pipeline, "_custom_signal_exprs", {})
    _seed_data_dir(tmp_path)

    repo = KlineRepository(DataStore(tmp_path))
    assert repo._instruments_cache is None, "用例前提: instruments 尚未加载"

    frame, as_of = repo.get_enriched_latest_asset("stock")

    assert as_of == LAST_DAY
    for col in ("limit_up_price", "consecutive_limit_ups", "turnover_rate", "name"):
        assert col in frame.columns, f"enriched 缓存缺少 {col}"
    hit = frame.filter(pl.col("symbol") == "600001.SH")
    assert hit["consecutive_limit_ups"].max() == 1, "涨停日应判定为连板 1"
    assert hit.filter(pl.col("date") == LAST_DAY)["name"].to_list() == ["浦发银行"]
