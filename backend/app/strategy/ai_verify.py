"""AI 生成结果的自验证闭环 — 静态校验过了不等于跑得通。

背景: 静态校验 (column_guard) 只看源码, 拦不住「运行期才炸」的一类问题:
polars 表达式误用 python 的 and/or、比较结果被 cast 成数值再参与位运算、
辅助列在 filter() 里引用但没物化。这几类都是合法 Python + 合法列名,
静态层面看不出问题, 只有按实盘口径真跑一遍才会炸。

实测 (scripts/ai_strategy_stability.py, 6 个策略方向) 全部炸在实跑这一步。
所以这里在生成后按实盘口径跑一遍, 跑不通就把报错喂回模型改 ——
生成期就把「能跑」闭环, 而不是等用户点保存时被 smoke 拦下。

草稿落在临时目录 + 单独的引擎实例里, 不写用户的 data/strategies/。
"""
from __future__ import annotations

import logging
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.strategy.engine import StrategyEngine
from app.strategy.smoke import smoke_message, smoke_strategy

logger = logging.getLogger(__name__)

# 生成期自验证最多让模型改几轮 (第 0 轮 = 刚生成的那版)。
MAX_VERIFY_ROUNDS = 2

# 临时文件名用固定安全名 —— META.id 由模型给, 不能直接拼进路径。
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_-]")


@contextmanager
def _sandbox_engine(code: str, strategy_id: str) -> Iterator[StrategyEngine]:
    """把代码放进临时目录单独加载一个引擎 (不污染用户的策略目录)。"""
    with tempfile.TemporaryDirectory() as tmp:
        draft_dir = Path(tmp) / "ai"
        draft_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{_SAFE_NAME_RE.sub('_', strategy_id) or 'draft'}.py"
        (draft_dir / file_name).write_text(code, encoding="utf-8")
        yield StrategyEngine(strategy_dirs=[draft_dir])


def _loaded_strategy_id(engine: StrategyEngine, fallback: str) -> str:
    """引擎按代码里的 META.id 建索引 —— 模型没照抄指定的 id 时取实际加载到的那个。"""
    if engine.has(fallback):
        return fallback
    ids = [str(s.meta.get("id")) for s in engine.strategy_definitions() if s.meta.get("id")]
    return ids[0] if len(ids) == 1 else fallback


def smoke_generated(code: str, strategy_id: str, repo) -> dict[str, Any]:
    """按实盘口径跑一次生成结果; 引擎/数据不可用时按 fail-open 跳过。"""
    try:
        with _sandbox_engine(code, strategy_id) as engine:
            return smoke_strategy(engine, repo, _loaded_strategy_id(engine, strategy_id))
    except Exception as exc:  # noqa: BLE001 — 校验器自身的问题不该挡住生成
        logger.warning("ai_verify: 实跑校验异常 (%s): %s", strategy_id, exc)
        return {"ok": True, "issues": [], "skipped": f"verify_error: {exc}"}


async def verify_and_repair(
    generator,
    result: dict[str, Any],
    *,
    repo,
    max_rounds: int = MAX_VERIFY_ROUNDS,
) -> dict[str, Any]:
    """生成结果跑不通就带着报错找模型改; 返回最终 result (带 smoke 字段)。

    repo 为 None 时跳过实跑 (无行情环境不该挡住生成)。
    """
    if not result.get("valid") or repo is None:
        return result
    strategy_id = str((result.get("meta") or {}).get("id") or "ai_generated")
    smoke = smoke_generated(result["code"], strategy_id, repo)
    result["smoke"] = smoke
    result["verify_rounds"] = 0

    rounds = 0
    while not smoke.get("ok") and rounds < max_rounds:
        rounds += 1
        issue = smoke_message(smoke)
        logger.info("ai_verify: %s 实跑失败, 第 %d 轮让模型修: %s",
                    strategy_id, rounds, issue)
        repaired = await generator.repair_code(
            result["code"],
            f"实盘口径实跑失败: {issue}",
            context="实盘口径实跑",
        )
        if not repaired.get("valid"):
            # 修完连静态校验都过不了 → 保留原始报错, 让用户看到真实原因
            result["smoke"] = smoke
            result["repair_error"] = repaired.get("error")
            result["verify_rounds"] = rounds
            return result
        result = repaired
        strategy_id = str((result.get("meta") or {}).get("id") or strategy_id)
        smoke = smoke_generated(result["code"], strategy_id, repo)
        result["smoke"] = smoke
    result["verify_rounds"] = rounds
    return result
