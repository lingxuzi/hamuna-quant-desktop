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
from app.custom.assistant.fetch_guard import _MARKET_NUMBER_RE, FetchGuard


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
    repo: Any = None,
    experts: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]]]:
    captured: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(chat_service, "ai_configured", lambda: True)
    monkeypatch.setattr(chat_service, "is_codex_cli_provider", lambda provider=None: False)
    monkeypatch.setattr(chat_service, "stream_openai_round", _script_round(script, captured))
    monkeypatch.setattr(assistant_tools, "execute_assistant_tool", execute)

    lines = [line async for line in chat_service.chat_stream(
        history=[{"role": "user", "content": question}],
        repo=repo,
        experts=experts,
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


async def test_market_question_must_fetch_market_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """没点名标的的行情问题同样得有数据兜底 —— 守卫不能只认具名标的。"""
    script = [
        {"text_pieces": ["今天上证涨 2.3%, 成交 1.2 万亿"]},  # 凭记忆写的
        {"tool_calls": [{"id": "c1", "name": "get_market_overview", "arguments": "{}"}]},
        {"text_pieces": ["今天上证 3200 点 (来自 get_market_overview)。"]},
    ]
    events, captured = await _collect(monkeypatch, "今天大盘怎么样", script)

    assert len(captured) == 3
    assert "2.3%" not in _deltas(events)
    assert "1.2 万亿" not in _deltas(events)
    assert "3200 点" in _deltas(events)
    assert ("get_market_overview", {}) in _tool_calls(events)


async def test_market_numbers_without_any_data_call_are_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取数工具全失败时, 编出来的行情数字不能交给用户。"""
    script = [{"text_pieces": ["今天上证涨 2.3%"]} for _ in range(4)]
    events, _ = await _collect(
        monkeypatch, "今天大盘怎么样", script, execute=_fake_execute_fail,
    )

    assert _deltas(events) == ""
    assert any("没有数据出处" in msg for msg in _notices(events))
    assert not any(e["type"] == "error" for e in events)


async def test_conceptual_question_is_not_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """概念题不该被当成行情问题拦下 —— 「涨停是 10%」是规则, 不是市场状态。"""
    script = [{"text_pieces": ["涨停就是当日涨幅达到 10%。"]}]
    events, captured = await _collect(monkeypatch, "涨停是什么意思", script)

    assert len(captured) == 1
    assert _deltas(events) == "涨停就是当日涨幅达到 10%。"
    assert _notices(events) == []


async def test_plain_count_is_not_treated_as_market_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """纯个数不算行情数字 —— 但「我有几个策略」是问自己的数据, 得查了再说。"""
    script = [
        {"text_pieces": ["你有 1 个策略。"]},  # 凭记忆说的
        {"tool_calls": [{"id": "c1", "name": "list_strategies", "arguments": "{}"}]},
        {"text_pieces": ["你有 2 个策略。"]},
    ]
    events, captured = await _collect(monkeypatch, "我有几个策略", script)

    assert len(captured) == 3
    assert "你有 1 个策略" not in _deltas(events)
    assert "你有 2 个策略" in _deltas(events)
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


def test_guard_still_sees_bare_codes_when_name_map_read_fails() -> None:
    """维表读取失败时不能整个失灵 —— 守卫失灵 = 模型爱编数字就编数字。"""

    class _BrokenMap(_Repo):
        def get_name_map(self) -> dict[str, str]:
            raise RuntimeError("维表读不出来")

    guard = FetchGuard.from_question("600519 最近怎么样", _BrokenMap())
    assert guard.asked == {"600519.SH"}

    # 年月仍要排除, 否则 "202611" 会被当成深市代码
    guard = FetchGuard.from_question("202611 那个月的行情", _BrokenMap())
    assert guard.asked == set()


def test_guard_without_repo_only_trusts_codes_with_suffix() -> None:
    """没有 repo 时只认带交易所后缀的代码 —— 没维表就没法排除误命中。"""
    guard = FetchGuard.from_question("600519 最近怎么样", None)
    assert guard.asked == set()

    guard = FetchGuard.from_question("600519.SH 最近怎么样", None)
    assert guard.asked == {"600519.SH"}


def test_guard_covers_symbol_by_tool_arguments() -> None:
    guard = FetchGuard.from_question("600519.SH 最近怎么样", _Repo())
    guard.record_call("get_stock_quote", {"symbol": "600519"})
    assert guard.missing() == set()

    guard = FetchGuard.from_question("600519.SH 最近怎么样", _Repo())
    guard.record_call("get_stock_quote", {"symbols": ["000858.SZ"]})
    assert guard.missing() == {"600519.SH"}


def test_guard_uses_context_symbol_for_pronoun_question() -> None:
    guard = FetchGuard.from_question("它最近怎么样", _Repo(), "600519.SH")
    assert guard.asked == {"600519.SH"}

    # 没点名也没指代 → 不牵扯上下文标的
    guard = FetchGuard.from_question("今天大盘怎么样", _Repo(), "600519.SH")
    assert guard.asked == set()


# --- 基本面取数: 行情取到了 ≠ 财务取到了 ---

class _AssetRepo(_Repo):
    """带资产类型的 repo: 只有 000001.SZ 是 ETF/指数, 其余按个股。"""

    def resolve_asset_type(self, symbol: str) -> str:
        return "stock" if symbol != "510300.SH" else "etf"


def test_analysis_question_requires_financials() -> None:
    """「分析X」= 要基本面: 只调了行情工具不算取完。"""
    guard = FetchGuard.from_question("分析600519.SH", _AssetRepo())
    assert guard.wants_financials
    guard.record_call("get_stock_quote", {"symbol": "600519"})
    guard.record_call("get_stock_daily", {"symbol": "600519"})
    # 复现过的失效: 只有行情, 正文却写出整张财务表 (数字全是编的)
    assert guard.incomplete()

    guard.record_call("get_financials", {"symbol": "600519.SH"})
    assert not guard.incomplete()


def test_financials_not_required_for_narrow_question() -> None:
    """只问走势不算基本面问题 —— 否则每次问价都被逼着取财务。"""
    guard = FetchGuard.from_question("600519.SH 最近走势怎样", _AssetRepo())
    assert not guard.wants_financials


@pytest.mark.parametrize("question", [
    "600519.SH 值不值",
    "600519.SH 能买吗",
    "600519.SH 贵不贵",
])
def test_valuation_phrasing_also_requires_financials(question: str) -> None:
    """换个说法问估值 (「能买吗」「贵不贵」) 同样要财务数据, 否则模型会自己编。"""
    assert FetchGuard.from_question(question, _AssetRepo()).wants_financials


def test_financials_not_required_for_etf() -> None:
    """ETF/指数没有三张表, 要求了也取不到。"""
    guard = FetchGuard.from_question("分析510300.SH", _AssetRepo())
    assert guard.asked == {"510300.SH"}
    assert not guard.wants_financials


def test_nudge_text_covers_the_financials_gap() -> None:
    guard = FetchGuard.from_question("分析600519.SH", _AssetRepo())
    guard.record_call("get_stock_quote", {"symbol": "600519"})
    text = guard.nudge_text()
    assert "get_financials" in text
    assert "600519.SH" in text

    guard.record_call("get_financials", {"symbol": "600519"})
    # 财务齐了只剩标的缺口时不再提财务
    assert "get_financials" not in guard.nudge_text() or not guard.missing_financials()


def test_financials_call_does_not_count_as_quote_coverage() -> None:
    """get_financials 只给财务数字, 不含价格 —— 拿它顶行情覆盖等于放过编价格。"""
    guard = FetchGuard.from_question("分析600519.SH", _AssetRepo())
    guard.record_call("get_financials", {"symbol": "600519.SH"})
    assert guard.missing() == {"600519.SH"}
    assert not guard.missing_financials()


def test_force_call_picks_quote_first_then_financials() -> None:
    """宿主代取: 先行情后财务, 两者都要真数据。"""
    guard = FetchGuard.from_question("分析600519.SH", _AssetRepo())
    assert guard.force_call() == ("get_stock_quote", {"symbols": ["600519.SH"]})

    guard.record_call("get_stock_quote", {"symbol": "600519.SH"})
    assert guard.force_call() == (
        "get_financials",
        {"symbol": "600519.SH", "table": "metrics", "periods": 4},
    )

    guard.record_call("get_financials", {"symbol": "600519.SH"})
    assert not guard.incomplete()
    assert guard.force_call() is None


async def test_missing_financials_triggers_nudge_and_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型只调行情工具就收尾 → 催它取财务; 催不动 → 放行并标注未校验。"""
    script = [
        {"tool_calls": [{"id": "c1", "name": "get_stock_quote",
                         "arguments": '{"symbols": ["000858.SZ"]}'}]},
        {"text_pieces": ["毛利率 4.98%, ROE -17.95%"]},  # 没调 get_financials 就写财务
        {"text_pieces": ["毛利率 24.16%, ROE -586%"]},
    ]
    events, captured = await _collect(
        monkeypatch, "分析000858.SZ", script, repo=_AssetRepo(),
    )

    assert "4.98%" not in _deltas(events)  # 财务没取到时正文先扣着
    # 第 2 轮起请求里必须出现「催取财务」的 user 消息
    nudged = [
        m for batch in captured[1:]
        for m in batch
        if m.get("role") == "user" and "get_financials" in str(m.get("content"))
    ]
    assert nudged


async def test_financials_are_fetched_forced_when_the_model_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """模型死活不调 get_financials → 宿主代取, 数字才有出处。"""
    script = [
        {"text_pieces": ["毛利率 4.98%"]},  # 编数, 一个工具都不调
        {"text_pieces": ["毛利率 4.98%"]},  # 催了还是编 → 宿主代取行情
        {"text_pieces": ["毛利率 4.98%"]},  # 还是不调财务 → 宿主代取财务
        {"text_pieces": ["毛利率 24.16%"]},  # 数据到手, 才放行
    ]
    events, _captured = await _collect(
        monkeypatch, "分析000858.SZ", script, repo=_AssetRepo(),
    )

    names = [name for name, _ in _tool_calls(events)]
    assert "get_stock_quote" in names
    assert "get_financials" in names
    assert "4.98%" not in _deltas(events)  # 编的数字没被放行
    assert "24.16%" in _deltas(events)


async def test_nudge_keeps_the_expert_reminder_in_front(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """催取数的消息会落到最后一条 —— 专家框架不能被挤到前一条消息里。"""
    monkeypatch.setattr(
        chat_service.assistant_experts,
        "referenced_bodies",
        lambda data_dir, names: [{"name": "buffett", "title": "巴菲特价值投资视角"}],
    )
    script = [
        {"text_pieces": ["现价 127.91"]},  # 没取数就收尾 → 扣下正文并催取数
        {"tool_calls": [{"id": "c1", "name": "get_stock_quote",
                         "arguments": '{"symbols": ["000858.SZ"]}'}]},
        {"text_pieces": ["五粮液最新价 73.02"]},
    ]
    _events, captured = await _collect(
        monkeypatch, "000858.SZ 呢?", script, experts=["buffett"],
    )

    nudge = [m for m in captured[1] if m.get("role") == "user"][-1]["content"]
    assert "巴菲特价值投资视角" in nudge  # 专家框架跟着走到最后一条
    assert "get_stock_quote" in nudge


# --- 当下行情类提问: 没点名标的也得有数据兜底 ---

def test_market_question_is_detected() -> None:
    """时间词 + 行情词 = 当下行情问题, 清单里要加市场数据。"""
    guard = FetchGuard.from_question("今天大盘怎么样", _Repo())
    assert guard.wants_market
    assert guard.missing_market()
    assert guard.incomplete()

    guard.record_call("get_market_overview", {})
    assert not guard.missing_market()
    assert not guard.incomplete()


def test_conceptual_question_is_not_a_market_question() -> None:
    """概念题不算行情问题 —— 否则「涨停是什么意思」会被逼着取数。"""
    guard = FetchGuard.from_question("涨停是什么意思", _Repo())
    assert not guard.wants_market
    assert not guard.incomplete()


@pytest.mark.parametrize("text", [
    "今天上证涨 2.3%",
    "成交额 1.2 万亿",
    "现价 73.02 元",
    "市盈率 20 倍",
    "上证 3200 点",
    "成交 5 万手",
    "推荐 3 只",
])
def test_market_numbers_are_detected(text: str) -> None:
    """带量纲的数字才算行情数字。"""
    assert _MARKET_NUMBER_RE.search(text)


@pytest.mark.parametrize("text", [
    "你有 1 个策略。",
    "近 60 日震荡",
    "第 3 期财报",
    "2024 年的行情",
    "今天市场普涨",
])
def test_plain_numbers_are_not_market_numbers(text: str) -> None:
    """纯个数、年月、期数都不是行情数字。"""
    assert not _MARKET_NUMBER_RE.search(text)


def test_unverified_numbers_needs_numbers_and_no_data_call() -> None:
    """有数字 + 没取过数 = 没出处的数字。"""
    guard = FetchGuard.from_question("今天大盘怎么样", _Repo())
    assert guard.unverified_numbers("今天上证涨 2.3%")
    assert not guard.unverified_numbers("今天市场普涨")

    guard.record_call("get_market_overview", {})
    assert not guard.unverified_numbers("今天上证涨 2.3%")


def test_unverified_numbers_ignores_non_data_tools() -> None:
    """加载技能/写操作不算取数 —— 它们不产出行情数字。"""
    guard = FetchGuard.from_question("今天大盘怎么样", _Repo())
    guard.record_call("load_skill", {"name": "buffett"})
    assert guard.unverified_numbers("今天上证涨 2.3%")


def test_named_symbol_does_not_force_a_market_wide_fetch() -> None:
    """点了名的标的走标的清单 —— 不该再逼模型拉整个市场 (「最近估值」也是标的自己的事)。"""
    guard = FetchGuard.from_question("600519.SH 最近估值怎么样", _AssetRepo())
    assert guard.asked == {"600519.SH"}
    assert not guard.wants_market
    assert not guard.missing_market()


# --- 用户自己的策略/信号/持仓: 也要有工具兜底 ---

def test_own_data_question_is_detected() -> None:
    """问自己的策略/持仓, 必须调工具 —— 模型能凭空写一个收益率。"""
    guard = FetchGuard.from_question("我的策略最近收益怎么样", _Repo())
    assert guard.wants_own_data
    assert guard.missing_own_data()
    assert guard.incomplete()

    guard.record_call("list_strategies", {})
    assert not guard.missing_own_data()
    assert not guard.incomplete()


def test_definition_question_about_strategies_is_not_own_data() -> None:
    """概念题不算 —— 「什么是网格策略」不该逼着拉自己的策略列表。"""
    guard = FetchGuard.from_question("什么是网格策略", _Repo())
    assert not guard.wants_own_data


async def test_own_data_numbers_without_a_tool_call_are_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取不到自己数据时, 编出来的收益率不能交给用户。"""
    script = [{"text_pieces": ["你的策略近一年收益率 23.5%, 回撤 12%。"]} for _ in range(4)]
    events, _ = await _collect(
        monkeypatch, "我的策略最近收益怎么样", script, execute=_fake_execute_fail,
    )

    assert _deltas(events) == ""
    assert any("没有数据出处" in msg for msg in _notices(events))
