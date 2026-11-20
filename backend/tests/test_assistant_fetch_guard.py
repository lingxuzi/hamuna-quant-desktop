"""AI 助手取数守卫: 用户点了名的标的, 模型没取到数就不能给结论。

复现过的失效: 连续问多只股票时, 第一轮有工具调用, 后面几轮模型看见历史里
上一只股票的完整回答, 就照着那个格式直接续写 —— 一个工具都不调, 价格与
涨跌幅全是编的。提示词压不住(改写「不重复查询同类信息」+ 追加强制条款,
两轮实验里第 2/3 轮照样编数), 所以守卫做在代码里:

· 提问里点名的标的 → 本轮必须取数的清单;
· 模型没调工具就收尾 → 丢掉这段正文, 催它回去取数;
· 催了仍不调 → 代它取一次(合成一次工具调用), 把真实数据塞回上下文;
· 到上限仍没取到 → 放行正文, 但明确提示数字未经取数校验。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.custom.assistant import chat_service
from app.custom.assistant import tools as assistant_tools
from app.custom.assistant.fetch_guard import FetchGuard


def _script_round(script: list[dict[str, Any]], capture: list[list[dict[str, Any]]]):
    async def fake_round(messages, tool_schemas, *, temperature=0.3, timeout=240.0):
        capture.append([dict(m) for m in messages])
        step = script.pop(0)
        for piece in step.get("text_pieces", []):
            yield {"type": "text", "delta": piece}
        yield {"type": "round_end", "tool_calls": step.get("tool_calls", []), "finish_reason": "stop"}

    return fake_round


async def _fake_execute(name: str, args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    symbols = list(args.get("symbols") or ([args["symbol"]] if args.get("symbol") else []))
    rows = [{"symbol": s, "close": 73.02} for s in symbols]
    return {"ok": True, "result": {"rows": rows, "count": len(rows)}}


async def _fake_execute_fail(name: str, args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return {"ok": False, "error": "取数失败"}


async def _collect(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    script: list[dict[str, Any]],
    execute: Any = _fake_execute,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    captured: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(chat_service, "ai_configured", lambda: True)
    monkeypatch.setattr(chat_service, "is_codex_cli_provider", lambda provider=None: False)
    monkeypatch.setattr(chat_service, "stream_openai_round", _script_round(script, captured))
    monkeypatch.setattr(assistant_tools, "execute_assistant_tool", execute)

    lines = [line async for line in chat_service.chat_stream(
        history=[{"role": "user", "content": question}],
    )]
    return [json.loads(line) for line in lines], captured


def _deltas(events: list[dict[str, Any]]) -> str:
    return "".join(e["content"] for e in events if e["type"] == "delta")


def _notices(events: list[dict[str, Any]]) -> list[str]:
    return [e["message"] for e in events if e["type"] == "notice"]


def _tool_calls(events: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    return [(e["name"], e["args"]) for e in events if e["type"] == "tool_call"]


async def test_fabricated_answer_is_dropped_and_data_is_fetched_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = [
        {"text_pieces": ["五粮液现价 127.91, 涨幅 69%"]},  # 编数, 一个工具都不调
        {"text_pieces": ["五粮液现价 130, 涨 60%"]},  # 催了还是编
        {"text_pieces": ["五粮液最新价 73.02"]},
    ]
    events, captured = await _collect(monkeypatch, "000858.SZ 呢?", script)

    assert "127.91" not in _deltas(events)
    assert "130, 涨 60%" not in _deltas(events)
    assert "73.02" in _deltas(events)
    # 催一次不动 → 代它取数, 工具足迹对用户可见
    assert ("get_stock_quote", {"symbols": ["000858.SZ"]}) in _tool_calls(events)
    # 真实数据进了模型的上下文(第 3 轮请求里有 tool 消息)
    tool_msgs = [m for m in captured[2] if m.get("role") == "tool"]
    assert tool_msgs and "000858.SZ" in tool_msgs[-1]["content"]
    assert _notices(events) == []


async def test_fetched_symbol_is_not_nudged_again(monkeypatch: pytest.MonkeyPatch) -> None:
    script = [
        {"tool_calls": [{"id": "c1", "name": "get_stock_quote",
                         "arguments": '{"symbols": ["000858.SZ"]}'}]},
        {"text_pieces": ["五粮液最新价 73.02"]},
    ]
    events, captured = await _collect(monkeypatch, "000858.SZ 呢?", script)

    assert len(captured) == 2
    assert _deltas(events) == "五粮液最新价 73.02"
    assert _notices(events) == []


async def test_question_without_symbol_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    script = [{"text_pieces": ["今天市场普涨"]}]
    events, captured = await _collect(monkeypatch, "今天市场怎么样", script)

    assert len(captured) == 1
    assert _deltas(events) == "今天市场普涨"
    assert _notices(events) == []


async def test_unverified_numbers_are_flagged_when_fetch_keeps_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = [
        {"text_pieces": ["一"]},
        {"text_pieces": ["二"]},
        {"text_pieces": ["三"]},
        {"text_pieces": ["四"]},
    ]
    events, captured = await _collect(
        monkeypatch, "000858.SZ 呢?", script, execute=_fake_execute_fail,
    )

    assert len(captured) == 4  # 催一次 + 代取两次, 有上限, 不无限循环
    assert _deltas(events) == "四"  # 前几轮的正文被丢掉
    notices = _notices(events)
    assert notices and "未经取数校验" in notices[0]


async def test_multi_symbol_question_requires_each_symbol_to_be_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = [
        {"tool_calls": [{"id": "c1", "name": "get_stock_quote",
                         "arguments": '{"symbols": ["600519.SH"]}'}]},
        {"text_pieces": ["先说茅台 1500"]},  # 只取到一只 → 不算完成, 这段正文先丢掉
        {"tool_calls": [{"id": "c2", "name": "get_stock_quote",
                         "arguments": '{"symbols": ["000858.SZ"]}'}]},
        {"text_pieces": ["茅台 1500, 五粮液 73"]},
    ]
    events, captured = await _collect(
        monkeypatch, "600519.SH 和 000858.SZ 最近怎么样", script,
    )

    assert "先说茅台 1500" not in _deltas(events)  # 第二只没取到前不放行
    assert len(captured) == 4  # 被逼着回去取第二只
    assert captured[2][-1]["role"] == "user"
    assert "000858.SZ" in captured[2][-1]["content"]


class _Repo:
    def get_name_map(self) -> dict[str, str]:
        return {
            "600519.SH": "贵州茅台",
            "000858.SZ": "五 粮 液",
            "000001.SZ": "平安银行",
        }


def test_guard_reads_codes_and_names_from_question() -> None:
    guard = FetchGuard.from_question("600519.SH 和五粮液怎么样", _Repo())
    assert guard.asked == {"600519.SH", "000858.SZ"}


def test_guard_ignores_numbers_that_are_not_securities() -> None:
    guard = FetchGuard.from_question("202611 那个月的行情", _Repo())
    assert guard.asked == set()


def test_guard_covers_symbol_by_tool_arguments() -> None:
    guard = FetchGuard.from_question("600519.SH 最近怎么样", _Repo())
    guard.record_call({"symbol": "600519"})
    assert guard.missing() == set()

    guard = FetchGuard.from_question("600519.SH 最近怎么样", _Repo())
    guard.record_call({"symbols": ["000858.SZ"]})
    assert guard.missing() == {"600519.SH"}


def test_guard_uses_context_symbol_for_pronoun_question() -> None:
    guard = FetchGuard.from_question("它最近怎么样", _Repo(), "600519.SH")
    assert guard.asked == {"600519.SH"}

    # 没点名也没指代 → 不牵扯上下文标的
    guard = FetchGuard.from_question("今天大盘怎么样", _Repo(), "600519.SH")
    assert guard.asked == set()
