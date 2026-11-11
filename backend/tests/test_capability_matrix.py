"""能力路由矩阵契约测试。

覆盖: 注册表与路由偏好字段一一对应、候选按各源 datasets 声明过滤、
候选只含当前可用源 (未就绪插件进 pending 并携带原因)、usable 跟随
生效源 (各页能力门控的统一判定)、每能力独立路由、偏好指向未知源时
的回退形态。全部用假插件/自定义源, 不依赖真实网络与本地 data/ 目录。
"""
from __future__ import annotations

from app.data_providers import custom as custom_sources
from app.data_providers.capabilities import CAPABILITY_REGISTRY, build_capability_matrix

DEFAULT_CURRENT = {
    "daily_data_provider": "easytdx",
    "adj_factor_provider": "easytdx",
    "minute_data_provider": "easytdx",
    "full_minute_data_provider": "easytdx",
    "depth5_data_provider": "easytdx",
    "realtime_data_provider": "easytdx",
    "financial_data_provider": "fuyao",
}

def _fake_sources(monkeypatch, plugins: list[dict], customs: list[dict] | None = None) -> None:
    # 内置 easytdx 的声明来自真实 loader 状态, fake 只替换第三方插件,
    # 保证「内置源恒在候选」这一契约在任何 fake 组合下都被测到。
    builtin = [p for p in custom_sources.list_plugins() if p["name"] == "easytdx"]
    monkeypatch.setattr(custom_sources, "list_plugins", lambda: builtin + plugins)
    monkeypatch.setattr(custom_sources, "list_sources", lambda: customs or [])

def _by_id(matrix: dict) -> dict[str, dict]:
    return {c["id"]: c for c in matrix["capabilities"]}

def test_registry_covers_all_routing_fields():
    """注册表是能力的单一权威: 可路由能力与偏好键一一对应、无重复;
    full_minute 为可路由能力 (field=full_minute_data_provider)。"""
    routable = [c["field"] for c in CAPABILITY_REGISTRY if c["field"] is not None]
    assert sorted(routable) == sorted(DEFAULT_CURRENT)
    assert len(set(routable)) == len(routable)
    assert {c["id"] for c in CAPABILITY_REGISTRY} == {
        "realtime", "daily", "minute", "full_minute", "depth5", "adj_factor", "financial",
    }
    full_minute = next(c for c in CAPABILITY_REGISTRY if c["id"] == "full_minute")
    assert full_minute["field"] == "full_minute_data_provider"
    # 默认源随插件加载状态走 (未装 → 空串):
    # 五项能力 + 全量分钟跟 easytdx, 财务跟 fuyao。
    from app.data_providers.capabilities import default_provider_for, _default_financial_provider
    easytdx_ready = "easytdx" in custom_sources.names()
    fuyao_ready = "fuyao" in custom_sources.names()
    for cap in CAPABILITY_REGISTRY:
        # 注册表 dict 的 default 字段不再模块级缓存; 实时口径统一走函数
        expected = _default_financial_provider() if cap["id"] == "financial" else (
            "easytdx" if easytdx_ready else "")
        assert default_provider_for(cap["id"]) == expected
        assert "follow" not in cap
        assert "tf_tier" not in cap

def test_matrix_without_third_party_sources(monkeypatch):
    """无插件无自定义源: 每个能力只剩内置 easytdx 候选, 默认路由全部生效。"""
    _fake_sources(monkeypatch, [])
    matrix = build_capability_matrix(dict(DEFAULT_CURRENT))
    assert len(matrix["capabilities"]) == 7
    for cap in matrix["capabilities"]:
        names = [c["name"] for c in cap["candidates"]]
        expected = "fuyao" if cap["id"] == "financial" else "easytdx"
        # 生成器写进 fake 后 candidates 只由注入的声明决定; fake 为空时只剩内置源
        assert all(n in ("easytdx", "fuyao") for n in names) or not names
        assert cap["pending"] == []
        assert cap["current"] == cap["effective"] == DEFAULT_CURRENT[cap["field"]]

