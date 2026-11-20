"""市场感知生成: 事实构建 + AI 决策 + 合并渲染的契约测试。"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.backtest.mining import compute_candidate_signature
from app.services.market_aware import (
    champion_from_run,
    render_market_aware_strategy,
    validate_decisions,
)
from app.services.market_facts import (
    MarketFactsUnavailableError,
    build_market_facts,
)
from app.services.mining_jobs import MiningRunStore
from app.strategy.market_plan import build_messages, parse_and_validate

FACTORS = {"turnover_rate": 1.0, "rsi_14": 2.0}
DIRECTIONS = {"turnover_rate": "high", "rsi_14": "low"}


# ── 市场事实 ──

def _regime_frame() -> pl.DataFrame:
    rows = []
    today = date.today()
    for index in range(40):
        rows.append({
            "date": (today - timedelta(days=40 - index)).isoformat(),
            "state": "strong" if index % 2 else "weak",
            "score": 60 + index % 20,
            "phase": "rally" if index % 3 else "repair",
            "up_ratio": 1.5,
            "index_pct": 0.002,
            "avg_pct": 0.001,
            "above_ma20_pct": 0.5,
            "avg_turnover": 3.0e8,
        })
    return pl.DataFrame(rows)


def test_market_facts_aggregates_states_and_phases(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.regime_builder.load_regime_history", lambda data_dir: _regime_frame()
    )
    result = build_market_facts(
        tmp_path, object(), dimensions=["market"], asset_type="stock", window_days=120
    )
    market = result["market"]
    assert market["bars"] == 40
    assert {s["state"] for s in market["states"]} == {"strong", "weak"}
    assert market["states"][0]["days"] == 20
    assert market["score"]["mean"] is not None
    assert market["phase_last"] in {"rally", "repair"}
    assert len(market["recent"]) == 10


def test_market_facts_rejects_unknown_dimension(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="不支持的参考维度"):
        build_market_facts(tmp_path, object(), dimensions=["moon_phase"])


def test_market_facts_requires_a_dimension(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="至少选择一个参考维度"):
        build_market_facts(tmp_path, object(), dimensions=[])


def test_money_flow_dimension_reports_missing_data(tmp_path: Path) -> None:
    """没配时序型扩展表时必须明确指路, 不静默降级。"""
    with pytest.raises(MarketFactsUnavailableError, match="扩展数据"):
        build_market_facts(tmp_path, object(), dimensions=["money_flow"])


def test_market_facts_missing_regime_history_reports_pointer(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.regime_builder.load_regime_history", lambda data_dir: pl.DataFrame()
    )
    with pytest.raises(MarketFactsUnavailableError, match="市场状态"):
        build_market_facts(tmp_path, object(), dimensions=["market"])


# ── AI 决策 ──

def test_parse_and_validate_clamps_out_of_range_values() -> None:
    decisions = parse_and_validate(
        json.dumps({
            "name": "测试",
            "params": {"entry_score": 500, "exit_score": -20, "top_rank": 9999},
            "basic_filter": {"price_min": 5},
            "risk": {"stop_loss": -5, "max_hold_days": 0},
        }),
        factors=FACTORS,
    )
    assert decisions["params"]["entry_score"] == 100.0
    assert decisions["params"]["exit_score"] == 0.0
    assert decisions["params"]["top_rank"] == 10  # 2 个因子 → 上限 10
    assert decisions["basic_filter"]["price_min"] == 5.0
    assert decisions["risk"]["stop_loss"] == -0.5
    assert decisions["risk"]["max_hold_days"] == 1


def test_parse_and_validate_keeps_entry_above_exit() -> None:
    decisions = parse_and_validate(
        json.dumps({"params": {"entry_score": 40, "exit_score": 60}}), factors=FACTORS
    )
    assert decisions["params"]["entry_score"] > decisions["params"]["exit_score"]


def test_parse_and_validate_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        parse_and_validate("[1, 2, 3]", factors=FACTORS)


def test_validate_decisions_rejects_broken_risk() -> None:
    with pytest.raises(ValueError, match="风控参数"):
        validate_decisions(
            {
                "params": {"entry_score": 70.0, "exit_score": 40.0, "top_rank": 10},
                "basic_filter": {},
                "risk": {"stop_loss": -0.08},
            },
            FACTORS,
        )


# ── 合并渲染 ──

def test_render_merges_champion_scoring_and_ai_params() -> None:
    source = render_market_aware_strategy(
        {
            "name": "冠军",
            "factors": FACTORS,
            "directions": DIRECTIONS,
        },
        {
            "name": "弱市防御",
            "description": "弱市收紧",
            "params": {"entry_score": 80.0, "exit_score": 50.0, "top_rank": 10},
            "basic_filter": {"price_min": 3.0},
            "risk": {"stop_loss": -0.05, "max_hold_days": 12},
        },
        strategy_id="custom_market_aware_abc",
        asset_type="stock",
    )
    assert "SCORING = {'turnover_rate': 1.0, 'rsi_14': 2.0}" in source
    assert "DIRECTIONS = {'turnover_rate': 'high', 'rsi_14': 'low'}" in source
    assert "'default': 80.0" in source
    assert "STOP_LOSS = -0.05" in source
    assert "MAX_HOLD_DAYS = 12" in source
    assert "BASIC_FILTER = {'price_min': 3.0}" in source
    assert 'EXECUTION_BACKEND = "matrix_native"' in source


def test_render_carries_mining_provenance_into_meta() -> None:
    """溯源键进 META: 跟单时按 candidate_signature 做同源核对。"""
    source = render_market_aware_strategy(
        {
            "name": "冠军",
            "factors": FACTORS,
            "directions": DIRECTIONS,
            "provenance": {
                "origin_run_id": "run-1",
                "candidate_signature": "sig-1",
                "mining_algorithm_version": "mining-v1",
                "factor_methodology_version": "factor_v2",
            },
        },
        {
            "name": "弱市防御",
            "description": "弱市收紧",
            "params": {"entry_score": 80.0, "exit_score": 50.0, "top_rank": 10},
            "basic_filter": {},
            "risk": {"stop_loss": -0.05, "max_hold_days": 12},
        },
        strategy_id="custom_market_aware_abc",
        asset_type="stock",
    )
    assert "'origin_run_id': 'run-1'" in source
    assert "'candidate_signature': 'sig-1'" in source


def test_champion_from_run_picks_best_scoring_factor_combination(tmp_path: Path) -> None:
    store = MiningRunStore(tmp_path)
    store.create(
        {
            "factor_names": ["turnover_rate", "rsi_14"],
            "strategy_ids": [],
            "asset_type": "stock",
            "start": "2025-01-01",
            "end": "2026-01-09",
            "budget_profile": "exploratory",
            "commission_pct": 0.0002,
            "stamp_tax_pct": 0.0005,
            "slippage_bps": 5.0,
        },
        {"generation": "test"},
        run_id="run-1",
    )
    definition = {
        "kind": "factor_rank",
        "factor_names": ["turnover_rate", "rsi_14"],
        "scoring": {"turnover_rate": 1.0, "rsi_14": 2.0},
        "directions": {"turnover_rate": "high", "rsi_14": "low"},
    }
    signature = compute_candidate_signature(definition)
    pl.DataFrame({
        "signature": [signature],
        "name": ["因子组合候选"],
        "kind": ["factor_combination"],
        "factor_names_json": [json.dumps(definition["factor_names"])],
        "strategy_id": [None],
        "definition_json": [json.dumps(definition, sort_keys=True)],
        "regime_state": ["overall"],
        "score": [0.9],
        "oos_return": [0.08],
        "oos_sharpe": [0.9],
        "oos_max_drawdown": [-0.12],
        "oos_positive_fold_ratio": [1.0],
        "oos_n_trades": [70],
        "confidence": ["standard"],
        "valid_folds": [3],
        "skipped_folds": [0],
        "promoted_candidate_id": [None],
        "published_strategy_id": [None],
    }, schema_overrides={
        "strategy_id": pl.String,
        "promoted_candidate_id": pl.String,
        "published_strategy_id": pl.String,
    }).write_parquet(store.artifact_path("run-1", "candidates"))
    store.register_artifact("run-1", "candidates")

    champion = champion_from_run(store, "run-1")
    assert champion["factors"] == FACTORS
    assert champion["directions"] == DIRECTIONS
    assert champion["score"] == 0.9
    # 溯源写进 META.provenance: 跟单时用于同源核对
    assert champion["provenance"]["origin_run_id"] == "run-1"
    assert champion["provenance"]["candidate_signature"] == signature


def test_champion_from_run_rejects_unknown_run(tmp_path: Path) -> None:
    store = MiningRunStore(tmp_path)
    with pytest.raises(ValueError, match="挖掘任务不存在"):
        champion_from_run(store, "missing")


# ── 提示词 ──

def test_build_messages_contains_facts_and_factor_pool() -> None:
    facts = {
        "dimensions": ["market", "sector"],
        "window": {"start": "2026-01-01", "end": "2026-05-01", "days": 120},
        "market": {
            "bars": 80,
            "states": [{"state": "weak", "days": 50}],
            "phases": [{"phase": "repair", "days": 70}],
            "score": {"mean": 40.0, "last": 10.0},
            "up_ratio": {"mean": 1.2, "last": 0.9},
            # up_rate 是占比 (0-1), 与 up_ratio (比值) 不同口径, 两个都要写:
            # 文本侧只喂 up_share, 缺失即 KeyError。
            "up_share": {"mean": 0.55, "last": 0.52},
            "avg_pct": {"mean": -0.001, "last": -0.002},
            "above_ma20_pct": {"mean": 0.4, "last": 0.3},
            "phase_last": "repair",
        },
        "sector": {
            "sectors": [
                {
                    "member": "AI算力",
                    "days": 30,
                    "avg_score": 70.0,
                    "best_rank": 1,
                    "limit_up_sum": 40,
                    "momentum": 3.0,
                }
            ],
            "rising": [{"member": "AI算力", "momentum": 3.0}],
            "fading": [{"member": "白酒", "momentum": -2.0}],
        },
    }
    messages = build_messages(facts, factors=FACTORS, directions=DIRECTIONS, asset_type="stock")
    user = messages[-1]["content"]
    assert "weak" in user and "repair" in user
    assert "AI算力" in user
    assert "turnover_rate" in user
    assert "越高越好" in user
