"""挖掘数据必须落在前一交易日, 不用今日数据。

盘中实时行情每 ~5s 覆写当天 enriched 分区并换一个全局 generation, 挖掘读到
当天数据必然撞 "enriched data changed while the snapshot was being read"。
锁定三件事:
① mining_data_end 把可用末日压到严格早于今天的最后一个分区;
② worker 解码请求时同样压口 (今天的数据不参与挖掘);
③ 读后校验改用前缀世代: 重写当天分区不动 ≤end 的前缀, 重写 ≤end 才失效。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from app.backtest.engine import BacktestEngine
from app.backtest.mining_runtime import _decode_runtime_request
from app.data_providers.repository import DataStore, KlineRepository
from app.market_time import cn_today
from app.services.mining_preflight import mining_availability, mining_data_end

TODAY = date(2026, 11, 19)
YESTERDAY = date(2026, 11, 18)


def _write_partitions(tmp_path: Path, dates: list[date]) -> None:
    for value in dates:
        partition = tmp_path / "kline_daily_enriched" / f"date={value.isoformat()}"
        partition.mkdir(parents=True, exist_ok=True)
        (partition / "part.parquet").touch()


# ── ① mining_data_end: 压到前一交易日 ──


def test_mining_data_end_excludes_today_partition() -> None:
    dates = [date(2026, 11, 17), YESTERDAY, TODAY]
    assert mining_data_end(Path("."), "stock", dates=dates, today=TODAY) == YESTERDAY


def test_mining_data_end_clamps_later_explicit_end() -> None:
    dates = [YESTERDAY, TODAY]
    assert mining_data_end(Path("."), "stock", TODAY, dates=dates, today=TODAY) == YESTERDAY


def test_mining_data_end_keeps_earlier_explicit_end() -> None:
    dates = [date(2026, 11, 17), YESTERDAY, TODAY]
    assert (
        mining_data_end(Path("."), "stock", date(2026, 11, 17), dates=dates, today=TODAY)
        == date(2026, 11, 17)
    )


def test_mining_data_end_falls_back_to_today_when_only_today_exists() -> None:
    """首日同步只有一个分区时无从退让, 仍按今天算 (不静默丢数据)。"""
    assert mining_data_end(Path("."), "stock", dates=[TODAY], today=TODAY) == TODAY


def test_mining_data_end_without_partitions_keeps_explicit_end() -> None:
    assert mining_data_end(Path("."), "stock", TODAY, dates=[], today=TODAY) == TODAY


def test_mining_availability_effective_end_is_previous_trading_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.mining_preflight.cn_today", lambda: TODAY)
    _write_partitions(tmp_path, [date(2026, 11, 17), YESTERDAY, TODAY])
    availability = mining_availability(
        tmp_path,
        asset_type="stock",
        budget_profile="exploratory",
    )
    assert availability.available_end == TODAY
    assert availability.effective_end == YESTERDAY


# ── ② worker 解码: 请求里的 end 也压到前一交易日 ──


def test_runtime_request_end_is_capped_to_previous_trading_day(tmp_path: Path) -> None:
    today = cn_today()
    dates = [today - timedelta(days=offset) for offset in range(240, 0, -1)] + [today]
    _write_partitions(tmp_path, dates)

    payload = {
        "run_id": "cap-end",
        "request": {
            "factor_names": ["turnover_rate"],
            "strategy_ids": [],
            "asset_type": "stock",
            "budget_profile": "exploratory",
            "start": dates[0].isoformat(),
            "end": today.isoformat(),
        },
    }
    request = _decode_runtime_request(payload, tmp_path, SimpleNamespace())
    assert request.end < today
    assert request.end == dates[-2]


# ── ③ 前缀世代: 盘中重写当天分区不再打断读 ──


def _frame(day: date, close: float = 10.0) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["600000.SH"], "date": [day],
        "open": [close], "high": [close], "low": [close],
        "close": [close], "volume": [1000.0],
    })


@pytest.fixture()
def repo(tmp_path: Path):
    result = KlineRepository(DataStore(tmp_path))
    yield result
    result.db.close()


def test_prefix_generation_survives_intraday_republish_of_today(repo) -> None:
    day = date(2026, 11, 18)
    today = date(2026, 11, 19)
    repo.append_enriched(pl.concat([_frame(day), _frame(today)]))
    engine = BacktestEngine(repo)
    token = engine.prefix_data_generation("stock", day)

    # 盘中重写今天分区: 全局世代变了, 但 ≤day 的前缀没动
    repo.flush_live_enriched(_frame(today, 12.0))
    assert engine.prefix_data_generation("stock", day) == token

    # 回补 ≤end 的分区: 前缀必须失效 (fail-closed)
    repo.flush_live_enriched(_frame(day, 9.0))
    assert engine.prefix_data_generation("stock", day) != token
