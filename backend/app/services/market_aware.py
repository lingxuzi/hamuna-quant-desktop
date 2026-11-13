"""市场感知策略生成: 近 120 天事实 + 挖掘冠军因子池 → AI 决策 → 合并渲染落盘。

分工:
- 挖掘冠军提供「因子池 + 权重 + 方向」(全样本长窗口验证, 平台渲染, AI 不改);
- AI 只决定「入场/离场分、基础过滤、止损与持有天数」, 越界夹逼, 缺项用默认。
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import date
from typing import Any

import polars as pl

from app.services.market_facts import DEFAULT_WINDOW_DAYS, build_market_facts
from app.strategy import market_plan
from app.strategy.factor_rank_render import render_factor_rank_strategy


def champion_from_run(run_store, run_id: str) -> dict[str, Any]:
    """取挖掘 run 的冠军候选 (整体表现最高分的因子组合); 无合格冠军时报错。"""
    manifest = run_store.get(run_id)
    if manifest is None:
        raise ValueError("挖掘任务不存在")
    artifacts = manifest.get("artifacts") or {}
    if not isinstance(artifacts, dict) or "candidates" not in artifacts:
        raise ValueError("该挖掘任务没有候选结果, 请先等挖掘跑完")
    path = run_store.artifact_path(run_id, "candidates")
    if not path.is_file():
        raise ValueError("候选结果文件缺失, 请重新运行挖掘")
    frame = pl.read_parquet(path)
    if frame.is_empty():
        raise ValueError("该挖掘任务没有产出候选, 请换更长区间或更低的挖掘档位再试")
    frame = frame.filter(
        pl.col("kind") == "factor_combination", pl.col("regime_state") == "overall"
    )
    if frame.is_empty():
        raise ValueError("该挖掘任务没有因子组合类候选, 无法合并为因子排名策略")
    row = frame.sort("score", descending=True).row(0, named=True)
    definition = json.loads(row["definition_json"])
    names = list(definition["factor_names"])
    summary = run_store.read_summary(run_id) or {}
    return {
        "run_id": run_id,
        "signature": row["signature"],
        "name": row["name"],
        "score": _finite(row["score"]),
        "factors": {name: float(definition["scoring"][name]) for name in names},
        "directions": {name: definition["directions"][name] for name in names},
        "provenance": {
            "origin_run_id": run_id,
            "candidate_signature": row["signature"],
            "mining_algorithm_version": str(summary.get("algorithm_version") or "mining-v1"),
            "factor_methodology_version": str(summary.get("methodology_version") or "factor_v2"),
        },
        "metrics": {
            "oos_sharpe": _finite(row.get("oos_sharpe")),
            "oos_return": _finite(row.get("oos_return")),
            "oos_max_drawdown": _finite(row.get("oos_max_drawdown")),
            "oos_n_trades": row.get("oos_n_trades"),
            "confidence": row.get("confidence"),
        },
    }


def validate_decisions(decisions: dict, factors: dict[str, float]) -> dict:
    """渲染前的最后一道校验: 结构化问题直接拒绝, 数值越界已在 parse 阶段夹逼。"""
    params = decisions["params"]
    if not (0.0 <= params["exit_score"] < params["entry_score"] <= 100.0):
        raise ValueError("AI 给出的入场/离场分不合法")
    if not 1 <= params["top_rank"] <= 100:
        raise ValueError("AI 给出的每日入选数不合法")
    if set(decisions["risk"]) != {"stop_loss", "max_hold_days"}:
        raise ValueError("AI 给出的风控参数不完整")
    stop = decisions["risk"]["stop_loss"]
    if not (-0.5 <= stop <= -0.01):
        raise ValueError("AI 给出的止损比例不合法")
    return decisions


def new_market_aware_id() -> str:
    return f"custom_market_aware_{uuid.uuid4().hex[:12]}"


def render_market_aware_strategy(
    champion: dict,
    decisions: dict,
    *,
    strategy_id: str,
    asset_type: str,
) -> str:
    name = decisions.get("name") or f"市场感知·{champion['name']}"
    description = decisions.get("description") or "由近 120 天市场事实与挖掘冠军因子池合并生成"
    return render_factor_rank_strategy(
        strategy_id=strategy_id,
        name=name[:30],
        description=description,
        tags=("market-aware", "factor-rank"),
        asset_type=asset_type,
        scoring=champion["factors"],
        directions=champion["directions"],
        params=decisions["params"],
        basic_filter=decisions["basic_filter"],
        stop_loss=decisions["risk"]["stop_loss"],
        max_hold_days=decisions["risk"]["max_hold_days"],
        provenance=champion.get("provenance"),
        docstring="市场感知因子排名策略: 挖掘冠军因子池 + 近 120 天市场事实决定的参数。",
    )


def build_strategy(
    *,
    data_dir,
    repo,
    run_store,
    run_id: str,
    dimensions: list[str],
    asset_type: str,
    ai_text: str,
    facts: dict | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    as_of: date | None = None,
) -> dict:
    """纯函数式核心: 事实 + 冠军 + AI 输出 → 源码 + 决策 (不落盘)。"""
    champion = champion_from_run(run_store, run_id)
    if facts is None:
        facts = build_market_facts(
            data_dir, repo, dimensions=dimensions, asset_type=asset_type,
            window_days=window_days, as_of=as_of,
        )
    decisions = validate_decisions(
        market_plan.parse_and_validate(ai_text, factors=champion["factors"]),
        champion["factors"],
    )
    strategy_id = new_market_aware_id()
    source = render_market_aware_strategy(
        champion, decisions, strategy_id=strategy_id, asset_type=asset_type
    )
    return {
        "strategy_id": strategy_id,
        "source": source,
        "champion": champion,
        "decisions": decisions,
        "facts": facts,
    }


def _finite(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    number = float(value)
    return round(number, 4) if math.isfinite(number) else None