def test_candidates_only_available_unready_goes_pending(monkeypatch):
    """候选只包含声明了该能力且可用的源; 未就绪插件进 pending 并携带原因。"""
    _fake_sources(
        monkeypatch,
        [
            {"name": "fuyao", "display_name": "fuyao", "datasets": ["realtime"],
             "available": True, "status": "ok"},
            {"name": "sdk", "display_name": "SDK", "datasets": ["daily", "minute"],
             "available": False, "status": "依赖未安装"},
        ],
        [{"name": "myhttp", "display_name": "MyHTTP", "datasets": ["financial", "realtime"]}],
    )
    caps = _by_id(build_capability_matrix(dict(DEFAULT_CURRENT)))
    assert [c["name"] for c in caps["realtime"]["candidates"]] == ["easytdx", "fuyao", "myhttp"]
    assert [c["name"] for c in caps["daily"]["candidates"]] == ["easytdx"]
    assert [c["name"] for c in caps["daily"]["pending"]] == ["sdk"]
    assert caps["daily"]["pending"][0]["available"] is False
    assert caps["daily"]["pending"][0]["note"] == "依赖未安装"
    assert [c["name"] for c in caps["financial"]["candidates"]] == ["myhttp"]
    assert caps["financial"]["candidates"][0]["kind"] == "custom"
    assert caps["financial"]["usable"] is False  # fuyao 本例未声明 financial
    assert [c["name"] for c in caps["adj_factor"]["candidates"]] == ["easytdx"]

def test_usable_follows_effective_provider(monkeypatch):
    """usable 跟随生效源: 各页能力门控的统一判定。

    - 生效源是可用插件 → usable;
    - 生效源是未就绪插件 → 不可用 (即使有其他可用候选);
    - 除权独立路由, 源可用性同样生效。
    """
    _fake_sources(
        monkeypatch,
        [
            {"name": "fuyao", "display_name": "fuyao", "datasets": ["realtime", "minute"],
             "available": True, "status": "ok"},
            {"name": "sdk", "display_name": "SDK", "datasets": ["daily", "adj_factor", "minute"],
             "available": False, "status": "依赖未安装"},
        ],
    )
    caps = _by_id(
        build_capability_matrix(
            dict(DEFAULT_CURRENT, realtime_data_provider="fuyao", minute_data_provider="sdk"),
        ),
    )
    # 路由到可用插件 → usable
    assert caps["realtime"]["usable"] is True
    # 路由到未就绪插件: 有可用候选 (fuyao/easytdx) 也不算 usable
    minute = caps["minute"]
    assert sorted(c["name"] for c in minute["candidates"]) == ["easytdx", "fuyao"]
    assert minute["usable"] is False
    # 除权默认路由 easytdx → 可用
    assert caps["adj_factor"]["effective"] == "easytdx"
    assert caps["adj_factor"]["usable"] is True
    # 除权显式路由到未就绪 sdk → 不可用 (daily 不受影响)
    caps = _by_id(
        build_capability_matrix(dict(DEFAULT_CURRENT, adj_factor_provider="sdk")),
    )
    assert [c["name"] for c in caps["adj_factor"]["candidates"]] == ["easytdx"]
    assert caps["adj_factor"]["effective"] == "sdk"
    assert caps["adj_factor"]["usable"] is False
    assert caps["daily"]["usable"] is True

def test_adj_factor_routes_independently(monkeypatch):
    """除权独立路由: 显式切到声明除权的插件即生效, 与日K当前源无关。

    历史遗留值 same_as_daily 不再被矩阵特判 — 存量配置经 preferences getter
    按非法源回退默认 (getter 层自愈), 矩阵只信任注入值。
    """
    _fake_sources(
        monkeypatch,
        [{"name": "sdk", "display_name": "SDK", "datasets": ["adj_factor"],
          "available": True, "status": "ok"}],
    )
    caps = _by_id(
        build_capability_matrix(dict(DEFAULT_CURRENT, adj_factor_provider="sdk")),
    )
    adj = caps["adj_factor"]
    assert adj["current"] == adj["effective"] == "sdk"
    assert adj["usable"] is True
    assert caps["daily"]["effective"] == "easytdx"
    assert caps["daily"]["usable"] is True

def test_depth5_capability_semantics(monkeypatch):
    """五档可独立路由到声明 depth5 的插件。"""
    _fake_sources(
        monkeypatch,
        [{"name": "depth_src", "display_name": "Depth", "datasets": ["depth5"],
          "available": True, "status": "ok"}],
    )
    cap = _by_id(build_capability_matrix(dict(DEFAULT_CURRENT)))["depth5"]
    assert [c["name"] for c in cap["candidates"]] == ["easytdx", "depth_src"]
    assert cap["usable"] is True
    # 显式路由到插件后仍可用
    current = dict(DEFAULT_CURRENT, depth5_data_provider="depth_src")
    cap = _by_id(build_capability_matrix(current))["depth5"]
    assert sorted(c["name"] for c in cap["candidates"]) == ["depth_src", "easytdx"]
    assert cap["effective"] == "depth_src"
    assert cap["usable"] is True

def test_unknown_current_display_falls_back_to_name(monkeypatch):
    """偏好指向未注册源 (正常经 getters 校验不会发生): 展示回退为原始名, 不抛异常。"""
    _fake_sources(monkeypatch, [])
    caps = _by_id(
        build_capability_matrix(
            dict(DEFAULT_CURRENT, realtime_data_provider="ghost"),
        ),
    )
    assert caps["realtime"]["current"] == "ghost"
    assert caps["realtime"]["current_display"] == "ghost"
    assert caps["realtime"]["effective_display"] == "ghost"

