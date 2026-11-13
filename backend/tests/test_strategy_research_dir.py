"""内置策略目录的加载门 — research/ 下每个 *.py 都必须是合法策略。

引擎把扫描目录里的每个模块都当策略加载 (engine._load_all)。往里放一个
普通工具模块 (没有 filter / MATRIX_STRATEGY) 会让每次 engine.reload() 失败,
进而让**所有**策略保存与发布一起失败, 报错还指向那个无关文件。
"""
from __future__ import annotations

from pathlib import Path

from app.strategy.engine import StrategyEngine

RESEARCH_DIR = Path(__file__).resolve().parents[1] / "app" / "strategy" / "research"


def test_research_dir_modules_all_load_as_strategies() -> None:
    engine = StrategyEngine(strategy_dirs=[RESEARCH_DIR])
    assert engine.load_errors() == []
