"""回归测试: 实时行情模式判定 — 生效源是否提供 realtime 能力。

档位体系 (TickFlow 免费/付费) 已下线: 实时模式只看「生效源当前能否提供
realtime 数据集」。生效源不提供 → none; 提供 → 全市场实时。
锁定判定结果, 防止跨源回退被无意恢复。
"""
from __future__ import annotations

from app.services.quote_service import QuoteService


def _source(monkeypatch, name: str, datasets: tuple[str, ...], *, available: bool = True) -> None:
    """打桩数据源目录: 只暴露一个声明了 datasets 的源, 并把它设为实时生效源。"""
    from app.data_providers import custom as custom_sources
    from app.services import preferences

    monkeypatch.setattr(custom_sources, "list_plugins", lambda: [{
        "name": name,
        "display_name": name,
        "datasets": list(datasets),
        "available": available,
        "status": "ok" if available else "不可用",
    }])
    monkeypatch.setattr(custom_sources, "list_sources", lambda: [])
    monkeypatch.setattr(preferences, "get_realtime_data_provider", lambda: name)


def test_custom_realtime_source_is_full_market(monkeypatch):
    """自定义实时源(如 fuyao)声明 realtime → 全市场实时。"""
    _source(monkeypatch, "fuyao", ("realtime", "daily"))
    assert QuoteService.realtime_mode() == "full_market"


def test_source_without_realtime_dataset_has_no_realtime(monkeypatch):
    """生效源不提供 realtime → 无实时 (不跨源回退, 由设置页提示换源)。"""
    _source(monkeypatch, "daily_only", ("daily",))
    assert QuoteService.realtime_mode() == "none"
    assert QuoteService.is_realtime_allowed() is False


def test_builtin_easytdx_is_full_market(monkeypatch):
    """内置源 easytdx 提供 realtime → 全市场实时。"""
    _source(monkeypatch, "easytdx", ("realtime", "daily", "minute"))
    assert QuoteService.realtime_mode() == "full_market"
