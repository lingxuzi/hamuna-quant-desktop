"""实时行情开关的 HTTP 契约: 生效源提供 realtime 即可开启, 数据门禁仍生效。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import settings as settings_api
from app.data_providers import custom
from app.market_time import CN_TZ
from app.services import data_integrity, preferences
from app.services.quote_service import QuoteService

ENDPOINT = "/api/settings/preferences/realtime-quotes"


@pytest.fixture()
def quote_api(monkeypatch, tmp_path):
    saved = {"realtime_data_provider": "fuyao", "realtime_quotes_enabled": False}
    monkeypatch.setattr(preferences, "load", lambda: saved.copy())
    monkeypatch.setattr(preferences, "save", lambda updates: saved.update(updates))
    monkeypatch.setattr(custom, "names", lambda: {"fuyao"})
    # 源目录也打桩: 实时门禁看的是「生效源是否声明 realtime 且可用」。
    monkeypatch.setattr(custom, "list_plugins", lambda: [{
        "name": "fuyao",
        "display_name": "fuyao",
        "datasets": ["realtime", "daily", "adj_factor", "financial"],
        "available": True,
        "status": "ok",
    }])
    monkeypatch.setattr(custom, "list_sources", lambda: [])
    service = QuoteService()
    # 真实运行 enable/disable 与持久化流程, 隔离后台网络和交易日探测。
    monkeypatch.setattr(service, "_poll_loop", lambda: None)
    monkeypatch.setattr(service, "_market_phase", lambda: "closed")
    app = FastAPI()
    app.include_router(settings_api.router)
    app.state.quote_service = service
    app.state.depth_service = None
    app.state.repo = SimpleNamespace(
        store=SimpleNamespace(data_dir=tmp_path),
        latest_daily_date=lambda: date(2026, 10, 28),
        latest_enriched_date=lambda: None,
    )
    with TestClient(app) as client:
        yield client, service, saved, app.state.repo
    service.stop()


def test_fuyao_enables_realtime(quote_api):
    client, service, saved, _ = quote_api

    response = client.put(ENDPOINT, json={"realtime_quotes_enabled": True})

    assert response.status_code == 200
    assert response.json() == {"realtime_quotes_enabled": True, "realtime_allowed": True}
    assert service.status()["enabled"] is True
    assert service.status()["running"] is True
    assert saved["realtime_quotes_enabled"] is True

    response = client.put(ENDPOINT, json={"realtime_quotes_enabled": False})
    assert response.status_code == 200
    assert service.status()["enabled"] is False
    assert service.status()["running"] is False
    assert saved["realtime_quotes_enabled"] is False


def test_fuyao_still_requires_local_data(quote_api):
    client, service, saved, repo = quote_api
    repo.latest_daily_date = lambda: None

    response = client.put(ENDPOINT, json={"realtime_quotes_enabled": True})

    assert response.status_code == 409
    assert "同步" in response.json()["detail"]
    assert service.status()["enabled"] is False
    assert saved["realtime_quotes_enabled"] is False


def test_fuyao_waits_for_history_repair(quote_api, monkeypatch):
    client, service, saved, _ = quote_api
    day = datetime.now(CN_TZ).date() - timedelta(days=1)
    monkeypatch.setattr(data_integrity, "scan_recent_integrity", lambda root: [
        data_integrity.IntegrityIssue(day, "kline_daily", "snapshot"),
    ])
    monkeypatch.setattr(
        data_integrity, "launch_integrity_repair",
        lambda *args, **kwargs: ("history-repair", True),
    )

    response = client.put(ENDPOINT, json={"realtime_quotes_enabled": True})

    assert response.status_code == 409
    assert "history-repair" in response.json()["detail"]
    assert service.status()["enabled"] is False
    assert saved["realtime_quotes_enabled"] is False
