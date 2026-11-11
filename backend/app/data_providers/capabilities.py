"""能力注册表与能力路由矩阵 — 数据集维度的单一权威定义。

能力 (capability) = 一个标准化数据集 (CONTRIBUTING「数据源插件化要求」):
daily / adj_factor / realtime / minute / depth5 / financial / full_minute
(注册表顺序即设置页卡片顺序)。注册表集中声明每个能力的展示元数据与路由
偏好字段, 前端设置页不再各自硬编码。
depth5 与其他数据集一样可由插件声明并独立路由; 五档不可用时连板梯队封单/
看板封单通过 usable 给出缺数据提示。

build_capability_matrix 把注册表、插件/自定义源的能力声明 (datasets) 和当前
路由偏好合并为一个矩阵, 供设置页一次拉全。当前偏好由 API 层注入
(preferences getters 自带合法源校验), 本模块不反向依赖 services 层。

候选契约: 每个能力的 candidates 只包含「当前确实可提供该能力」的源,
未就绪的插件/自定义源 (依赖未装/Key 未配) 放入 pending 并携带原因,
供前端置灰提示。其他页面可以把 candidates 直接当作可用提供方名单。

usable 契约: 每个能力额外给出 usable = 生效源当前能否真正提供该能力
(生效源在 candidates 中)。各页面的能力门控 (缺能力提示 → 数据源配置)
统一以 usable 为准, 而不是「套餐」视角 — 路由到可用插件即可用。

运行时能力集 (CapabilitySet) 与本模块同处一文件: 它描述「某个操作当前
能不能做」, 与数据集注册表是同一件事的两个视角。CapabilitySet 由
detect_capabilities() 本地计算 —— 生效源声明了某数据集且可用, 就授予
对应的运行时能力键。业务代码只读 CapabilitySet, 不读本模块。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.data_providers import registry

# ---------------------------------------------------------------------------
# 运行时能力模型
# ---------------------------------------------------------------------------

class Cap(StrEnum):
    """所有 capability 的命名常量。新增能力时只在这里加一行。"""

    QUOTE_BY_SYMBOL        = "quote.by_symbol"
    QUOTE_BATCH            = "quote.batch"
    QUOTE_POOL             = "quote.pool"
    KLINE_DAILY_BY_SYMBOL  = "kline.daily.by_symbol"
    KLINE_DAILY_BATCH      = "kline.daily.batch"
    KLINE_MINUTE_BY_SYMBOL = "kline.minute.by_symbol"
    KLINE_MINUTE_BATCH     = "kline.minute.batch"
    INTRADAY               = "intraday"
    INTRADAY_BATCH         = "intraday.batch"
    INTRADAY_UNIVERSE      = "intraday.universe"
    DEPTH5                 = "depth5"
    DEPTH5_BATCH           = "depth5.batch"
    WEBSOCKET              = "websocket"
    FINANCIAL              = "financial"
    ADJ_FACTOR             = "adj_factor"


@dataclass(slots=True, frozen=True)
class CapabilityLimits:
    """单个 capability 的运行时限制。"""

    rpm: int | None = None        # 次/分钟,None 表示未知或不限
    batch: int | None = None      # 标的/次
    subscribe: int | None = None  # WS 订阅上限


class CapabilitySet:
    """当前可用的运行时能力。业务代码的唯一真理源。"""

    def __init__(self, caps: dict[Cap, CapabilityLimits] | None = None) -> None:
        self._caps: dict[Cap, CapabilityLimits] = dict(caps or {})

    def has(self, cap: Cap) -> bool:
        return cap in self._caps

    def limits(self, cap: Cap) -> CapabilityLimits | None:
        return self._caps.get(cap)

    def require(self, cap: Cap) -> CapabilityLimits:
        """断言可用,否则抛 CapabilityDenied。"""
        if cap not in self._caps:
            raise CapabilityDenied(cap)
        return self._caps[cap]

    def all(self) -> dict[Cap, CapabilityLimits]:
        return dict(self._caps)

    def grant(self, cap: Cap, limits: CapabilityLimits | None = None) -> None:
        """补授一个能力 (不覆盖已有)。"""
        if cap not in self._caps:
            self._caps[cap] = limits or CapabilityLimits()

    def to_dict(self) -> dict[str, dict]:
        return {
            str(cap): {
                "rpm": lim.rpm,
                "batch": lim.batch,
                "subscribe": lim.subscribe,
            }
            for cap, lim in self._caps.items()
        }


class CapabilityDenied(Exception):
    """请求的 capability 当前不可用。"""

    def __init__(self, cap: Cap, suggestion: str | None = None) -> None:
        self.cap = cap
        self.suggestion = suggestion or f"当前数据源未提供『{cap}』能力"
        super().__init__(f"capability not available: {cap}; {self.suggestion}")


# ---------------------------------------------------------------------------
# 数据集注册表 (能力路由矩阵的真相源)
# ---------------------------------------------------------------------------

def _default_provider() -> str:
    """默认源 = easytdx 内置源可用则 easytdx, 否则空串。

    必须与 preferences._default_provider() 同口径: 注册表是能力矩阵
    展示的唯一真相源, 两处不一致会让「默认」列显示一个当前装不上的源。
    惰性导入: 本模块被 custom/__init__ 导入链引用, 顶层 import custom
    会造成循环初始化。
    """
    from app.data_providers import custom as _custom
    return "easytdx" if "easytdx" in _custom.names() else ""


# 五项能力跟随 easytdx (主力源); 财务跟随 fuyao (见 _default_financial_provider)。
_EASYTDX_ROUTED = frozenset({"daily", "adj_factor", "realtime", "minute", "depth5"})


def _default_financial_provider() -> str:
    """财务默认源 = fuyao 插件已加载则 fuyao, 否则空串。

    与 preferences._default_financial_provider() 同口径。硬编码别的源会让
    「配了 Key 却全程显示能力不可用」, 用户必须手动点一次才生效。
    """
    from app.data_providers import custom as _custom
    return "fuyao" if "fuyao" in _custom.names() else ""


def _default_full_minute_provider() -> str:
    """全量分钟默认源 = easytdx 内置源可用则 easytdx, 否则空串。

    与 preferences._default_full_minute_provider() 同口径。easytdx 逐标的拉
    当日 1m (全市场一轮约 13 分钟), 仅在盘中刷新开关打开时才跑, 不会因
    默认值变化自动产生负载。
    """
    from app.data_providers import custom as _custom
    return "easytdx" if "easytdx" in _custom.names() else ""


def _default_for(cap_id: str) -> str:
    if cap_id in _EASYTDX_ROUTED:
        return _default_provider()
    if cap_id == "financial":
        return _default_financial_provider()
    if cap_id == "full_minute":
        return _default_full_minute_provider()
    return ""


def default_for_field(field: str) -> str:
    """偏好字段 → 该能力的默认源; 字段不存在或无可用源时返回空串。

    设置页在「卸载插件 / 删除源」后用它回退偏好 —— 注册表是默认值的唯一
    真相源, 别处硬编码源名会让两处不一致。
    """
    for cap in CAPABILITY_REGISTRY:
        if cap.get("field") == field:
            return _default_for(cap["id"])
    return ""


def default_provider_for(dataset: str) -> str:
    """数据集 id → 该数据集的默认源; 无默认源时返回空串。

    数据集 id 与注册表的 cap id 同名 (daily/adj_factor/...), 直接查注册表,
    不另建一份映射。
    """
    for cap in CAPABILITY_REGISTRY:
        if cap.get("id") == dataset:
            return _default_for(cap["id"])
    return ""


CAPABILITY_REGISTRY: list[dict] = [
    {
        "id": "daily",
        "label": "日K",
        "desc": "历史K线与实时覆写",
        "field": "daily_data_provider",
        "default": "",
    },
    {
        "id": "adj_factor",
        "label": "除权因子",
        "desc": "前复权计算基准",
        "field": "adj_factor_provider",
        "default": "",
        # 独立路由 (曾经的「跟随日K」特殊值已下线: 每个能力单独配置,
        # 复权口径一致性改由未来的一致性警示保障, 不做路由耦合)
    },
    {
        "id": "realtime",
        "label": "实时行情",
        "desc": "全市场实时快照",
        "field": "realtime_data_provider",
        "default": "",
    },
    {
        "id": "minute",
        "label": "分钟K",
        "desc": "分时图与分钟回测",
        "field": "minute_data_provider",
        "default": "",
    },
    {
        "id": "depth5",
        "label": "五档盘口",
        "desc": "连板梯队封单与盘口深度",
        "field": "depth5_data_provider",
        "default": "",
    },
    {
        "id": "financial",
        "label": "财务数据",
        "desc": "财务指标与三大报表",
        "field": "financial_data_provider",
        "default": "",
    },
    {
        "id": "full_minute",
        "label": "全量分钟",
        "desc": "盘中全市场当日分钟落盘 (冷启动全天 + 标的池增量)",
        "field": "full_minute_data_provider",
        "default": "",
        # 插件/自定义源声明 full_minute 数据集即可提供
        # (插件实现 get_intraday_batch / 可选 get_intraday_latest, YAML 仅修复轮)
    },
]


def _sync_defaults() -> None:
    """把注册表的 default 刷成当前实际默认源。

    插件可在运行期安装依赖 (设置页「安装依赖」), 加载状态会变 → 默认源跟着变;
    导入时算一次不够, 每次构建矩阵前刷新。
    """
    for cap in CAPABILITY_REGISTRY:
        cap["default"] = _default_for(cap["id"])


def _declared_sources() -> list[dict]:
    """内置源 + 插件 + 自定义源 → 统一能力声明视图。

    未注册 (hidden/加载失败) 的源不会出现。
    """
    from app.data_providers import custom as custom_sources
    rows: list[dict] = []
    builtin = registry.builtin_names()
    for plugin in custom_sources.list_plugins():
        rows.append({
            "name": plugin["name"],
            "display": plugin.get("display_name") or plugin["name"],
            "datasets": set(plugin.get("datasets") or []),
            "available": bool(plugin.get("available")),
            "status": str(plugin.get("status") or ""),
            # 内置源走注册表 (设置页「内置」组), 其余走插件组
            "kind": "builtin" if plugin["name"] in builtin else "plugin",
        })
    for source in custom_sources.list_sources():
        rows.append({
            "name": source["name"],
            "display": source.get("display_name") or source["name"],
            "datasets": set(source.get("datasets") or []),
            # 自定义源注册即已通过加载校验, 视为可用
            "available": True,
            "status": "ok",
            "kind": "custom",
        })
    return rows


def _display_of(sources: list[dict], name: str) -> str:
    for s in sources:
        if s["name"] == name:
            return s["display"]
    return name


def build_capability_matrix(current: dict[str, str]) -> dict:
    """注册表 + 源能力声明 + 当前偏好 → 能力路由矩阵。

    current 为 {偏好字段: 当前值}, 由 API 层经 preferences getters 注入;
    getters 已把非法值 (未注册源) 回退为默认, 这里直接信任。effective
    即当前值本身 (每个能力独立路由, 无跟随/派生特殊值)。
    """
    sources = _declared_sources()
    _sync_defaults()

    capabilities = []
    for cap in CAPABILITY_REGISTRY:
        effective = current.get(cap["field"], cap["default"])
        candidates: list[dict] = []
        pending: list[dict] = []
        for s in sources:
            if cap["id"] not in s["datasets"]:
                continue
            entry = {
                "name": s["name"],
                "display": s["display"],
                "kind": s["kind"],
                "available": s["available"],
                "status": s["status"],
                "note": None if s["available"] else (s["status"] or "不可用"),
            }
            (candidates if s["available"] else pending).append(entry)
        usable = any(c["name"] == effective for c in candidates)
        capabilities.append({
            "id": cap["id"],
            "label": cap["label"],
            "desc": cap["desc"],
            "field": cap["field"],
            "default": cap["default"],
            "usable": usable,
            "current": effective,
            "current_display": _display_of(sources, effective),
            "effective": effective,
            "effective_display": _display_of(sources, effective),
            "candidates": candidates,
            "pending": pending,
        })
    return {"capabilities": capabilities}


# ---------------------------------------------------------------------------
# 门禁视角 (面向各服务的启动/调度判断与日志)
# ---------------------------------------------------------------------------

def _cap_specs_by_id() -> dict[str, dict]:
    return {c["id"]: c for c in CAPABILITY_REGISTRY}


def _current_routing() -> dict[str, str]:
    """偏好字段 → 当前生效源 (经 preferences getters, 非法值已回退默认)。

    与 api/settings.py 的 capability-matrix 端点同一组 getter; 显式列全,
    不用 getattr 拼函数名 (拼错会静默回退, 难排查)。
    """
    from app.services import preferences

    return {
        "realtime_data_provider": preferences.get_realtime_data_provider(),
        "daily_data_provider": preferences.get_daily_data_provider(),
        "minute_data_provider": preferences.get_minute_data_provider(),
        "full_minute_data_provider": preferences.get_full_minute_data_provider(),
        "depth5_data_provider": preferences.get_depth5_data_provider(),
        "adj_factor_provider": preferences.get_adj_factor_provider(),
        "financial_data_provider": preferences.get_financial_provider(),
    }


def resolve_capability_sources() -> list[dict]:
    """每个能力一行: 生效源 + 展示名 + 该源当前能否真正提供 (usable)。

    与 build_capability_matrix 同口径 (复用同一 candidates 组装), 供服务层
    的门禁判断与日志标签使用 —— 门禁必须看「生效源能否提供」。

    返回 [{id, label, field, source, source_display, usable}]。
    """
    sources = _declared_sources()
    _sync_defaults()
    current = _current_routing()

    rows: list[dict] = []
    for cap in CAPABILITY_REGISTRY:
        field = cap.get("field")
        if not field:
            continue
        effective = current.get(field, cap["default"])
        provider_names = {
            s["name"] for s in sources
            if cap["id"] in s["datasets"] and s["available"]
        }
        rows.append({
            "id": cap["id"],
            "label": cap["label"],
            "field": field,
            "source": effective,
            "source_display": _display_of(sources, effective),
            "usable": effective in provider_names,
        })
    return rows


def capability_gate(cap_id: str) -> tuple[bool, str]:
    """单个能力的门禁: (是否可用, 日志标签)。

    标签形如 "日K ← fuyao" / "财务数据 ← (当前源不可用)", 让「为什么没跑」
    在日志里一眼可读, 而不必回设置页对照路由。

    可用性以生效源为准 (与设置页 usable 同口径): 路由到已声明的源即可用。
    """
    for row in resolve_capability_sources():
        if row["id"] == cap_id:
            label = f"{row['label']} ← {row['source_display']}"
            if not row["usable"]:
                label += "(当前源不可用)"
            return row["usable"], label
    return False, f"{cap_id}(未知能力)"


# ---------------------------------------------------------------------------
# 运行时能力集 (本地计算)
# ---------------------------------------------------------------------------

# 数据集 → 运行时能力键。
# 数据集级授权 (而非逐能力探测): 一个源声明了 daily 数据集, 就同时提供
# 「按标的日K」与「批量日K」两种调用形态 —— 同一份数据, 不同取数粒度。
#
# websocket 无对应数据集契约 (无任何源提供 WS 推送), 因此不授予;
# 前端 DepthConfigCard 的 fastPolling 判断据此恒为 false, 走轮询路径。
_DATASET_CAPS: tuple[tuple[str, tuple[Cap, ...]], ...] = (
    ("daily", (Cap.KLINE_DAILY_BATCH, Cap.KLINE_DAILY_BY_SYMBOL)),
    ("adj_factor", (Cap.ADJ_FACTOR,)),
    ("realtime", (Cap.QUOTE_POOL, Cap.QUOTE_BATCH, Cap.QUOTE_BY_SYMBOL)),
    ("minute", (Cap.KLINE_MINUTE_BATCH, Cap.KLINE_MINUTE_BY_SYMBOL)),
    ("depth5", (Cap.DEPTH5_BATCH, Cap.DEPTH5)),
    ("financial", (Cap.FINANCIAL,)),
    ("full_minute", (Cap.INTRADAY_UNIVERSE, Cap.INTRADAY_BATCH, Cap.INTRADAY)),
)


def detect_capabilities() -> CapabilitySet:
    """本地计算当前可用的运行时能力集。

    每个能力的生效源声明了该数据集且当前可用 → 授予对应的运行时能力键。
    纯本地计算, 无网络探测: 生效源与能力声明都在进程内, 偏好或插件状态一变
    就能立刻重算, 不需要「重检测」端点。

    限速参数不来自本模块 —— 速率由各源自身的节流策略决定 (easytdx 逐标的
    间隔 20ms), 能力集只回答「能不能做」。
    """
    capset = CapabilitySet()
    sources = _declared_sources()
    current = _current_routing()
    by_name = {s["name"]: s for s in sources}

    for dataset, caps in _DATASET_CAPS:
        # 数据集 id 与偏好字段不是一一对应 (adj_factor 字段名无 _data_provider 后缀),
        # 借注册表把数据集映射到字段。
        field = _field_for_dataset(dataset)
        if not field:
            continue
        provider_name = current.get(field, "")
        source = by_name.get(provider_name)
        if not source or not source["available"]:
            continue
        if dataset not in source["datasets"]:
            continue
        for cap in caps:
            capset.grant(cap)

    return capset


def _field_for_dataset(dataset: str) -> str:
    """数据集 id → 路由偏好字段名 (注册表是唯一映射来源)。"""
    for cap in CAPABILITY_REGISTRY:
        if cap["id"] == dataset:
            return cap.get("field") or ""
    return ""
