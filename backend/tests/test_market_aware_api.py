"""市场感知生成 API 契约: 事实预览端点 + 合并生成端点。

沿用 mini-app 模式: 只挂 strategy 路由, 不进主应用 lifespan。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import strategy as strategy_api


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(strategy_api.router)
    app.state.data_dir = tmp_path
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    return TestClient(app)


def test_facts_endpoint_reports_missing_dimension(monkeypatch, client: TestClient) -> None:
    """缺数据的维度必须给出带指路的 400, 不静默降级。"""
    from app.services.market_facts import MarketFactsUnavailableError

    def _boom(*args, **kwargs):
        raise MarketFactsUnavailableError("还没有可用的资金流数据: 请先到「扩展数据」建一张时序型扩表")

    monkeypatch.setattr("app.services.market_facts.build_market_facts", _boom)
    response = client.post("/api/strategies/market-aware/facts", json={
        "dimensions": ["money_flow"], "asset_type": "stock", "window_days": 120,
    })
    assert response.status_code == 400
    assert "扩展数据" in response.json()["detail"]


def test_facts_endpoint_rejects_unknown_dimension(client: TestClient) -> None:
    response = client.post("/api/strategies/market-aware/facts", json={
        "dimensions": ["moon_phase"], "asset_type": "stock", "window_days": 120,
    })
    assert response.status_code == 422


def test_facts_endpoint_rejects_extra_fields(client: TestClient) -> None:
    response = client.post("/api/strategies/market-aware/facts", json={
        "dimensions": ["market"], "asset_type": "stock", "window_days": 120, "extra": 1,
    })
    assert response.status_code == 422


def test_build_endpoint_rejects_missing_run(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_facts.build_market_facts",
        lambda *a, **k: {"dimensions": ["market"], "window": {"start": "", "end": "", "days": 120}},
    )
    response = client.post("/api/strategies/market-aware/build", json={
        "run_id": "does-not-exist", "dimensions": ["market"], "asset_type": "stock",
        "window_days": 120,
    })
    assert response.status_code == 400
    assert "挖掘任务" in response.json()["detail"]


_FACTS = {
    "dimensions": ["market"],
    "window": {"start": "2026-01-01", "end": "2026-05-01", "days": 120},
    "market": {
        "bars": 120,
        "states": [{"state": "bull", "days": 60}],
        "phases": [{"phase": "主升", "days": 30}],
        "score": {"mean": 55.0, "last": 60.0},
        "up_ratio": {"mean": 0.5, "last": 0.55},
        # build_market_facts 恒定产出 up_share (up/(up+down), 0-1); 文本层只喂它。
        "up_share": {"mean": 0.55, "last": 0.58},
        "avg_pct": {"mean": 0.001, "last": 0.002},
        "above_ma20_pct": {"mean": 0.5, "last": 0.52},
        "phase_last": "主升",
        "recent": [],
    },
}

_CHAMPION = {
    "run_id": "run-1",
    "signature": "sig",
    "name": "冠军组合",
    "score": 1.0,
    "factors": {"mom_20": 0.6},
    "directions": {"mom_20": "high"},
    "provenance": {},
    "metrics": {},
}


def test_build_stream_reports_stages_and_done(client: TestClient, monkeypatch) -> None:
    """生成端点必须流式吐出阶段事件, 让前端能显示进度而不是黑箱等待。"""
    class _FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, run_id):
            return {"run_id": run_id, "artifacts": {}}

    async def _fake_ai(messages, **kwargs):
        yield '{"name":"市场感知策略","params":{"entry_score":80,"exit_score":50,'
        yield '"top_rank":10},"risk":{"stop_loss":-0.05,"max_hold_days":20}}'

    monkeypatch.setattr("app.services.mining_jobs.MiningRunStore", _FakeStore)
    monkeypatch.setattr("app.services.market_facts.build_market_facts", lambda *a, **k: _FACTS)
    monkeypatch.setattr("app.services.market_aware.champion_from_run", lambda *a, **k: _CHAMPION)
    monkeypatch.setattr("app.services.ai_provider.stream_ai_text", _fake_ai)
    monkeypatch.setattr(strategy_api, "_get_engine", lambda request: object())
    monkeypatch.setattr(strategy_api, "save_strategy_code_core", lambda *a, **k: {})
    monkeypatch.setattr(strategy_api, "_invalidate_strategy_runtime", lambda request: None)

    response = client.post("/api/strategies/market-aware/build", json={
        "run_id": "run-1", "dimensions": ["market"], "asset_type": "stock", "window_days": 120,
    })
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    kinds = [event["type"] for event in events]
    assert "stage" in kinds and "delta" in kinds and kinds[-1] == "done"
    assert [event["key"] for event in events if event["type"] == "stage"][:3] == [
        "facts", "champion", "ai",
    ]
    assert events[-1]["result"]["strategy_id"].startswith("custom_market_aware_")
    assert events[-1]["result"]["decisions"]["params"]["entry_score"] == 80


def test_build_stream_reports_error_event(client: TestClient, monkeypatch) -> None:
    """流已开始后失败要吐 error 事件 (HTTP 已是 200), 不能静默断流。"""
    class _FakeStore:
        def __init__(self, *args, **kwargs):
            pass

        def get(self, run_id):
            return {"run_id": run_id, "artifacts": {}}

    async def _boom_ai(messages, **kwargs):
        raise RuntimeError("模型不可用")
        yield  # pragma: no cover - 让函数成为异步生成器

    monkeypatch.setattr("app.services.mining_jobs.MiningRunStore", _FakeStore)
    monkeypatch.setattr("app.services.market_facts.build_market_facts", lambda *a, **k: _FACTS)
    monkeypatch.setattr("app.services.market_aware.champion_from_run", lambda *a, **k: _CHAMPION)
    monkeypatch.setattr("app.services.ai_provider.stream_ai_text", _boom_ai)

    response = client.post("/api/strategies/market-aware/build", json={
        "run_id": "run-1", "dimensions": ["market"], "asset_type": "stock", "window_days": 120,
    })
    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events[-1]["type"] == "error"
    assert "模型不可用" in events[-1]["message"]
