# 回归: 口径条声称「剔除 ST 与停牌」, 但自定义条件路径 (ScreenerService.run)
# 原本没有任何 ST 过滤 — 197 只 ST 股会混进自定义选股结果。
# 停牌股在管道层天然无当日行, 不需要额外处理; ST 由本测试锁死。
import types
from datetime import date

import polars as pl
import pytest

from app.services.screener import ScreenerService


class _FakeRepo:
    """最小 repo 桩: 只实现 run() 数据路径需要的取数接口。"""

    def __init__(self, data_dir, enriched, instruments, latest):
        self.store = types.SimpleNamespace(data_dir=data_dir)
        self._enriched = enriched
        self._instruments = instruments
        self._latest = latest

    def get_enriched_latest_asset(self, asset_type):
        return self._enriched, self._latest

    def get_instruments_asset(self, asset_type):
        return self._instruments

    def get_enriched_history(self, target_date, lookback_days):
        return None


def _make_repo(tmp_path, names):
    day = date(2026, 11, 9)
    enriched = pl.DataFrame({
        "symbol": [f"{600000 + i}.SH" for i in range(len(names))],
        "date": [day] * len(names),
        "close": [10.0] * len(names),
        "volume": [1000.0] * len(names),
        "amount": [1e7] * len(names),
    })
    instruments = pl.DataFrame({
        "symbol": enriched["symbol"],
        "name": names,
    })
    return _FakeRepo(tmp_path, enriched, instruments, day), day


def test_custom_run_excludes_st(tmp_path, monkeypatch):
    repo, day = _make_repo(tmp_path, ["贵州茅台", "ST康美", "*ST节能", "退市西水", "平安银行"])
    svc = ScreenerService(repo)
    # 屏蔽盘后完整性检查 (桩数据无管道产物)
    monkeypatch.setattr(svc, "_assert_integrity", lambda *a, **k: None)

    result = svc.run(as_of=day, conditions=["close > 0"])
    got = {row["symbol"] for row in result.rows}
    assert got == {"600000.SH", "600004.SH"}  # 只剩两只非 ST
    assert result.total == 2


def test_custom_run_etf_no_name_column_keeps_all(tmp_path, monkeypatch):
    # ETF: 无 name 列 (无 instruments join), 过滤跳过不误杀
    repo, day = _make_repo(tmp_path, [])
    repo._enriched = repo._enriched.drop("name") if "name" in repo._enriched.columns else repo._enriched
    repo._instruments = pl.DataFrame()
    svc = ScreenerService(repo, asset_type="etf")
    monkeypatch.setattr(svc, "_assert_integrity", lambda *a, **k: None)

    enriched = pl.DataFrame({
        "symbol": ["510050.SH", "510300.SH"],
        "date": [day, day],
        "close": [3.0, 4.0],
        "volume": [100.0, 200.0],
        "amount": [1e6, 1e6],
    })
    repo._enriched = enriched
    result = svc.run(as_of=day, conditions=["close > 0"])
    assert result.total == 2
