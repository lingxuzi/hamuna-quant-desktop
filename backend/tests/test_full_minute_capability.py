"""全量分钟能力 (Cap.INTRADAY_UNIVERSE) 契约。

- 能力位存在且值为 "intraday.universe"
- 生效源声明 full_minute 数据集 → 本地能力计算授予该能力位
- 盘中分钟服务门控挂在该能力位上
"""
from pathlib import Path
from types import SimpleNamespace

import polars as pl

from app.services.minute_refresh import MinuteRefreshService
from app.data_providers.capabilities import Cap, CapabilityLimits, CapabilitySet


def test_capability_enum_value():
    assert Cap("intraday.universe") is Cap.INTRADAY_UNIVERSE






def test_service_gate_requires_universe_not_batch_alone():
    class _Repo:
        store = SimpleNamespace(data_dir=Path("."))

        def get_instruments(self) -> pl.DataFrame:
            return pl.DataFrame({"symbol": []})

    def _svc_with(caps: dict) -> MinuteRefreshService:
        svc = MinuteRefreshService(_Repo())
        svc.set_app_state(SimpleNamespace(capabilities=CapabilitySet(caps)))
        return svc

    universe = {Cap.INTRADAY_UNIVERSE: CapabilityLimits(rpm=20)}
    batch_only = {Cap.INTRADAY_BATCH: CapabilityLimits(rpm=60, batch=200)}

    assert _svc_with(universe).capability_ok() is True
    assert _svc_with(batch_only).capability_ok() is False       # 仅有 intraday.batch 不放行
    assert _svc_with({}).capability_ok() is False
