"""数据源偏好回退 — 旧配置自愈 + 告警不刷屏。

两条要守住的线:
  1. 已下线的源 (旧版本留下的 'tickflow') 写回默认源 —— 偏好文件收敛,
     之后每次 getter 直接命中, 不再走回退路径;
  2. 仍在但此刻不可用的源 (插件依赖没装) 保留用户偏好 —— 依赖装好即恢复。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from app.services import preferences


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 preferences.json 指到临时文件, 并清掉进程内缓存与告警记忆。"""
    path = tmp_path / "preferences.json"
    path.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(preferences, "_path", lambda: path)
    monkeypatch.setattr(preferences, "_cache", None)
    monkeypatch.setattr(preferences, "_cache_sig", None)
    monkeypatch.setattr(preferences, "_warned_invalid", set())
    return path


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_retired_provider_is_written_back_to_default(store, monkeypatch):
    _write(store, {"minute_data_provider": "tickflow"})
    monkeypatch.setattr(preferences, "_allowed_data_providers", lambda: {"easytdx", "fuyao"})
    monkeypatch.setattr(preferences, "_known_data_providers", lambda: {"easytdx", "fuyao"})
    monkeypatch.setattr(preferences, "_default_provider", lambda: "easytdx")

    assert preferences.get_minute_data_provider() == "easytdx"
    assert _read(store)["minute_data_provider"] == "easytdx"


def test_retired_provider_warns_only_once(store, monkeypatch, caplog):
    _write(store, {"minute_data_provider": "tickflow"})
    monkeypatch.setattr(preferences, "_allowed_data_providers", lambda: {"easytdx"})
    monkeypatch.setattr(preferences, "_known_data_providers", lambda: {"easytdx"})
    monkeypatch.setattr(preferences, "_default_provider", lambda: "easytdx")

    with caplog.at_level(logging.WARNING, logger="app.services.preferences"):
        assert preferences.get_minute_data_provider() == "easytdx"
        assert preferences.get_minute_data_provider() == "easytdx"
        assert preferences.get_minute_data_provider() == "easytdx"
    warns = [r for r in caplog.records if "minute_data_provider" in r.getMessage()]
    assert len(warns) == 1


def test_unavailable_but_known_provider_keeps_preference(store, monkeypatch):
    """插件依赖没装 → 回退可用源, 但不覆盖用户的显式选择。"""
    _write(store, {"minute_data_provider": "fuyao"})
    monkeypatch.setattr(preferences, "_allowed_data_providers", lambda: {"easytdx"})
    monkeypatch.setattr(preferences, "_known_data_providers", lambda: {"easytdx", "fuyao"})
    monkeypatch.setattr(preferences, "_default_provider", lambda: "easytdx")

    assert preferences.get_minute_data_provider() == "easytdx"
    assert _read(store)["minute_data_provider"] == "fuyao"


def test_no_available_source_does_not_warn(store, monkeypatch, caplog):
    """插件全没装 → 偏好为空, 不是非法值, 不该刷告警。"""
    _write(store, {})
    monkeypatch.setattr(preferences, "_allowed_data_providers", lambda: set())
    monkeypatch.setattr(preferences, "_known_data_providers", lambda: set())
    monkeypatch.setattr(preferences, "_default_provider", lambda: "")

    with caplog.at_level(logging.WARNING, logger="app.services.preferences"):
        assert preferences.get_minute_data_provider() == ""
    assert not [r for r in caplog.records if "minute_data_provider" in r.getMessage()]


def test_valid_provider_passes_through(store, monkeypatch):
    _write(store, {"minute_data_provider": "fuyao"})
    monkeypatch.setattr(preferences, "_allowed_data_providers", lambda: {"easytdx", "fuyao"})
    monkeypatch.setattr(preferences, "_known_data_providers", lambda: {"easytdx", "fuyao"})
    monkeypatch.setattr(preferences, "_default_provider", lambda: "easytdx")

    assert preferences.get_minute_data_provider() == "fuyao"
    assert _read(store)["minute_data_provider"] == "fuyao"
