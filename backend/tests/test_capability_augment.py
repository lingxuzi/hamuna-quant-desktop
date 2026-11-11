"""能力标准统一: 数据集→能力映射回归测试 (detect_capabilities 的路由判定)。

生效源声明了某数据集且可用 → grant 对应能力; 取数路由仍按 preferences 分流。
"""
from __future__ import annotations

from app.data_providers import custom as custom_sources
from app.data_providers.capabilities import Cap, detect_capabilities

def _fake_sources(monkeypatch, name: str, datasets: set[str], *, available: bool = True) -> None:
    """注入一个假插件源 (覆盖内置源声明), 路由全部指向它。"""
    def _plugins():
        return [
            {"name": "easytdx", "display_name": "easytdx",
             "datasets": sorted(ds for ds in ("daily", "adj_factor", "realtime", "minute", "depth5", "full_minute") if ds in datasets),
             "available": available, "status": "ok"},
            {"name": name, "display_name": name, "datasets": sorted(datasets),
             "available": available, "status": "ok"},
        ]
    monkeypatch.setattr(custom_sources, "list_plugins", _plugins)
    monkeypatch.setattr(custom_sources, "list_sources", lambda: [])

def _route_all(monkeypatch, name: str) -> None:
    from app.services import preferences
    monkeypatch.setattr(preferences, "get_daily_data_provider", lambda: name)
    monkeypatch.setattr(preferences, "get_adj_factor_provider", lambda: name)
    monkeypatch.setattr(preferences, "get_minute_data_provider", lambda: name)
    monkeypatch.setattr(preferences, "get_depth5_data_provider", lambda: name)
    monkeypatch.setattr(preferences, "get_financial_provider", lambda: name)
    monkeypatch.setattr(preferences, "get_full_minute_data_provider", lambda: name)

def test_daily_custom_source_grants_daily_batch(monkeypatch):
    _fake_sources(monkeypatch, "mock_src", {"daily"})
    _route_all(monkeypatch, "mock_src")
    capset = detect_capabilities()
    assert capset.has(Cap.KLINE_DAILY_BATCH)
    # 未声明其他数据集 → 不补
    assert not capset.has(Cap.ADJ_FACTOR)
    assert not capset.has(Cap.KLINE_MINUTE_BATCH)
    assert not capset.has(Cap.FINANCIAL)

def test_adj_custom_source_grants_adj_factor(monkeypatch):
    """adj 显式路由到声明除权的自定义源 → 授予能力 (独立判定)。"""
    _fake_sources(monkeypatch, "mock_src", {"adj_factor"})
    _route_all(monkeypatch, "mock_src")
    assert detect_capabilities().has(Cap.ADJ_FACTOR)

def test_full_minute_custom_source_grants_intraday_universe(monkeypatch):
    """全量分钟: 声明 full_minute 数据集且被路由 → 授予 INTRADAY_UNIVERSE,
    minute_refresh 服务门控与该口径统一。"""
    _fake_sources(monkeypatch, "mock_src", {"full_minute"})
    _route_all(monkeypatch, "mock_src")
    capset = detect_capabilities()
    assert capset.has(Cap.INTRADAY_UNIVERSE)
    # 声明了 full_minute 不等于声明 minute → 不补逐标的分钟K能力
    assert not capset.has(Cap.KLINE_MINUTE_BATCH)

def test_full_minute_dataset_without_routing_not_granted(monkeypatch):
    """路由到声明了 full_minute 但不可用的源 → 不授予。"""
    _fake_sources(monkeypatch, "mock_src", {"full_minute"}, available=False)
    _route_all(monkeypatch, "mock_src")
    assert not detect_capabilities().has(Cap.INTRADAY_UNIVERSE)

def test_unavailable_source_not_granted(monkeypatch):
    """生效源声明了数据集但当前不可用 → 不授予。"""
    _fake_sources(monkeypatch, "mock_src", {"daily"}, available=False)
    _route_all(monkeypatch, "mock_src")
    assert not detect_capabilities().has(Cap.KLINE_DAILY_BATCH)

def test_minute_custom_source_grants_minute_batch(monkeypatch):
    _fake_sources(monkeypatch, "mock_src", {"minute"})
    _route_all(monkeypatch, "mock_src")
    assert detect_capabilities().has(Cap.KLINE_MINUTE_BATCH)

def test_depth5_custom_source_grants_depth_batch(monkeypatch):
    """五档独立路由到声明 depth5 的自定义源时授予批量五档能力。"""
    _fake_sources(monkeypatch, "mock_src", {"depth5"})
    _route_all(monkeypatch, "mock_src")
    assert detect_capabilities().has(Cap.DEPTH5_BATCH)

def test_financial_custom_source_grants_financial(monkeypatch):
    _fake_sources(monkeypatch, "mock_src", {"financial"})
    _route_all(monkeypatch, "mock_src")
    assert detect_capabilities().has(Cap.FINANCIAL)

def test_provider_active_but_dataset_not_declared_no_grant(monkeypatch):
    """provider 被选为当前源但未声明该数据集 → 不 grant。"""
    _fake_sources(monkeypatch, "mock_src", set())
    _route_all(monkeypatch, "mock_src")
    capset = detect_capabilities()
    assert not capset.has(Cap.KLINE_DAILY_BATCH)
    assert not capset.has(Cap.ADJ_FACTOR)
    assert not capset.has(Cap.KLINE_MINUTE_BATCH)
    assert not capset.has(Cap.FINANCIAL)
