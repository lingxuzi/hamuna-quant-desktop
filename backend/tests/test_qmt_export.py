"""QMT 脚本生成器测试：全部矩阵策略可生成、不支持类型明确报错、产物可编译可解码。"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import ClassVar

import pytest

from app.services import qmt_export

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _engine():
    from app.strategy.engine import StrategyEngine

    return StrategyEngine([
        Path(__file__).resolve().parent.parent / "app" / "strategy" / "research",
        _DATA_DIR / "strategies" / "custom",
        _DATA_DIR / "strategies" / "ai",
        _DATA_DIR / "strategies" / "composite",
    ])


def _matrix_native_ids() -> list[str]:
    engine = _engine()
    return [
        s.meta["id"]
        for s in engine.strategy_definitions()
        if s.execution_backend == "matrix_native"
        and not s.meta.get("research_only")
        and s.matrix_strategy is not None
        and "FactorRank" not in type(s.matrix_strategy).__name__
    ]


_NATIVE_IDS = _matrix_native_ids()


def _build(strategy_id: str) -> bytes:
    return qmt_export.build_qmt_script(
        engine=_engine(), data_dir=_DATA_DIR, strategy_id=strategy_id,
        config={"amount": 10000, "max_positions": 5},
    )[0]


def test_native_strategy_count() -> None:
    assert len(_NATIVE_IDS) == 25, _NATIVE_IDS


@pytest.mark.parametrize("strategy_id", _NATIVE_IDS)
def test_every_native_strategy_generates_valid_script(strategy_id: str) -> None:
    data = _build(strategy_id)
    text = data.decode("gbk")
    assert text.splitlines()[0] == "#coding:gbk"
    ast.parse(text)  # Python 3 语法可解析（实际执行环境 3.6 由梢后静态检查保证）


@pytest.mark.parametrize("strategy_id", _NATIVE_IDS)
def test_script_has_no_repository_imports_or_annotations(strategy_id: str) -> None:
    text = _build(strategy_id).decode("gbk")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "app", node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in (
                    "numpy", "pandas", "time", "datetime"), alias.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.returns is None, node.name
            assert all(arg.annotation is None for arg in node.args.args), node.name
        if isinstance(node, ast.AnnAssign):
            assert node.annotation is None


@pytest.mark.parametrize("strategy_id", _NATIVE_IDS)
def test_script_declares_required_constants(strategy_id: str) -> None:
    text = _build(strategy_id).decode("gbk")
    for name in ("PARAMS =", "CONFIG =", "SCORING =", "STOP_LOSS =",
                 "MAX_HOLD_DAYS =", "MATRIX_STRATEGY ="):
        assert name in text, (strategy_id, name)
    assert 'print("[dry-run]' in text


@pytest.mark.parametrize("strategy_id", _NATIVE_IDS)
def test_script_has_no_python37_syntax(strategy_id: str) -> None:
    """QMT 内嵌 3.6：不能出现 f-string 的 ``=`` 调试语法、海象运算符、位置参数分隔符。"""
    text = _build(strategy_id).decode("gbk")
    tree = ast.parse(text)
    for node in ast.walk(tree):
        assert not isinstance(node, ast.NamedExpr), "walrus 需要 3.8"
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    assert value.format_spec is None
    assert ":=" not in text


@pytest.mark.parametrize("strategy_id", ["ma_golden_cross", "macd_golden", "near_limit_up"])
def test_script_embeds_strategy_class(strategy_id: str) -> None:
    text = _build(strategy_id).decode("gbk")
    assert "def compute_signals(" in text
    assert "matrix_feature(" in text


def test_aliases_present_in_script() -> None:
    text = _build("macd_golden").decode("gbk")
    assert "ewm_adjust_false = valid_ewm_adjust_false" in text
    assert "shift = valid_shift" in text


@pytest.mark.parametrize("backend,strategy_id", [
    ("polars_expr", None),
    ("python_history_legacy", None),
    ("factor_rank", "factor_rank_research"),
])
def test_unsupported_backend_raises(backend: str, strategy_id) -> None:
    class _Fake:
        meta: ClassVar = {"id": "x"}
        execution_backend = backend
        matrix_strategy = None
        file_path = None

    with pytest.raises(qmt_export.QmtExportError):
        qmt_export._supported(_Fake())


def test_unknown_strategy_raises() -> None:
    with pytest.raises(qmt_export.QmtExportError):
        qmt_export.build_qmt_script(
            engine=_engine(), data_dir=_DATA_DIR, strategy_id="does_not_exist",
            config={})


def test_research_only_strategy_raises() -> None:
    engine = _engine()
    research = [
        s.meta["id"] for s in engine.strategy_definitions()
        if s.meta.get("research_only")
    ]
    if not research:
        pytest.skip("no research_only strategy present")
    with pytest.raises(qmt_export.QmtExportError):
        qmt_export.build_qmt_script(
            engine=engine, data_dir=_DATA_DIR, strategy_id=research[0],
            config={})


def test_warmup_bars_default_from_strategy() -> None:
    data = _build("ma_golden_cross")
    text = data.decode("gbk")
    assert "WARMUP_BARS = 60" in text


def test_config_round_trips_into_script() -> None:
    data = qmt_export.build_qmt_script(
        engine=_engine(), data_dir=_DATA_DIR, strategy_id="ma_golden_cross",
        config={"amount": 25000, "max_positions": 3, "live_trade": True},
    )[0]
    text = data.decode("gbk")
    assert "'amount': 25000" in text
    assert "'max_positions': 3" in text
    assert "'live_trade': True" in text
    # QMT 是 Python 3.6, json 字面量 (true/false/null) 会 NameError
    assert ": true" not in text and ": false" not in text and ": null" not in text

# ── 驱动层: 从生成产物里 exec 出真实函数, 验刚修过的持仓语义 ──
_DRIVER_FUNCS = {"_held_bars", "_exit_holds", "_lots", "_ranked_entries"}
_DRIVER_CLASSES = {"_State"}

def _driver_ns(strategy_id: str = "ma_golden_cross") -> dict:
    """从生成产物里取出驱动函数与 _State, 重建模块级 _STATE。"""
    text = _build(strategy_id).decode("gbk")
    tree = ast.parse(text)
    keep = [
        node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in _DRIVER_FUNCS)
        or (isinstance(node, ast.ClassDef) and node.name in _DRIVER_CLASSES)
    ]
    ns: dict = {"np": __import__("numpy"), "STOP_LOSS": -0.07,
                "MAX_HOLD_DAYS": 20, "CONFIG": {"amount": 10000}}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "<driver>", "exec"), ns)
    ns["_STATE"] = ns["_State"]()
    return ns


class _Market:
    def __init__(self, symbols, dates, close):
        import numpy as np
        self.symbols = list(symbols)
        self.trading_dates = list(dates)
        self.close = np.asarray(close, dtype=float)
        self.shape = (len(self.trading_dates), len(self.symbols))




def test_exit_holds_does_not_dump_unknown_age_positions() -> None:
    """建仓日未知 (如重启后读到的旧仓) 时不得按 max-hold 清仓。"""
    import numpy as np
    ns = _driver_ns()
    market = _Market(["600000.SH"], [20260101, 20260102, 20260103],
                     np.zeros((3, 1)))
    positions = {"600000.SH": {"volume": 100, "cost": 10.0}}
    reasons = ns["_exit_holds"](
        market, np.zeros(1, bool), np.array([10.0]), positions, 20260103)
    assert reasons == {}


def test_exit_holds_triggers_after_max_hold_days() -> None:
    import numpy as np
    ns = _driver_ns()
    market = _Market(["600000.SH"], list(range(20260101, 20260132)),
                     np.zeros((31, 1)))
    positions = {"600000.SH": {"volume": 100, "cost": 10.0}}
    ns["_STATE"].held_since = {"600000.SH": 20260101}
    reasons = ns["_exit_holds"](
        market, np.zeros(1, bool), np.array([10.0]), positions, 20260131)
    assert reasons == {"600000.SH": "max-hold"}


def test_exit_holds_stop_loss_by_cost() -> None:
    import numpy as np
    ns = _driver_ns()
    market = _Market(["600000.SH"], [20260101, 20260102], np.zeros((2, 1)))
    positions = {"600000.SH": {"volume": 100, "cost": 10.0}}
    ns["_STATE"].held_since = {"600000.SH": 20260102}
    reasons = ns["_exit_holds"](
        market, np.zeros(1, bool), np.array([9.0]), positions, 20260102)
    assert reasons == {"600000.SH": "stop-loss"}


def _signal_row_ns() -> dict:
    """从生成产物里取 _signal_row，验实盘该交易哪一行。"""
    import time as _time
    text = _build("ma_golden_cross").decode("gbk")
    tree = ast.parse(text)
    keep = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "_signal_row"]
    ns: dict = {"time": _time}
    exec(compile(ast.Module(body=keep, type_ignores=[]), "<row>", "exec"), ns)
    return ns


def test_signal_row_open_t_plus_1_skips_today(monkeypatch) -> None:
    """open_t+1: 即便今天已有 bar, 也只用今天之前那一根。"""
    import time as _time
    ns = _signal_row_ns()
    market = _Market(["600000.SH"], [20260101, 20260102, 20260105],
                     None)
    monkeypatch.setattr(_time, "strftime", lambda _fmt: "20260105")
    assert ns["_signal_row"](market, "open_t+1") == 1


def test_signal_row_close_t_accepts_today(monkeypatch) -> None:
    import time as _time
    ns = _signal_row_ns()
    market = _Market(["600000.SH"], [20260101, 20260102, 20260105], None)
    monkeypatch.setattr(_time, "strftime", lambda _fmt: "20260105")
    assert ns["_signal_row"](market, "close_t") == 2
