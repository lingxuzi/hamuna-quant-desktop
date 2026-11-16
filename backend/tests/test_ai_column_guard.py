from __future__ import annotations

import pytest

from app.api.strategy import StrategyCodeValidateRequest, _prepare_strategy_code
from app.strategy.ai_generator import AIStrategyGenerator
from app.strategy.engine import strategy_runtime_error

# AI 生成过的真实坏代码: polars_expr 的 filter() 里引用从未物化的辅助列 _ma5/_ma100,
# 并且要判断「上穿」却用只有当日数据的 filter()。
BROKEN_CODE = '''"""5日线上穿100日线做多策略"""
import polars as pl

META = {
    "id": "ai_broken",
    "name": "5日线上穿100日线",
    "params": [],
    "scoring": {"momentum_20d": 1.0},
}

EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    ma5 = pl.col("close").rolling_mean(window_size=5).alias("_ma5")
    ma100 = pl.col("close").rolling_mean(window_size=100).alias("_ma100")
    prev_ma5 = pl.col("_ma5").shift(1).over("symbol")
    prev_ma100 = pl.col("_ma100").shift(1).over("symbol")
    cross_up = (pl.col("_ma5") > pl.col("_ma100")) & (prev_ma5 <= prev_ma100)
    return cross_up
'''

VALID_POLARS_CODE = '''"""有效日线策略"""
import polars as pl

META = {
    "id": "ai_valid",
    "name": "有效策略",
    "params": [],
    "scoring": {"momentum_20d": 1.0},
}

EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return (pl.col("close") > pl.col("ma20")) & (pl.col("volume") > pl.col("vol_ma5"))
'''

VALID_HISTORY_CODE = '''"""有效历史窗口策略"""
import polars as pl

META = {
    "id": "ai_valid_history",
    "name": "有效历史策略",
    "params": [],
    "scoring": {"momentum_20d": 1.0},
}

EXECUTION_BACKEND = "python_history_legacy"
LOOKBACK_DAYS = 100
REQUIRED_FEATURES = {"close", "volume"}

ENTRY_SIGNALS = []
EXIT_SIGNALS = []

def filter_history(df: pl.DataFrame, params: dict) -> pl.DataFrame:
    hist = df.sort(["symbol", "date"]).with_columns([
        pl.col("close").rolling_mean(100).over("symbol").alias("_ma100"),
    ])
    return hist.filter(pl.col("close") > pl.col("_ma100"))
'''


def test_validate_code_rejects_helper_column_in_polars_filter():
    result = AIStrategyGenerator().validate_code(BROKEN_CODE)

    assert not result["valid"]
    assert "_ma5" in result["error"]
    assert "ma100" in result["error"]


def test_broken_code_triggers_structural_repair():
    result = AIStrategyGenerator().validate_code(BROKEN_CODE)

    assert AIStrategyGenerator.needs_structural_repair(result)


def test_prepare_strategy_code_rejects_missing_column():
    req = StrategyCodeValidateRequest(strategy_id="ai_broken", code=BROKEN_CODE)

    with pytest.raises(ValueError, match="不存在的列"):
        _prepare_strategy_code(req)


def test_valid_polars_strategy_passes():
    result = AIStrategyGenerator().validate_code(VALID_POLARS_CODE)

    assert result["valid"], result["error"]


def test_valid_history_strategy_passes():
    result = AIStrategyGenerator().validate_code(VALID_HISTORY_CODE)

    assert result["valid"], result["error"]


def test_history_strategy_without_lookback_is_rejected():
    code = VALID_HISTORY_CODE.replace("LOOKBACK_DAYS = 100\n", "")
    result = AIStrategyGenerator().validate_code(code)

    assert not result["valid"]
    assert "LOOKBACK_DAYS" in result["error"]


def test_history_strategy_without_over_symbol_is_rejected():
    # 多标的 × 多日面板上不带 .over("symbol") 会跨标的串数据 → 静默算错, 必须拦。
    code = VALID_HISTORY_CODE.replace('.over("symbol")', "")
    result = AIStrategyGenerator().validate_code(code)

    assert not result["valid"]
    assert "over" in result["error"]


def test_history_strategy_with_group_by_is_not_rejected():
    # 显式 group_by/agg 口径静态不可判定 → fail-open 放行, 不误杀。
    code = VALID_HISTORY_CODE.replace(
        'pl.col("close").rolling_mean(100).over("symbol").alias("_ma100"),',
        'pl.col("close").rolling_mean(100).alias("_ma100"),',
    ).replace(
        "    hist = df.sort",
        '    hist = df.group_by("symbol", maintain_order=True).agg(pl.col("close").rolling_mean(100).alias("_ma100"))\n    hist = df.sort',
    )
    result = AIStrategyGenerator().validate_code(code)

    assert "over" not in (result.get("error") or "")


def test_runtime_error_names_the_missing_column():
    error = strategy_runtime_error(
        "ai_broken",
        RuntimeError('unable to find column "_ma5"; valid columns: ["close"]'),
    )

    assert isinstance(error, ValueError)
    assert "_ma5" in str(error)
