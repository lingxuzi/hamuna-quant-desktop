"""盘后管道分钟K阶段门控: 生效分钟源解析成功 (声明 minute 数据集) 即放行。

门控口径与 api/kline._minute_allowed 一致 —— 生效分钟源解析失败或未声明
minute 数据集时, 本轮静默跳过分钟阶段, 不跨源回退。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.jobs import daily_pipeline
from app.services import data_integrity, preferences
from app.data_providers.capabilities import CapabilitySet


def _fake_provider(datasets: set[str]) -> SimpleNamespace:
    """最小 provider 替身: resolver 只读 config.datasets。"""
    return SimpleNamespace(config=SimpleNamespace(datasets={ds: {} for ds in datasets}))


def _run_pipeline(tmp_path: Path, monkeypatch, minute_provider: str,
                  datasets: set[str]) -> tuple[dict, list[dict]]:
    """跑 run_now 到分钟阶段, 返回 (result, sync_and_persist_minute 调用记录)。"""
    monkeypatch.setattr(daily_pipeline.settings, "data_dir", tmp_path)
    monkeypatch.setattr(preferences, "load", lambda: {
        "pipeline_pull_a_share": False, "pipeline_pull_etf": False,
        "pipeline_pull_index": False,   # 指数/ETF 阶段与本用例无关, 直接跳过
        "pipeline_regime_enabled": False, "minute_sync_enabled": True,
        "minute_sync_days": 5, "minute_data_provider": minute_provider,
    })
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: minute_provider)
    monkeypatch.setattr(daily_pipeline.instrument_sync, "sync_instruments", lambda *_: 0)
    monkeypatch.setattr(daily_pipeline, "_resolve_universe", lambda *_: [])
    monkeypatch.setattr(daily_pipeline, "_invalidate", lambda *_: None)
    monkeypatch.setattr(daily_pipeline, "_refresh_views", lambda *_: None)
    monkeypatch.setattr(daily_pipeline, "_refresh_single_view", lambda *_: None)
    monkeypatch.setattr(data_integrity, "scan_recent_integrity", lambda *_, **__: [])

    # 真 resolver + 假自定义源: 该源声明/不声明 minute 数据集
    from app.data_providers import custom as custom_sources

    monkeypatch.setattr(
        custom_sources, "get_provider", lambda name: _fake_provider(datasets),
    )

    calls: list[dict] = []

    def fake_sync_minute(symbols, *args, **kwargs):
        calls.append({"symbols": symbols, **kwargs})
        return 1

    monkeypatch.setattr(
        daily_pipeline.kline_sync, "sync_and_persist_minute", fake_sync_minute,
    )
    repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path),
        latest_daily_date=lambda: date(2026, 9, 28),
    )
    return daily_pipeline.run_now(repo, CapabilitySet(set())), calls


@pytest.mark.parametrize("datasets,expect_called", [
    ({"minute"}, True),    # 生效源声明 minute 数据集 → 放行
    ({"daily"}, False),    # 生效源没有 minute 数据集 → 跳过
])
def test_minute_stage_gate_routes_by_provider(tmp_path, monkeypatch, datasets, expect_called):
    result, calls = _run_pipeline(tmp_path, monkeypatch, "customsrc", datasets)
    assert bool(calls) is expect_called
    if expect_called:
        assert calls[0]["days"] == 5
        assert "sync_minute" not in result["skipped_stages"]
    else:
        assert "sync_minute" in result["skipped_stages"]
