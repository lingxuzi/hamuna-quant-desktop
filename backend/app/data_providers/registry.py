"""内置数据源注册表 —— 内置 provider 类的唯一真相源。

内置源 = 随应用分发、无需用户额外配置的行情源。当前唯一内置源是 easytdx
(通达信 MAC 协议直连, 实现见 app/plugins/easytdx)。

easytdx 同时保留 app/plugins/easytdx/plugin.yaml: 那份清单承载「安装依赖」
按钮所需的 runtime/requirements 与能力声明 (datasets/usage_notes), 但 provider
类由本注册表提供 —— 加载器不再从插件清单里动态 import 它, 避免同一个类出现
两个真相源。

实例仍由 app.data_providers.custom.loader 持有 (ManagedProvider 负责热替换与
在途请求保护); 本模块只回答「哪个名字对应哪个类」。
"""
from __future__ import annotations

from app.plugins.easytdx.provider import EasyTdxProvider

_PROVIDERS = {
    "easytdx": EasyTdxProvider,
}


def builtin_names() -> set[str]:
    """内置源名字集合。"""
    return set(_PROVIDERS)


def provider_class(name: str):
    """内置 provider 类; 名字不是内置源时返回 None (调用方自行回退)。"""
    return _PROVIDERS.get((name or "").lower())
