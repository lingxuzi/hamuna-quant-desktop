"""个股详情 K 线传输压缩与分钟源能力门控回归测试。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import indices, kline
from app.data_providers.capabilities import Cap, CapabilityLimits, CapabilitySet

_SYMBOL = "600000.SH"
_TRADE_DATE = date(2026, 1, 15)


@pytest.fixture(autouse=True)
def _isolated_settings_and_clock(monkeypatch):
    monkeypatch.setattr("app.services.preferences.load", lambda: {"minute_data_provider": "tickflow"})
    monkeypatch.setattr("app.services.preferences.get_daily_batch_compress", lambda: True)
    monkeypatch.setattr("app.services.preferences.get_minute_batch_compress", lambda: True)
    monkeypatch.setattr(kline, "cn_today", lambda: _TRADE_DATE)
    monkeypatch.setattr(kline, "cn_now", lambda: datetime(2026, 1, 15, 10, 30))
    monkeypatch.setattr(kline, "in_continuous_session", lambda: True)


def _minute_rows(count: int = 240) -> pl.DataFrame:
    start = datetime(2026, 1, 15, 9, 30)
    return pl.DataFrame(
        {
            "symbol": [_SYMBOL] * count,
            "datetime": [start + timedelta(minutes=i) for i in range(count)],
            "open": [10.0] * count,
            "high": [10.1] * count,
            "low": [9.9] * count,
            "close": [10.05] * count,
            "volume": [1_000.0] * count,
            "amount": [10_050.0] * count,
        }
    )


class _DetailRepo:
    def __init__(self, minute: pl.DataFrame | None = None) -> None:
        self.minute = minute if minute is not None else _minute_rows()
        # /minute 读取 repo.store.data_dir 判断分钟基准标记 (无标记即旧行为)
        import tempfile
        from types import SimpleNamespace
        from pathlib import Path
        self.store = SimpleNamespace(data_dir=Path(tempfile.mkdtemp()))

    def resolve_asset_type(self, symbol: str) -> str:
        return "stock"

    def get_instruments(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "symbol": [_SYMBOL],
                "name": ["浦发银行"],
                "total_shares": [1.0],
                "float_shares": [1.0],
            }
        )

    def get_daily_asset(self, asset_type, symbol, start, end, columns=None):
        frame = pl.DataFrame(
            {
                "symbol": [_SYMBOL] * 30,
                "date": [date(2025, 12, 15) + timedelta(days=i) for i in range(30)],
                "open": [10.0] * 30,
                "high": [10.2] * 30,
                "low": [9.8] * 30,
                "close": [10.1] * 30,
                "volume": [1_000.0] * 30,
                "amount": [10_100.0] * 30,
                "ma5": [10.0] * 30,
                "ma10": [10.0] * 30,
                "ma20": [10.0] * 30,
            }
        )
        return frame.select(columns) if columns else frame

    def get_minute(self, symbol, trade_date, asset_type="stock") -> pl.DataFrame:
        return self.minute

    def get_minute_range(self, symbols, start, end, asset_type="stock") -> pl.DataFrame:
        return self.minute


class _IndexRepo:
    def get_index_instruments(self) -> pl.DataFrame:
        return pl.DataFrame({"symbol": ["000001.SH"], "name": ["上证指数"]})


def _client(repo, capset: CapabilitySet | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(kline.router)
    app.include_router(indices.router)
    app.state.repo = repo
    app.state.capabilities = capset or CapabilitySet()
    app.state.quote_service = None
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "daily_compress", "minute_compress"),
    [
        (f"/api/kline/daily?symbol={_SYMBOL}&days=120", True, False),
        (f"/api/kline/minute?symbol={_SYMBOL}&date={_TRADE_DATE}", False, True),
        (f"/api/kline/minute-range?symbol={_SYMBOL}&days=10", False, True),
    ],
)
def test_detail_kline_responses_use_configured_gzip(
    monkeypatch,
    path,
    daily_compress,
    minute_compress,
):
    monkeypatch.setattr(
        "app.services.preferences.get_daily_batch_compress",
        lambda: daily_compress,
    )
    monkeypatch.setattr(
        "app.services.preferences.get_minute_batch_compress",
        lambda: minute_compress,
    )

    response = _client(_DetailRepo()).get(
        path,
        headers={"Accept-Encoding": "gzip"},
    )

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["symbol"] == _SYMBOL
    plain = _client(_DetailRepo()).get(path, headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in plain.headers
    assert response.json() == plain.json()
    assert int(response.headers["content-length"]) < len(plain.content)

    monkeypatch.setattr("app.services.preferences.get_daily_batch_compress", lambda: False)
    monkeypatch.setattr("app.services.preferences.get_minute_batch_compress", lambda: False)
    disabled = _client(_DetailRepo()).get(path, headers={"Accept-Encoding": "gzip"})
    assert "content-encoding" not in disabled.headers
    assert disabled.json() == plain.json()


@pytest.mark.parametrize("live", [False, True])
def test_unresolvable_minute_source_fails_closed(monkeypatch, live):
    """生效分钟源无法解析时 fail-closed: 不跨源取数, 响应 source=none。"""
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: "missing_src")
    monkeypatch.setattr("app.services.preferences.load", lambda: {"minute_data_provider": "missing_src"})

    response = _client(_DetailRepo(pl.DataFrame())).get(
        "/api/kline/minute",
        params={"symbol": _SYMBOL, "date": str(_TRADE_DATE), "live": live},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "none"


def test_custom_minute_source_remains_available_without_capability_grant(monkeypatch):
    """源可解析即取数: 运行时能力集未授予分钟能力也不影响生效源自己的取数。"""
    provider = MagicMock()
    provider.config.datasets = {"minute": object()}
    provider.get_minute.return_value = _minute_rows(1)
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: "custom")
    monkeypatch.setattr("app.services.preferences.load", lambda: {"minute_data_provider": "custom"})
    monkeypatch.setattr("app.data_providers.custom.get_provider", lambda name: provider)

    response = _client(_DetailRepo(pl.DataFrame())).get(
        "/api/kline/minute",
        params={"symbol": _SYMBOL, "date": str(_TRADE_DATE)},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "live"
    provider.get_minute.assert_called_once()


def test_failed_custom_source_does_not_fall_back_to_another_source(monkeypatch):
    """生效源请求失败时 fail-closed: 不跨源补数, 响应 source=none。"""
    provider = MagicMock()
    provider.config.datasets = {"minute": object()}
    provider.get_minute.side_effect = RuntimeError("custom source unavailable")
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: "custom")
    monkeypatch.setattr("app.services.preferences.load", lambda: {"minute_data_provider": "custom"})
    monkeypatch.setattr("app.data_providers.custom.get_provider", lambda name: provider)

    capset = CapabilitySet({Cap.KLINE_MINUTE_BATCH: CapabilityLimits()})
    response = _client(_DetailRepo(pl.DataFrame()), capset).get(
        "/api/kline/minute",
        params={"symbol": _SYMBOL, "date": str(_TRADE_DATE)},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "none"


def test_gzip_payload_strips_nonfinite_floats() -> None:
    """gzip 路径不得写出 NaN/Infinity: 前端 JSON.parse 会直接炸掉。

    停牌/坏源/指标暖机窗口会出现 nan 或 inf。Python json.dumps 默认 allow_nan=True,
    压缩路径会把非法 JSON 词写进 gzip 体; 未压缩路径走 Starlette allow_nan=False, 整段 500。
    助手分时小图已经按同样口径清洗 (test_assistant_intraday_chart_finite)。
    """
    import gzip as gz
    import json
    from types import SimpleNamespace

    from fastapi.responses import Response

    req = SimpleNamespace(headers={"accept-encoding": "gzip"})
    payload = {
        "symbol": _SYMBOL,
        # 压缩路径只在 JSON 超过 1024 字节时启用
        "pad": "x" * 1200,
        "rows": [
            {"close": float("nan")},
            {"close": float("inf")},
            {"close": 10.5},
        ],
    }
    result = kline._gzip_payload(req, payload, pref_key="minute_batch_compress")
    assert isinstance(result, Response)
    text = gz.decompress(result.body).decode()
    json.dumps(json.loads(text), allow_nan=False)
    data = json.loads(text)
    assert data["rows"][0]["close"] is None
    assert data["rows"][1]["close"] is None
    assert data["rows"][2]["close"] == 10.5


def test_uncompressed_payload_strips_nonfinite_floats() -> None:
    """未压缩路径同样清洗, 避免 Starlette JSONResponse allow_nan=False 整段 500。"""
    import json
    from types import SimpleNamespace

    req = SimpleNamespace(headers={})
    payload = {"rows": [{"close": float("nan")}, {"close": float("-inf")}]}
    result = kline._gzip_payload(req, payload, pref_key="daily_batch_compress")
    assert isinstance(result, dict)
    json.dumps(result, allow_nan=False)
    assert result["rows"][0]["close"] is None
    assert result["rows"][1]["close"] is None



def test_capability_grant_alone_does_not_bypass_source_resolution(monkeypatch):
    """能力集授予分钟能力也不能凭空取数: 源不可解析仍 fail-closed。"""
    capset = CapabilitySet({Cap.KLINE_MINUTE_BY_SYMBOL: CapabilityLimits()})
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: "missing_src")
    monkeypatch.setattr("app.services.preferences.load", lambda: {"minute_data_provider": "missing_src"})

    response = _client(_DetailRepo(pl.DataFrame()), capset).get(
        "/api/kline/minute",
        params={"symbol": _SYMBOL, "date": str(_TRADE_DATE)},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "none"


def test_index_minute_fails_closed_on_unresolvable_source(monkeypatch):
    """指数分时同样 fail-closed: 源不可解析时 source=none, 不跨源。"""
    monkeypatch.setattr("app.services.preferences.get_minute_data_provider", lambda: "missing_src")
    monkeypatch.setattr("app.services.preferences.load", lambda: {"minute_data_provider": "missing_src"})

    response = _client(_IndexRepo()).get(
        "/api/index/minute",
        params={"symbol": "000001.SH", "date": str(_TRADE_DATE)},
    )

    assert response.status_code == 200
    assert response.json()["source"] == "none"


def test_gzip_payload_fast_path_skips_deep_copy() -> None:
    """常态 (无非有限浮点) 不做 _json_safe 整树深拷贝。

    深拷贝是批量响应峰值最大的一份瞬态分配 (与原 payload 同量级),
    常态跳过后峰值约减半; 本测试锁定"只有遇到 NaN/inf 才允许净化"的契约。
    """
    import gzip as gz
    import json
    from types import SimpleNamespace
    from unittest.mock import patch

    from fastapi.responses import Response

    req = SimpleNamespace(headers={"accept-encoding": "gzip"})
    payload = {"pad": "x" * 1200, "rows": [{"close": 10.5}, {"close": 11.0}]}
    with patch.object(kline, "_json_safe", side_effect=AssertionError("常态不应触发深拷贝")):
        result = kline._gzip_payload(req, payload, pref_key="minute_batch_compress")
    assert isinstance(result, Response)
    data = json.loads(gz.decompress(result.body))
    assert data["rows"] == [{"close": 10.5}, {"close": 11.0}]


def test_gzip_payload_sanitizes_exactly_once_on_nonfinite() -> None:
    """遇 NaN/inf 时回退净化且只净化一次 (dumps 重试不再走 _json_safe)。"""
    import gzip as gz
    import json
    from types import SimpleNamespace
    from unittest.mock import patch

    from fastapi.responses import Response

    req = SimpleNamespace(headers={"accept-encoding": "gzip"})
    payload = {"pad": "x" * 1200, "rows": [{"close": float("nan")}, {"close": 10.5}]}
    # _json_safe 递归自调用也走模块属性, 只统计根调用 (obj is payload)
    calls: list[int] = []
    real = kline._json_safe

    def counting(obj):
        if obj is payload:
            calls.append(1)
        return real(obj)

    with patch.object(kline, "_json_safe", side_effect=counting):
        result = kline._gzip_payload(req, payload, pref_key="daily_batch_compress")
    assert isinstance(result, Response)
    assert calls == [1]
    data = json.loads(gz.decompress(result.body))
    assert data["rows"][0]["close"] is None
    assert data["rows"][1]["close"] == 10.5


def test_gzip_payload_tiny_nonfinite_returns_sanitized_dict() -> None:
    """未达压缩阈值 (<1KB) 且含 NaN: 返回净化后的 dict, 不触发 Starlette 500。"""
    import json
    from types import SimpleNamespace

    req = SimpleNamespace(headers={"accept-encoding": "gzip"})
    payload = {"rows": [{"close": float("nan")}]}
    result = kline._gzip_payload(req, payload, pref_key="minute_batch_compress")
    assert isinstance(result, dict)
    json.dumps(result, allow_nan=False)
    assert result["rows"][0]["close"] is None
