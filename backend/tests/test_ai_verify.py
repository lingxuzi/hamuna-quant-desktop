"""生成期实跑自验证 — 跑不通就把报错喂回模型改。

背景: 静态校验 (column_guard) 只看源码, 「polars 表达式误用 and/or、
辅助列没物化」这类问题静态看不出来, 只有按实盘口径真跑才炸。
ai_verify 把实跑结果闭环回生成环节 (app/strategy/ai_verify.py)。
"""
from __future__ import annotations

from app.strategy import ai_verify
from app.strategy.ai_generator import AIStrategyGenerator


class _FakeGenerator:
    """只实现 repair_code —— 记录模型被喂了什么, 返回预设结果。"""

    def __init__(self, *results: dict) -> None:
        self.calls: list[dict] = []
        self._results = list(results)

    async def repair_code(self, code: str, error: str, *, context: str = "结构校验") -> dict:
        self.calls.append({"code": code, "error": error, "context": context})
        return self._results.pop(0) if self._results else {"valid": False, "error": "没有更多结果"}


_OK = {"ok": True, "issues": [], "hits": 3}
_FAIL = {"ok": False, "issues": [{"code": "run_failed", "message": "the truth value of an Expr is ambiguous"}]}

_VALID = {"valid": True, "code": "x = 1", "meta": {"id": "ai_probe"}}


async def test_smoke_ok_does_not_call_model(monkeypatch):
    monkeypatch.setattr(ai_verify, "smoke_generated", lambda code, sid, repo: _OK)
    gen = _FakeGenerator()
    result = await ai_verify.verify_and_repair(gen, dict(_VALID), repo=object())
    assert gen.calls == []
    assert result["smoke"] == _OK


async def test_smoke_failure_feeds_error_back_to_model(monkeypatch):
    seen: list[str] = []

    def _smoke(code, sid, repo):
        seen.append(code)
        return _FAIL if len(seen) == 1 else _OK

    monkeypatch.setattr(ai_verify, "smoke_generated", _smoke)
    fixed = {"valid": True, "code": "fixed = 2", "meta": {"id": "ai_probe"}}
    gen = _FakeGenerator(fixed)
    result = await ai_verify.verify_and_repair(gen, dict(_VALID), repo=object())

    assert len(gen.calls) == 1
    assert "the truth value of an Expr is ambiguous" in gen.calls[0]["error"]
    assert gen.calls[0]["context"] == "实盘口径实跑"
    assert result["code"] == "fixed = 2"
    assert result["smoke"] == _OK


async def test_still_failing_keeps_original_issue(monkeypatch):
    monkeypatch.setattr(ai_verify, "smoke_generated", lambda code, sid, repo: _FAIL)
    gen = _FakeGenerator({"valid": True, "code": "still broken", "meta": {"id": "ai_probe"}})
    result = await ai_verify.verify_and_repair(gen, dict(_VALID), repo=object(), max_rounds=1)

    assert len(gen.calls) == 1
    assert result["smoke"]["ok"] is False


async def test_invalid_result_skips_smoke(monkeypatch):
    called = []
    monkeypatch.setattr(ai_verify, "smoke_generated",
                        lambda *a: called.append(a) or _OK)
    gen = _FakeGenerator()
    result = await ai_verify.verify_and_repair(gen, {"valid": False, "error": "坏的"}, repo=object())
    assert called == []
    assert gen.calls == []
    assert "smoke" not in result


async def test_no_repo_skips_smoke(monkeypatch):
    called = []
    monkeypatch.setattr(ai_verify, "smoke_generated",
                        lambda *a: called.append(a) or _OK)
    gen = _FakeGenerator()
    await ai_verify.verify_and_repair(gen, dict(_VALID), repo=None)
    assert called == []
    assert gen.calls == []


def test_syntax_error_triggers_structural_repair():
    assert AIStrategyGenerator.needs_structural_repair({"error": "Python 语法错误: invalid syntax"})


def test_safety_violation_triggers_structural_repair():
    """安全白名单拦截也走修复轮: 模型顺手 import 越界模块时让它自己改。"""
    assert AIStrategyGenerator.needs_structural_repair(
        {"error": "禁止 import os (不在策略安全白名单)"}
    )


def test_history_backend_with_filter_entry_is_rejected():
    """python_history_legacy 又定义 filter() → 生成期就拦 (引擎加载期会拒)。"""
    from app.strategy.ai_generator import _strategy_entrypoint_error

    code = (
        'import polars as pl\n'
        'META = {"id": "ai_x", "name": "x", "description": "x", "scoring": {}}\n'
        'EXECUTION_BACKEND = "python_history_legacy"\n'
        "LOOKBACK_DAYS = 5\n"
        "def filter_history(df, params):\n    return df\n"
        "def filter(df, params):\n    return pl.lit(True)\n"
    )
    error = _strategy_entrypoint_error(code)
    assert error and "filter_history" in error
    assert AIStrategyGenerator.needs_structural_repair({"error": error})


async def test_build_endpoint_runs_verify_when_repo_present(monkeypatch):
    """有行情仓库时, /build 必须走实跑校验 (跑不通才找模型改)。"""
    from types import SimpleNamespace

    from app.api.strategy import BuildRequest, build_strategy

    async def fake_generate(self, prompt):
        return {"valid": True, "code": "code", "meta": {"id": "ai_x"}}

    monkeypatch.setattr(AIStrategyGenerator, "generate", fake_generate)
    seen: list[int] = []

    def _smoke(code, sid, repo):
        seen.append(len(seen))
        return _FAIL if len(seen) == 1 else _OK

    monkeypatch.setattr(ai_verify, "smoke_generated", _smoke)
    calls: list[str] = []

    async def fake_repair(self, code, error, *, context="结构校验"):
        calls.append(context)
        return {"valid": True, "code": "fixed", "meta": {"id": "ai_x"}}

    monkeypatch.setattr(AIStrategyGenerator, "repair_code", fake_repair)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(repo=object())))
    req = BuildRequest(step=1, name="n", description="d", direction="long", rules="r")

    result = await build_strategy(req, request)
    assert calls == ["实盘口径实跑"]
    assert result["code"] == "fixed"


async def test_build_endpoint_skips_verify_without_repo(monkeypatch):
    from types import SimpleNamespace

    from app.api.strategy import BuildRequest, build_strategy

    async def fake_generate(self, prompt):
        return {"valid": True, "code": "code", "meta": {"id": "ai_x"}}

    monkeypatch.setattr(AIStrategyGenerator, "generate", fake_generate)
    called = []
    monkeypatch.setattr(ai_verify, "smoke_generated", lambda *a: called.append(a) or _OK)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    req = BuildRequest(step=1, name="n", description="d", direction="long", rules="r")

    result = await build_strategy(req, request)
    assert called == []
    assert "smoke" not in result
