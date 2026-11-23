"""保存期实跑 smoke — 契约测试 (不依赖真实行情)。"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

import app.services.screener as screener_module
from app.api.strategy import StrategyCodeSaveRequest, save_strategy_code_core
from app.strategy.engine import StrategyEngine
from app.strategy.smoke import smoke_message, smoke_strategy

_VALID_CODE = '''"""冒烟用策略。"""

import polars as pl

META = {
    "id": "custom_smoke_demo",
    "name": "冒烟策略",
    "description": "收盘价站上20日均线",
    "asset_types": ["stock"],
    "timeframes": ["1d"],
    "params": [],
    "scoring": {},
    "order_by": "close",
    "descending": True,
    "limit": 20,
}

EXECUTION_BACKEND = "polars_expr"


def filter(df: pl.DataFrame, params: dict) -> pl.Expr:
    return pl.col("close") > pl.col("ma20")
'''


class _FakeEngine:
    """只实现 smoke 用到的两个方法。"""

    def __init__(self, *, backend: str = "polars_expr", error: Exception | None = None, rows=()):
        self.backend = backend
        self.error = error
        self.rows = list(rows)
        self.ran: list[str] = []

    def get(self, strategy_id):
        return SimpleNamespace(execution_backend=self.backend)

    def run(self, strategy_id, context):
        self.ran.append(strategy_id)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(rows=self.rows)


class _FakeService:
    """替代 ScreenerService: 只提供 smoke 用到的两个方法。"""

    latest: date | None = date(2026, 1, 5)
    context_error: Exception | None = None

    def __init__(self, repo):
        self.repo = repo

    def latest_date(self):
        return type(self).latest

    def build_strategy_context(self, engine, as_of, strategy_ids):
        if type(self).context_error is not None:
            raise type(self).context_error
        return SimpleNamespace(as_of=as_of)


@pytest.fixture
def fake_service(monkeypatch):
    monkeypatch.setattr(screener_module, "ScreenerService", _FakeService)
    monkeypatch.setattr(_FakeService, "latest", date(2026, 1, 5))
    monkeypatch.setattr(_FakeService, "context_error", None)
    return _FakeService


def test_runtime_failure_is_reported(fake_service):
    engine = _FakeEngine(error=ValueError("策略 x 引用了不存在的列「_ma5」"))
    result = smoke_strategy(engine, object(), "x")
    assert result["ok"] is False
    assert result["issues"][0]["code"] == "run_failed"
    assert "_ma5" in result["issues"][0]["message"]


def test_success_reports_hit_count(fake_service):
    engine = _FakeEngine(rows=[{"symbol": "600001.SH"}, {"symbol": "600002.SH"}])
    result = smoke_strategy(engine, object(), "x")
    assert result["ok"] is True
    assert result["hits"] == 2
    assert result["as_of"] == "2026-01-05"


def test_matrix_backend_runs_and_reports_hits(fake_service):
    """矩阵策略不再跳过实跑: 轴序写错时跑得通但 0 命中, 必须真跑才发现。"""
    engine = _FakeEngine(backend="matrix_native", rows=[{"symbol": "600001.SH"}])
    result = smoke_strategy(engine, object(), "x")
    assert result["ok"] is True
    assert result["skipped"] is None
    assert engine.ran == ["x"]


def test_matrix_zero_hits_over_window_is_blocked(fake_service, monkeypatch):
    """当日 0 命中且整窗无信号 → 判定策略失效 (轴序写错的典型症状)。"""
    import app.strategy.smoke as smoke_module

    engine = _FakeEngine(backend="matrix_native")
    monkeypatch.setattr(smoke_module, "_window_entry_count", lambda *a, **k: 0)
    result = smoke_strategy(engine, object(), "x")
    assert result["ok"] is False
    assert result["issues"][0]["code"] == "no_hits"
    assert "shape[0]" in result["issues"][0]["message"]


def test_matrix_zero_hits_today_but_window_has_signals_passes(fake_service, monkeypatch):
    """今天恰好没有符合的股票是正常行情, 不能误伤。"""
    import app.strategy.smoke as smoke_module

    engine = _FakeEngine(backend="matrix_native")
    monkeypatch.setattr(smoke_module, "_window_entry_count", lambda *a, **k: 42)
    result = smoke_strategy(engine, object(), "x")
    assert result["ok"] is True


def test_missing_repo_is_skipped(fake_service):
    result = smoke_strategy(_FakeEngine(), None, "x")
    assert result["ok"] is True
    assert result["skipped"] == "no_repo"


def test_no_market_data_is_skipped(fake_service, monkeypatch):
    monkeypatch.setattr(_FakeService, "latest", None)
    result = smoke_strategy(_FakeEngine(), object(), "x")
    assert result["ok"] is True
    assert result["skipped"] == "no_market_data"


def test_context_failure_does_not_block(fake_service, monkeypatch):
    monkeypatch.setattr(_FakeService, "context_error", RuntimeError("缺少 parquet"))
    result = smoke_strategy(_FakeEngine(), object(), "x")
    assert result["ok"] is True
    assert result["skipped"].startswith("context_failed")


def test_smoke_message_readable(fake_service):
    failed = smoke_strategy(_FakeEngine(error=ValueError("缺列 _ma5")), object(), "x")
    assert smoke_message(failed) == "缺列 _ma5"
    assert "实跑通过" in smoke_message(smoke_strategy(_FakeEngine(), object(), "x"))


def test_load_failure_carries_engine_load_error(fake_service):
    """策略没加载上时必须带上真实原因, 只报 unknown strategy 模型没法改。"""

    class _LoadFailedEngine:
        def get(self, strategy_id):
            raise ValueError(f"unknown strategy: {strategy_id}")

        def load_errors(self):
            return [{"file": "/tmp/ai/x.py",
                     "error": "python_history_legacy strategy must declare only filter_history"}]

    result = smoke_strategy(_LoadFailedEngine(), object(), "x")
    assert result["ok"] is False
    assert result["issues"][0]["code"] == "load_failed"
    assert "must declare only filter_history" in result["issues"][0]["message"]


def test_save_is_rolled_back_when_smoke_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.api.strategy.smoke_strategy",
        lambda *args, **kwargs: {
            "ok": False,
            "issues": [{"code": "run_failed", "message": "引用了不存在的列「_ma5」"}],
            "skipped": None,
        },
    )
    engine = StrategyEngine([tmp_path / "strategies" / "custom"])
    with pytest.raises(ValueError, match="实跑失败"):
        save_strategy_code_core(
            StrategyCodeSaveRequest(
                strategy_id="custom_smoke_demo",
                code=_VALID_CODE,
                target_source="custom",
                mode="create",
            ),
            engine,
            tmp_path,
        )
    assert not (tmp_path / "strategies" / "custom" / "custom_smoke_demo.py").exists()


def test_save_succeeds_when_smoke_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.api.strategy.smoke_strategy",
        lambda *args, **kwargs: {"ok": True, "issues": [], "skipped": None},
    )
    engine = StrategyEngine([tmp_path / "strategies" / "custom"])
    saved = save_strategy_code_core(
        StrategyCodeSaveRequest(
            strategy_id="custom_smoke_demo",
            code=_VALID_CODE,
            target_source="custom",
            mode="create",
        ),
        engine,
        tmp_path,
    )
    assert saved["ok"] is True
    assert (tmp_path / "strategies" / "custom" / "custom_smoke_demo.py").exists()
