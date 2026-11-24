"""告警记录查询入参边界 — days/limit 越界应被拦下, 合法值照常返回。

同类列表端点 (abnormal.py 的 limit、rps.py 的 days) 已用 Query(ge=..., le=...)
约束同名参数; 本文件锁住 /api/alerts 的同一口径。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.alerts import router
from app.services import alert_store


def _client(tmp_path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    return TestClient(app)


def _seed(tmp_path, count: int = 3) -> None:
    now_ms = int(time.time() * 1000)
    alert_store.append_many(
        tmp_path,
        [
            {"ts": now_ms - i * 1000, "rule_id": f"r{i}", "source": "monitor", "type": "price"}
            for i in range(count)
        ],
    )


@pytest.mark.parametrize("params", [{"days": -1}, {"days": 0}, {"limit": -1}, {"limit": 0}])
def test_out_of_range_params_are_rejected(tmp_path, params):
    _seed(tmp_path)
    resp = _client(tmp_path).get("/api/alerts", params=params)
    assert resp.status_code == 422, resp.text


def test_days_and_limit_above_store_retention_are_rejected(tmp_path):
    _seed(tmp_path)
    client = _client(tmp_path)
    # 存储侧只保留 MAX_DAYS 天 / MAX_RECORDS 条, 超出上限的请求没有可返回的数据
    assert client.get("/api/alerts", params={"days": alert_store.MAX_DAYS + 1}).status_code == 422
    assert client.get(
        "/api/alerts", params={"limit": alert_store.MAX_RECORDS + 1}
    ).status_code == 422


def test_valid_params_still_return_all_records(tmp_path):
    _seed(tmp_path, 3)
    client = _client(tmp_path)
    # 默认值
    body = client.get("/api/alerts").json()
    assert len(body["alerts"]) == 3
    assert body["total"] == 3
    # 显式边界值 (前端实际用的 days=7 / limit=1、10、500 均在内)
    for params in (
        {"days": 1, "limit": 1},
        {"days": 7, "limit": 500},
        {"days": alert_store.MAX_DAYS, "limit": alert_store.MAX_RECORDS},
    ):
        resp = client.get("/api/alerts", params=params)
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["alerts"]) == min(3, params["limit"])

def test_total_shares_the_same_window_as_the_rows(tmp_path):
    """总数与列表必须同一时间窗口 —— 否则页面按总数渲染条目、按列表渲染行,
    过期记录会让「32 条异动」下面一条都不显示。"""
    now_ms = int(time.time() * 1000)
    stale = now_ms - (alert_store.MAX_DAYS + 1) * 86400 * 1000
    alert_store.append_many(tmp_path, [
        {"ts": now_ms, "rule_id": "fresh", "source": "monitor", "type": "price"},
        {"ts": stale, "rule_id": "stale", "source": "monitor", "type": "price"},
    ])
    body = _client(tmp_path).get("/api/alerts", params={"days": 7}).json()
    assert [e["rule_id"] for e in body["alerts"]] == ["fresh"]
    assert body["total"] == 1
