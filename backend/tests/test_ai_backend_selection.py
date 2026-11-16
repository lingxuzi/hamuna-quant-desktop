"""AI 策略生成的「后端自选」契约 — 让跨日需求不再必然生成失败。

回归的是三类真实失败:
1. 请求侧根本不能指定 python_history_legacy → 跨日需求只能被塞进 polars_expr。
2. 修复轮要求「保留原执行后端」→ 唯一一次 LLM 修复机会被浪费在错误后端里。
3. 无需后端偏好的普通需求被默认钉死在 polars_expr。
"""
from __future__ import annotations

import pytest

from app.api.strategy import AIIterateRequest, BuildRequest, _build_execution_backend
from app.strategy.ai_generator import AIStrategyGenerator
from app.strategy.prompt_builder import build_step1

_CROSS_DAY_RULES = "5日线上穿100日线时买入"

# 需要跨日判断却被写成 polars_expr 的代码 (真实失败形态)
_CROSS_DAY_IN_POLARS = '''"""上穿策略 (后端写错)"""
import polars as pl

META = {
    "id": "ai_x",
    "name": "5日线上穿100日线",
    "params": [],
    "scoring": {},
}

EXECUTION_BACKEND = "polars_expr"
ENTRY_SIGNALS = []
EXIT_SIGNALS = []

def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    prev = pl.col("ma5").shift(1).over("symbol")
    return (pl.col("ma5") > pl.col("ma100")) & (prev <= pl.col("ma100").shift(1))
'''


def test_request_models_accept_auto_and_history_backend():
    assert BuildRequest(step=1).execution_backend == "auto"
    assert AIIterateRequest().execution_backend == "auto"
    assert BuildRequest(step=1, execution_backend="python_history_legacy").execution_backend \
        == "python_history_legacy"


def test_auto_prompt_lets_model_choose_backend():
    prompt = build_step1("上穿策略", "5日线上穿100日线", "long", _CROSS_DAY_RULES,
                         execution_backend="auto")
    # 自动选择时必须把判定规则交给模型, 而不是钉死一个后端
    assert "python_history_legacy" in prompt
    assert "自行选择" in prompt
    assert "上穿" in prompt or "多日" in prompt


def test_fixed_prompt_still_locks_backend():
    prompt = build_step1("策略", "描述", "long", "规则",
                         execution_backend="matrix_native")
    assert "matrix_native" in prompt
    assert "不得自行更换" in prompt
    assert "自行选择" not in prompt


def test_step2_update_keeps_existing_code_backend():
    """修改已有策略时后端跟着代码走, 不让 AI 悄悄换后端。"""
    req = BuildRequest(step=2, execution_backend="polars_expr",
                       current_code=_CROSS_DAY_IN_POLARS)
    # 显式传了 polars_expr 但代码里也是 polars_expr → 保持一致
    assert _build_execution_backend(req) == "polars_expr"

    req_auto = BuildRequest(step=2, current_code=_CROSS_DAY_IN_POLARS)
    assert _build_execution_backend(req_auto) == "polars_expr"

    history_code = _CROSS_DAY_IN_POLARS.replace(
        'EXECUTION_BACKEND = "polars_expr"',
        'EXECUTION_BACKEND = "python_history_legacy"\nLOOKBACK_DAYS = 100',
    ).replace("def filter(", "def filter_history(")
    assert _build_execution_backend(
        BuildRequest(step=2, current_code=history_code)
    ) == "python_history_legacy"


@pytest.mark.asyncio
async def test_repair_prompt_allows_switching_backend_on_cross_day_error(monkeypatch):
    """跨日算子被写进 polars_expr 时, 修复提示必须允许换成历史后端。"""
    gen = AIStrategyGenerator(execution_backend="polars_expr")
    captured: dict[str, str] = {}

    async def fake_call(prompt: str, guide: str) -> str:
        captured["prompt"] = prompt
        return "```python\nprint('stub')\n```"

    monkeypatch.setattr(gen, "_call_llm", fake_call)
    error = (
        "策略静态校验失败: polars_expr 的 filter() 每日每标的只有一行, "
        "不能用跨日算子 shift; 需要多日判断请改用 python_history_legacy + filter_history()"
    )
    await gen.repair_code(_CROSS_DAY_IN_POLARS, error)

    prompt = captured["prompt"]
    assert 'python_history_legacy' in prompt
    assert "保留原执行后端" not in prompt


@pytest.mark.asyncio
async def test_repair_prompt_keeps_backend_when_error_is_unrelated(monkeypatch):
    """非跨日错误仍然保留原后端, 不制造无谓改动。"""
    gen = AIStrategyGenerator(execution_backend="polars_expr")
    captured: dict[str, str] = {}

    async def fake_call(prompt: str, guide: str) -> str:
        captured["prompt"] = prompt
        return "```python\nprint('stub')\n```"

    monkeypatch.setattr(gen, "_call_llm", fake_call)
    await gen.repair_code(_CROSS_DAY_IN_POLARS, "解析META失败: 找不到 META 字典")

    assert "保留原执行后端" in captured["prompt"]
