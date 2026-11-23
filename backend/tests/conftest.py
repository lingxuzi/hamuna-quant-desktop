"""共享 fixture —— 数据目录自带内置策略与内置信号。

种子的来源与理由见 ``seeded_env.py`` 的模块说明。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from seeded_env import seed_builtin_signals, seed_builtin_strategies

from app.config import settings


@pytest.fixture
def seeded_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每个测试独享的、已种入内置策略与信号的数据目录 (并接管 settings.data_dir)。"""
    from app.indicators import pipeline

    data_dir = tmp_path / "data"
    seed_builtin_strategies(data_dir)
    seed_builtin_signals(data_dir)
    monkeypatch.setattr(settings, "data_dir", data_dir)
    # 自定义信号表达式是模块级缓存: 换目录必须失效, 否则读到上一个测试的信号
    pipeline.invalidate_custom_signals()
    yield data_dir
    pipeline.invalidate_custom_signals()
