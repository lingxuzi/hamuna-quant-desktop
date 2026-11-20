"""回测统计口径: 年化 / 最大回撤起点 / 平局不计亏损。

这几处都会静默把数字放大或缩小 —— 不报错, 只是给出错误的统计, 然后被前端
和 AI 助手当成结论。
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from app.backtest.engine import (
    BacktestEngine,
    SimulationOptions,
    TradeRecord,
    _annualize,
    _sample_periods_per_year,
)


def _trade(pnl: float, exit_day: str) -> TradeRecord:
    return TradeRecord(
        symbol="000001.SZ",
        entry_date=date.fromisoformat(exit_day),
        exit_date=date.fromisoformat(exit_day),
        entry_price=10.0,
        exit_price=10.0 * (1 + pnl),
        shares=100,
        entry_value=1000.0,
        pnl_pct=pnl,
        duration=5,
        exit_reason="signal",
    )


def test_annualize_refuses_ultra_short_spans():
    # 3 根 K 线 +20%: 旧口径 (1.2)**(252/3) = 4.4e8, 无信息量
    assert _annualize(0.20, 3 / 252) == pytest.approx(0.20)
    assert _annualize(0.20, 0.05) == pytest.approx(0.20)


def test_annualize_compounds_over_real_span():
    # 一年 +21% → 年化 21%(跨度正好一年)
    assert _annualize(0.21, 1.0) == pytest.approx(0.21, abs=1e-9)
    # 半年 +21% → (1.21)^2 - 1 = 46.41%
    assert _annualize(0.21, 0.5) == pytest.approx(0.4641, abs=1e-6)


def test_sample_periods_per_year_monthly_exits():
    # 12 次月度卖出跨 334 天 (0.91 年) → 约 13 次/年, 不是 252
    days = [f"2026-{m:02d}-15" for m in range(1, 13)]
    assert _sample_periods_per_year(days) == pytest.approx(13.1, abs=0.5)


def test_sample_periods_per_year_daily_exits():
    days = [f"2026-11-{d:02d}" for d in range(1, 21)]
    # 20 个样本 / 0.52 年 ≈ 38 次/年
    assert _sample_periods_per_year(days) > 20


def test_sample_periods_per_year_single_sample_falls_back():
    assert _sample_periods_per_year(["2026-01-05"]) == 252.0
    assert _sample_periods_per_year([]) == 252.0
    assert _sample_periods_per_year(["not-a-date", "2026-01-05"]) == 252.0


def test_candidate_stats_first_leg_drawdown_counted():
    # 先 -20% 再 +25%: 曲线 1.0 → 0.8 → 1.0, 最大回撤必须是 -20%
    trades = [
        _trade(-0.20, "2026-01-05"),
        _trade(0.25, "2026-02-05"),
    ]
    result = BacktestEngine._calc_independent_candidate_result(
        trades, n_candidates=2, execution_stats={}, options=SimulationOptions(),
    )
    assert result.stats["max_drawdown"] == pytest.approx(-0.20, abs=1e-6)


def test_candidate_stats_sharpe_uses_sample_frequency():
    # 12 个月度卖出、收益有波动 → sharpe = mean/std × √(样本/年), 不是 √252
    pnls = [0.01, -0.02, 0.03, -0.01, 0.02, -0.03, 0.04, -0.02, 0.01, 0.03, -0.01, 0.02]
    trades = [_trade(p, f"2026-{m:02d}-15") for m, p in zip(range(1, 13), pnls)]
    result = BacktestEngine._calc_independent_candidate_result(
        trades, n_candidates=12, execution_stats={}, options=SimulationOptions(),
    )
    expected = float(np.mean(pnls) / np.std(pnls, ddof=1) * np.sqrt(_sample_periods_per_year(
        [f"2026-{m:02d}-15" for m in range(1, 13)]
    )))
    assert result.stats["sharpe"] == pytest.approx(round(expected, 2), abs=1e-9)
    # 旧口径 (×√252) 会大出 √(252/13.1) ≈ 4.4 倍
    assert abs(result.stats["sharpe"]) < abs(float(np.mean(pnls) / np.std(pnls, ddof=1) * np.sqrt(252)))


def test_zero_pnl_is_not_a_loss():
    # +10%, 0%, -5%: 亏损均值 5%, 盈利均值 10% → profit_factor 2.0
    # 旧口径把 0% 算进亏损 → 亏损均值 2.5% → profit_factor 4.0
    trades = [
        _trade(0.10, "2026-01-05"),
        _trade(0.0, "2026-02-05"),
        _trade(-0.05, "2026-03-05"),
    ]
    result = BacktestEngine._calc_independent_candidate_result(
        trades, n_candidates=3, execution_stats={}, options=SimulationOptions(),
    )
    assert result.stats["profit_factor"] == pytest.approx(2.0, abs=1e-6)