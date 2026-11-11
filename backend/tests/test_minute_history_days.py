"""分钟源历史深度能力 (minute_history_days) 契约测试。

provider 可选类属性 minute_history_days 声明 1 分钟历史深度(交易日):
- 浅源可声明如 5 (免费分时接口仅保留最近 5 个交易日)
- 未声明 / 源不可用 → None (深历史)
preferences GET 带出该字段, 前端分时档位据此收窄 (浅源默认 5日, 深源默认 20日)。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.api import settings
from app.services import preferences


def _mock_resolver(monkeypatch, provider, err=None):
    """resolver 契约: (provider, error_msg) —— 源不可用时 provider=None。"""
    monkeypatch.setattr(
        "app.services.kline_sync._resolve_minute_provider",
        lambda name: (provider, err),
    )
    # 存量值与生效值一致, 避免被判成「源已失效」而 fail-closed。
    monkeypatch.setattr(preferences, "load", lambda: {"minute_data_provider": "src"})


def test_history_days_from_custom_provider(monkeypatch):
    """自定义浅源 → 声明值; 前端据此只显示 1/5 日档。"""
    _mock_resolver(monkeypatch, SimpleNamespace(minute_history_days=5))
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "shallow_demo")
    assert settings._minute_history_days() == 5


def test_history_days_none_for_undeclared_provider(monkeypatch):
    """未声明的自定义源 → None (深历史基准)。"""
    _mock_resolver(monkeypatch, SimpleNamespace())
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "my_source")
    assert settings._minute_history_days() is None


def test_history_days_none_when_source_unavailable(monkeypatch):
    """源不可解析 → None (深历史基准)。"""
    _mock_resolver(monkeypatch, None)
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "missing_src")
    assert settings._minute_history_days() is None


def test_history_days_none_when_resolver_fails(monkeypatch):
    """resolver 异常 (registry 损坏) → 降级 None, 不抛 500。"""
    _mock_resolver(monkeypatch, None, err="registry broken")
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: "shallow_demo")
    assert settings._minute_history_days() is None


def test_preferences_get_includes_history_days(monkeypatch):
    """GET /preferences 响应包含 minute_history_days 字段。"""
    _mock_resolver(monkeypatch, SimpleNamespace(minute_history_days=5))
    payload = settings.get_preferences()
    assert payload["minute_history_days"] == 5
    assert "minute_data_provider" in payload
