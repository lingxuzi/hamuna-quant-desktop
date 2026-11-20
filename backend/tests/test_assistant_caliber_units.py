"""助手层数值口径必须与工具实际返回单位一致 (回测统计 / regime / sector / breadth)。

这批口径错位都会让模型的结论差 100 倍, 而且 prompt 里原先完全没有声明:
- run_backtest 的 total_return / annual_return / max_drawdown / win_rate 是小数 (0.153 = 15.3%);
- get_regime 的 promo_rate / seal_rate 是 0-1 小数;
- get_sector_rotation 的 pct_now / pct_prev 是小数;
- get_market_overview 同一 payload 内 breadth.up_pct 是百分数, 而 avg_pct 是小数,
  indices[].change_pct 是百分数, 而 top_gainers[].change_pct 是小数。

另: 足迹卡(run_backtest 的 summary) 直接给人看, 必须已换算成百分数,
不能再把 0.153 原样打印成「总收益 0.153」。
"""
from __future__ import annotations

from app.custom.assistant.prompt import build_system_prompt
from app.custom.assistant.tools import summarize_tool_result


def _caliber_lines() -> list[str]:
    lines = build_system_prompt(None).splitlines()
    start = lines.index("数据口径:") + 1
    out: list[str] = []
    for line in lines[start:]:
        if not line.startswith("- "):
            break
        out.append(line)
    return out


def test_prompt_declares_backtest_stats_as_fraction() -> None:
    joined = "\n".join(_caliber_lines())
    assert "total_return" in joined
    assert "win_rate" in joined
    # 必须点明是小数且要乘 100, 否则模型会写「总收益 0.153%」
    assert "0.153 = 15.3%" in joined


def test_prompt_declares_regime_and_sector_as_fraction() -> None:
    joined = "\n".join(_caliber_lines())
    assert "promo_rate" in joined and "seal_rate" in joined
    assert "pct_now" in joined
    # 同名不同口径必须显式点出, 否则 overview 的 52.83 会被当成 0.5283
    assert "limit.seal_rate" in joined


def test_prompt_declares_breadth_mixed_units() -> None:
    joined = "\n".join(_caliber_lines())
    assert "breadth" in joined
    assert "avg_pct" in joined and "up_pct" in joined


def _payload(result: dict) -> dict:
    return {"ok": True, "result": result}


def test_backtest_summary_renders_percent() -> None:
    summary = summarize_tool_result(
        "run_backtest",
        _payload({"stats": {
            "total_return": 0.153, "annual_return": 0.2349,
            "max_drawdown": -0.0812, "sharpe": 1.42,
        }}),
    )
    assert "15.30%" in summary
    assert "23.49%" in summary
    assert "-8.12%" in summary
    assert "1.42" in summary


def test_backtest_summary_handles_missing_stats() -> None:
    summary = summarize_tool_result("run_backtest", _payload({}))
    assert "无指标" in summary


def test_backtest_summary_tolerates_null_fields() -> None:
    summary = summarize_tool_result(
        "run_backtest",
        _payload({"stats": {"total_return": None, "annual_return": 0.1}}),
    )
    # 缺字段给占位, 不能崩
    assert "-" in summary
    assert "10.00%" in summary
