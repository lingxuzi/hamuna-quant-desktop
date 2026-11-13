"""渲染因子排名 (matrix_native) 策略源码。

挖掘冠军发布 (mining_candidates) 与市场感知生成 (market_aware) 共用同一模板,
避免两份渲染漂移。只接受已校验的数值/字符串, 不做语义判断。
"""
from __future__ import annotations


def render_factor_rank_strategy(
    *,
    strategy_id: str,
    name: str,
    scoring: dict[str, float],
    directions: dict[str, str],
    description: str = "Published mining factor-rank candidate",
    tags: tuple[str, ...] = ("mining", "factor-rank"),
    asset_type: str = "stock",
    params: dict | None = None,
    basic_filter: dict | None = None,
    stop_loss: float | None = -0.08,
    max_hold_days: int | None = 30,
    provenance: dict | None = None,
    docstring: str = "Trusted factor-rank strategy published from a mining run.",
) -> str:
    """params 覆盖 {entry_score, exit_score, top_rank} 默认值; basic_filter 为空时省略。"""
    defaults = {"entry_score": 70.0, "exit_score": 40.0, "top_rank": 20}
    merged = {**defaults, **(params or {})}
    meta = {
        "id": strategy_id,
        "name": name,
        "description": description,
        "tags": list(tags),
        "asset_types": [asset_type],
        "timeframes": ["1d"],
        "research_only": False,
        **(provenance or {}),
        "params": [
            {
                "id": "entry_score",
                "label": "入场最低分",
                "type": "float",
                "default": float(merged["entry_score"]),
                "min": 0.0,
                "max": 100.0,
                "step": 5.0,
            },
            {
                "id": "exit_score",
                "label": "离场最高分",
                "type": "float",
                "default": float(merged["exit_score"]),
                "min": 0.0,
                "max": 100.0,
                "step": 5.0,
            },
            {
                "id": "top_rank",
                "label": "每日最多入选",
                "type": "int",
                "default": int(merged["top_rank"]),
                "min": 1,
                "max": 100,
                "step": 1,
            },
        ],
        "scoring": {},
        "order_by": "score",
        "descending": True,
        "limit": 100,
    }
    normalized_scoring = {name: float(value) for name, value in scoring.items()}
    normalized_directions = {name: directions[name] for name in scoring}
    lines = [
        f'"""{docstring}"""',
        "from app.strategy.research.factor_rank_research import "
        "FactorRankResearchMatrixStrategy",
        "",
        f"META = {meta!r}",
        "",
    ]
    if basic_filter:
        lines.append(f"BASIC_FILTER = {dict(basic_filter)!r}")
        lines.append("")
    lines.extend([
        'EXECUTION_BACKEND = "matrix_native"',
        'ENTRY_SIGNALS = ["signal_factor_rank_entry"]',
        'EXIT_SIGNALS = ["signal_factor_rank_exit"]',
    ])
    if stop_loss is not None:
        lines.append(f"STOP_LOSS = {float(stop_loss)!r}")
    if max_hold_days is not None:
        lines.append(f"MAX_HOLD_DAYS = {int(max_hold_days)}")
    lines.extend([
        "",
        f"SCORING = {normalized_scoring!r}",
        f"DIRECTIONS = {normalized_directions!r}",
        "MATRIX_STRATEGY = FactorRankResearchMatrixStrategy(SCORING, DIRECTIONS)",
        "",
    ])
    return "\n".join(lines)
