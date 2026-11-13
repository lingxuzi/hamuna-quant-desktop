"""市场感知生成 API 契约: 事实预览端点 + 合并生成端点。

沿用 mini-app 模式: 只挂 strategy 路由, 不进主应用 lifespan。
"""
from __future__ import annotations

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