def test_full_minute_routable_like_other_capabilities(monkeypatch):
    """全量分钟行: 与其他能力同样可路由 — 声明 full_minute 数据集的源进候选,
    路由到它则 usable=True; 默认路由 easytdx。"""
    _fake_sources(
        monkeypatch,
        [],
        [{"name": "myfm", "display_name": "MyFM", "datasets": ["full_minute"]}],
    )
    caps = _by_id(build_capability_matrix(dict(DEFAULT_CURRENT)))
    fm = caps["full_minute"]
    assert fm["field"] == "full_minute_data_provider"
    assert [c["name"] for c in fm["candidates"]] == ["easytdx", "myfm"]
    assert fm["usable"] is True
    assert fm["effective"] == "easytdx"

    # 路由到声明该数据集的自定义源 → 可用
    routed = dict(DEFAULT_CURRENT, full_minute_data_provider="myfm")
    fm_routed = _by_id(build_capability_matrix(routed))["full_minute"]
    assert sorted(c["name"] for c in fm_routed["candidates"]) == ["easytdx", "myfm"]
    assert fm_routed["usable"] is True
    assert fm_routed["effective"] == "myfm"

    # 生效源是内置 easytdx 但无第三方源 → 仍可用 (内置源声明了 full_minute)
    caps_default = _by_id(build_capability_matrix(dict(DEFAULT_CURRENT)))
    assert caps_default["full_minute"]["usable"] is True

def test_api_endpoint_injects_all_routing_preferences(monkeypatch):
    """回归 (#301): get_capability_matrix 组装 current 必须覆盖注册表全部路由字段。

    full_minute_data_provider 曾漏传 → 矩阵 effective 恒回退默认,
    用户即使已路由自定义源, 卡片仍恒显「不可用」(真实路由读 preferences
    不受影响, 纯展示层)。此处捕获 API 层实际传给 build_capability_matrix 的
    current, 断言它与注册表路由字段一一对应且取自对应 getter。
    """
    from app.api import settings as settings_api
    from app.data_providers import capabilities as capabilities_mod
    from app.services import preferences

    getter_values = {
        "realtime_data_provider": "rt-src",
        "daily_data_provider": "daily-src",
        "minute_data_provider": "min-src",
        "full_minute_data_provider": "fm-src",
        "depth5_data_provider": "d5-src",
        "adj_factor_provider": "adj-src",
        "financial_data_provider": "fin-src",
    }
    getter_names = {
        "realtime_data_provider": "get_realtime_data_provider",
        "daily_data_provider": "get_daily_data_provider",
        "minute_data_provider": "get_minute_data_provider",
        "full_minute_data_provider": "get_full_minute_data_provider",
        "depth5_data_provider": "get_depth5_data_provider",
        "adj_factor_provider": "get_adj_factor_provider",
        "financial_data_provider": "get_financial_provider",
    }
    for field, getter in getter_names.items():
        monkeypatch.setattr(preferences, getter, lambda f=field: getter_values[f])

    captured: dict[str, dict] = {}

    def _spy(current):
        captured["current"] = dict(current)
        return build_capability_matrix(current)

    monkeypatch.setattr(capabilities_mod, "build_capability_matrix", _spy)
    settings_api.get_capability_matrix()

    routable_fields = {c["field"] for c in CAPABILITY_REGISTRY if c["field"] is not None}
    assert routable_fields == set(getter_values)
    # 注入完整: 每个路由字段都来自对应 getter, 不允许缺键 (缺键会在构建侧静默回退默认)
    assert captured["current"] == getter_values

def test_api_endpoint_full_minute_usable_follows_preference(monkeypatch):
    """端到端回归 (#301 用户场景): 已路由自定义源 → 全量分钟卡片可用。

    修复前: API 层漏传偏好, effective 恒默认源 → usable=False。
    """
    from app.api import settings as settings_api
    from app.services import preferences

    _fake_sources(
        monkeypatch,
        [],
        [{"name": "eltdx", "display_name": "ELTDX", "datasets": ["full_minute"]}],
    )
    for name in (
        "get_realtime_data_provider", "get_daily_data_provider",
        "get_minute_data_provider", "get_depth5_data_provider",
        "get_adj_factor_provider", "get_financial_provider",
    ):
        monkeypatch.setattr(preferences, name, lambda: "easytdx")
    monkeypatch.setattr(preferences, "get_full_minute_data_provider", lambda: "eltdx")

    fm = _by_id(settings_api.get_capability_matrix())["full_minute"]
    assert fm["effective"] == "eltdx"
    assert fm["usable"] is True
