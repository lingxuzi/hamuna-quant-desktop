"""ETF 历史补拉端点的接口契约测试。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import etf as etf_api
from app.data_providers.capabilities import Cap, CapabilitySet


@pytest.fixture()
def client(tmp_path: Path, monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(etf_api.router)
    app.state.data_dir = tmp_path
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    app.state.capabilities = CapabilitySet()
    monkeypatch.setattr(etf_api.job_store, "create", lambda *a, **k: ("job-1", True))
    return TestClient(app)


def _grant_batch() -> CapabilitySet:
    capset = CapabilitySet()
    capset.grant(Cap.KLINE_DAILY_BATCH)
    return capset


def test_sync_daily_requires_batch_cap(client: TestClient) -> None:
    resp = client.post("/api/etf/sync_daily", json={"days": 365, "batch_size": 200})
    assert resp.status_code == 403
    assert "batch" in resp.json()["detail"].lower()


def test_sync_daily_rejects_out_of_range_params(client: TestClient) -> None:
    client.app.state.capabilities = _grant_batch()
    assert client.post("/api/etf/sync_daily", json={"days": 5}).status_code == 422
    assert client.post("/api/etf/sync_daily", json={"days": 9999}).status_code == 422
    assert client.post("/api/etf/sync_daily", json={"batch_size": 1}).status_code == 422


def test_sync_daily_conflicts_when_job_running(client: TestClient, monkeypatch) -> None:
    client.app.state.capabilities = _grant_batch()
    monkeypatch.setattr(etf_api.job_store, "create", lambda *a, **k: ("job-existing", False))
    resp = client.post("/api/etf/sync_daily", json={"days": 365, "batch_size": 200})
    assert resp.status_code == 409
    assert "任务" in resp.json()["detail"]


def test_sync_history_chunks_symbols_and_reports_progress(monkeypatch) -> None:
    """按批切标的, 每批调一次同步, 行数与进度都要汇总。"""
    seen: list[list[str]] = []
    progress_calls: list[tuple[str, int]] = []

    def fake_sync(repo, capset, *, start_date, end_date, symbols_override, on_chunk_done=None):
        seen.append(list(symbols_override))
        if on_chunk_done:
            on_chunk_done(len(symbols_override), len(symbols_override))
        return len(symbols_override) * 10

    monkeypatch.setattr(etf_api.index_sync, "sync_and_persist_etf_daily", fake_sync)
    monkeypatch.setattr(etf_api, "_etf_symbols", lambda repo: [f"{i:06d}" for i in range(5)])

    result = etf_api._sync_etf_history(
        None,
        None,
        days=365,
        batch_size=2,
        progress=lambda stage, pct, msg, **kw: progress_calls.append((stage, pct)),
    )

    assert seen == [["000000", "000001"], ["000002", "000003"], ["000004"]]
    assert result["symbols"] == 5
    assert result["rows_written"] == 50
    assert result["start"] < result["end"]
    assert progress_calls[0][0] == "etf_sync"
    assert progress_calls[-1] == ("etf_sync", 100)


def test_sync_history_fails_without_symbol_list(monkeypatch) -> None:
    monkeypatch.setattr(etf_api, "_etf_symbols", lambda repo: [])
    with pytest.raises(ValueError, match="ETF"):
        etf_api._sync_etf_history(None, None, days=365, batch_size=200, progress=lambda *a, **k: None)
