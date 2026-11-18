"""模型只输出行动计划就收尾时, 助手必须继续执行, 不能把计划当答案交付。

现象: 模型回"我先查一下数据, 同时拉报价和关键价位。"就 finish_reason=stop,
本轮没有 tool_calls —— 后端若直接收尾, 用户只看到一句计划, 没有任何分析正文。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.custom.assistant import chat_service
from app.custom.assistant import tools as assistant_tools

PLAN_TEXT = "先确认数据是否最新, 同时拉报价和关键价位。"

# 三种真实出现过的半截话形态: 计划式 / 框架式 / 隐晦式
PLAN_SAMPLES = [
    "先确认数据是否最新, 同时拉报价和关键价位。",
    "我先按你点名的巴菲特框架来, 先看财务与报表有没有排雷信号, 再谈估值与护城河。",
    "我是巴菲特视角，先不猜——把财务数据拉起来看。",
]


def _install(monkeypatch: pytest.MonkeyPatch, script: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按脚本逐轮产出模型回复; 返回每轮实际收到的请求消息。"""
    captured: list[list[dict[str, Any]]] = []

    async def fake_round(messages, tool_schemas, *, temperature=0.3, timeout=240.0):
        captured.append([dict(m) for m in messages])
        step = script.pop(0)
        for text in step.get("texts", []):
            yield {"type": "text", "delta": text}
        yield {
            "type": "round_end",
            "tool_calls": step.get("tool_calls", []),
            "finish_reason": "tool_calls" if step.get("tool_calls") else "stop",
        }

    async def fake_execute(name: str, args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        return {"ok": True, "result": {}}

    monkeypatch.setattr(chat_service, "ai_configured", lambda: True)
    monkeypatch.setattr(chat_service, "is_codex_cli_provider", lambda provider=None: False)
    monkeypatch.setattr(chat_service, "stream_openai_round", fake_round)
    monkeypatch.setattr(assistant_tools, "execute_assistant_tool", fake_execute)
    return captured


async def _run(monkeypatch: pytest.MonkeyPatch, script: list[dict[str, Any]]):
    captured = _install(monkeypatch, script)
    lines = [
        json.loads(line) async for line in chat_service.chat_stream(
            history=[{"role": "user", "content": "帮我分析600337"}],
        )
    ]
    return lines, captured


@pytest.mark.parametrize("plan_text", PLAN_SAMPLES)
async def test_plan_only_answer_is_nudged_into_tool_call(
    monkeypatch: pytest.MonkeyPatch, plan_text: str,
) -> None:
    """计划式回复必须触发纠偏轮, 模型随后调用工具并给出正文。"""
    lines, captured = await _run(monkeypatch, [
        {"texts": [plan_text]},
        {"tool_calls": [{"id": "c1", "name": "get_stock_daily", "arguments": "{}"}]},
        {"texts": ["600337 近 60 日处于震荡筑底区间。"]},
    ])

    assert len(captured) == 3
    # 第二轮请求里带上了纠偏指令
    assert any("只输出了行动计划" in str(m.get("content", "")) for m in captured[1])
    assert not any(e["type"] == "error" for e in lines)
    # 正文确实交付了
    assert "".join(e["content"] for e in lines if e["type"] == "delta").endswith("震荡筑底区间。")
    assert lines[-1]["type"] == "done"


async def test_plan_nudge_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型一直吐计划时最多纠偏几次, 不能无限循环。"""
    script = [{"texts": [PLAN_TEXT]} for _ in range(6)]
    lines, captured = await _run(monkeypatch, script)

    assert len(captured) == 3  # 1 轮原答 + 2 次纠偏
    assert not any(e["type"] == "error" for e in lines)


@pytest.mark.parametrize("answer", [
    "你有 1 个策略。",
    "今天是大盘普涨日。",
    "先给结论, 再给依据。",
    "你好，有什么可以帮您？",
])
async def test_normal_short_answer_ends_turn(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    """短但确实是结论的回答不该被纠偏 —— 否则纯框架问题会被拖成多轮。"""
    lines, captured = await _run(monkeypatch, [{"texts": [answer]}])

    assert len(captured) == 1
    assert lines[-1]["type"] == "done"


async def test_plan_detection_needs_intent_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有计划词的长文本不算计划 (长回答里出现"先看"很常见)。"""
    lines, captured = await _run(monkeypatch, [{"texts": ["先看均线, 再看量能。" * 20]}])

    assert len(captured) == 1
    assert lines[-1]["type"] == "done"
