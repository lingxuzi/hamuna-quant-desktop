"""QMT 导出产物的规范门禁 —— 用 qmt_lint 校验全部矩阵策略。

两条断言缺一不可:
1. 正向: 仓库生成的每一份脚本零违规 (回归守卫, 防止模板改动引入不合规写法)
2. 反向: 每条规则都有一段反例能让它报警 (防止校验器写坏了却静默通过)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from seeded_env import SEEDED_DATA_DIR

from app.services import qmt_lint

# 内置策略在运行时住在 data/strategies/custom (由 app/strategy/seeds 复制而来),
# 而 data/ 不进 Git —— 干净环境里没有它们, 这里用已种入种子的临时目录。
_DATA_DIR = SEEDED_DATA_DIR


def _engine():
    from app.strategy.engine import StrategyEngine

    return StrategyEngine([
        Path(__file__).resolve().parent.parent / "app" / "strategy" / "research",
        _DATA_DIR / "strategies" / "custom",
        _DATA_DIR / "strategies" / "ai",
        _DATA_DIR / "strategies" / "composite",
    ])


def _native_ids() -> list[str]:
    engine = _engine()
    return [
        s.meta["id"]
        for s in engine.strategy_definitions()
        if s.execution_backend == "matrix_native"
        and s.matrix_strategy is not None
        and not s.meta.get("research_only")
    ]


_NATIVE_IDS = _native_ids()


def _generate(strategy_id: str) -> str:
    from app.services import qmt_export

    data = qmt_export.build_qmt_script(
        engine=_engine(), data_dir=_DATA_DIR, strategy_id=strategy_id,
        config={"amount": 10000, "max_positions": 5},
    )[0]
    return data.decode("gbk")


@pytest.mark.parametrize("strategy_id", _NATIVE_IDS)
def test_generated_script_passes_qmt_lint(strategy_id: str) -> None:
    issues = qmt_lint.lint_text(_generate(strategy_id))
    assert not issues, "\n".join(str(i) for i in issues)


def test_gbk_roundtrip_of_generated_script() -> None:
    """产物必须是 QMT 能按 GBK 读回的字节, 而不是仅 Unicode 可解码。"""
    from app.services import qmt_export

    raw = qmt_export.build_qmt_script(
        engine=_engine(), data_dir=_DATA_DIR, strategy_id="macd_golden",
        config={},
    )[0]
    issues = qmt_lint.lint_file_bytes(raw)
    assert not issues, "\n".join(str(i) for i in issues)


# ── 反向: 每条规则都要真的会报警 ──────────────────────────────────
_BAD_HEADER = "#coding:gbk\n"


def _lint(source: str) -> list[str]:
    return [i.rule for i in qmt_lint.lint_text(_BAD_HEADER + source)]


def test_encoding_rule_fires_on_missing_header() -> None:
    issues = qmt_lint.lint_text("import numpy\n")
    assert "encoding" in [i.rule for i in issues]


def test_py36_rule_fires_on_annotations_and_walrus() -> None:
    rules = _lint(
        "def init(C: object) -> None:\n"
        "    pass\n"
        "def handlebar(C):\n"
        "    if (n := 1):\n"
        "        pass\n"
    )
    assert rules.count("py36") >= 3


def test_py36_rule_fires_on_future_annotations() -> None:
    rules = _lint("from __future__ import annotations\n")
    assert "py36" in rules


def test_ctx_rule_fires_on_contextinfo_write() -> None:
    rules = _lint(
        "def init(C):\n    C.cache = {}\n"
        "def handlebar(C):\n    C.cache['a'] = 1\n"
    )
    assert rules.count("ctx") >= 1


def test_after_init_rule_fires_on_sector_in_init() -> None:
    rules = _lint(
        "def init(C):\n    C.l = C.get_stock_list_in_sector('沪深A股')\n"
        "def handlebar(C):\n    pass\n"
    )
    assert "after_init" in rules


def test_fetch_rule_fires_on_init_market_data_and_kwargs() -> None:
    rules = _lint(
        "def init(C):\n    C.d = C.get_market_data_ex(fields=['close'], stocks=['x'])\n"
        "def handlebar(C):\n    pass\n"
    )
    assert rules.count("fetch") >= 2


def test_schedule_rule_fires_on_none_interval() -> None:
    rules = _lint(
        "def init(C):\n"
        "    C.schedule_run(on_t, '20260101093200', -1, None, 't')\n"
        "def handlebar(C):\n    pass\n"
    )
    assert "schedule" in rules


def test_schedule_rule_fires_on_bad_time_point() -> None:
    rules = _lint(
        "def init(C):\n"
        "    C.schedule_run(on_t, '%Y%m%d093200', -1, None, 't')\n"
        "def handlebar(C):\n    pass\n"
    )
    assert "schedule" in rules


def test_passorder_rule_fires_on_bad_enums() -> None:
    rules = _lint(
        "def init(C):\n    pass\n"
        "def handlebar(C):\n"
        "    passorder(99, 9999, 'a', 'x', 99, -1, 100, 's', 2, 'r', C)\n"
    )
    assert rules.count("passorder") >= 3


def test_passorder_rule_fires_on_short_arg_list() -> None:
    rules = _lint(
        "def init(C):\n    pass\n"
        "def handlebar(C):\n"
        "    passorder(23, 1101, 'a', 'x', 5, -1, 100)\n"
    )
    assert "passorder" in rules


def test_blocking_rule_fires_on_sleep() -> None:
    rules = _lint(
        "def init(C):\n    pass\n"
        "def handlebar(C):\n    import time\n    time.sleep(1)\n"
    )
    assert "blocking" in rules


def test_thread_rule_fires_on_threading_import() -> None:
    rules = _lint(
        "import threading\n"
        "def init(C):\n    pass\n"
        "def handlebar(C):\n    pass\n"
    )
    assert "thread" in rules


def test_volume_rule_fires_on_non_lot_size() -> None:
    rules = _lint(
        "def init(C):\n    pass\n"
        "def handlebar(C):\n"
        "    passorder(23, 1101, 'a', 'x', 5, -1, 150, 's', 2, 'r', C)\n"
    )
    assert "volume" in rules


def test_entry_rule_fires_on_missing_handlebar() -> None:
    rules = _lint("def init(C):\n    pass\n")
    assert "entry" in rules


def test_tick_rule_fires_on_unknown_field() -> None:
    rules = _lint(
        "def init(C):\n    pass\n"
        "def handlebar(C):\n"
        "    tick = C.get_full_tick(['x'])\n"
        "    tick.get('bogusField')\n"
    )
    assert "tick" in rules


def test_unparseable_file_reports_syntax_error() -> None:
    issues = qmt_lint.lint_text(_BAD_HEADER + "def (\n")
    assert any(i.rule == "py36" and "语法" in i.message for i in issues), issues