"""测试用数据目录 —— 内置策略与内置信号已种入。

为什么测试必须自己种: 内置策略种子在 ``app/strategy/seeds/``, 内置自定义信号
种子在 ``app/strategy/signal_seeds.py``, 两者运行时分别被复制进
``data/strategies/custom/`` 与写进 ``data/user_data/custom_signals/`` 才生效
(见 ``app/strategy/strategy_seeds.py``、``app/strategy/custom_signals.py``)。
而 ``data/`` 是用户运行时数据, 不进 Git —— 干净环境 (CI) 里既没有迁移痕迹
也没有种子: 原生策略数为 0, 依赖 ``signal_limit_*`` 的逻辑随之塌掉。

本机有迁移痕迹会种入、CI 没有, 两边行为本来就不一致。测试要覆盖"内置策略 /
内置信号"的行为, 就自己把种子种进临时目录, 不碰开发者本机的 ``data/``。
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from app.strategy import custom_signals
from app.strategy.strategy_seeds import SEEDS_DIR


def seed_builtin_strategies(data_dir: Path) -> int:
    """把内置策略种子复制进 ``data/strategies/custom`` (与 seed_missing_builtins 同源)。"""
    target = data_dir / "strategies" / "custom"
    target.mkdir(parents=True, exist_ok=True)
    written = 0
    for seed in sorted(SEEDS_DIR.glob("*.py")):
        # _ 前缀是包的 __init__.py 等: 引擎把 _*.py 当依赖文件, 不是策略
        if seed.name.startswith("_"):
            continue
        shutil.copy2(seed, target / seed.name)
        written += 1
    return written


def seed_builtin_signals(data_dir: Path) -> int:
    """种入内置自定义信号 (signal_limit_up / signal_limit_down 等)。"""
    return custom_signals.seed_if_empty(data_dir)


def make_seeded_data_dir() -> Path:
    """新建一个临时数据目录并种入内置策略与内置信号。"""
    data_dir = Path(tempfile.mkdtemp(prefix="seeded-data-"))
    seed_builtin_strategies(data_dir)
    seed_builtin_signals(data_dir)
    return data_dir


# 模块级共享目录: 供"导入期就要算出策略清单"的测试用 (parametrize 需要静态列表)。
# TemporaryDirectory 持在模块里, 进程退出时自动清理。
_TMP = tempfile.TemporaryDirectory(prefix="seeded-data-")
SEEDED_DATA_DIR = Path(_TMP.name)
seed_builtin_strategies(SEEDED_DATA_DIR)
seed_builtin_signals(SEEDED_DATA_DIR)
