"""AI 助手 get_financials 必须暴露口径事实, 模型才不会被数字骗。

两个线上症状:
① ST 股 roe=-586.17 (分母净资产接近零/为负) 被当普通盈利能力引用 —— 数值失真,
   必须先点明成因。
② 本地每个标的常只有 1 期报告期 (period_end 截面), 模型拿这 1 期去做同比,
   会把「只有 1 期」当成「多年」, 讲出并不存在的多年趋势。

工具层做确定性检测 (available_periods / warnings), prompt 层立口径规则。
"""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from app.custom.assistant import tools as assistant_tools
from app.custom.assistant.prompt import build_system_prompt


def _write_metrics(data_dir, rows: list[dict]) -> None:
    out = data_dir / "financials" / "metrics"
    out.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(out / "part.parquet")


async def _call(data_dir, symbol: str = "600337.SH") -> dict:
    ctx = assistant_tools.ToolContext.build(data_dir=data_dir)
    payload = await assistant_tools.execute_assistant_tool(
        "get_financials", {"symbol": symbol, "table": "metrics"}, ctx,
    )
    assert payload["ok"] is True
    return payload["result"]


async def test_single_period_is_explicitly_reported(tmp_path) -> None:
    """只有 1 期时必须给出 available_periods 并说明无法做同比。"""
    _write_metrics(tmp_path, [{
        "symbol": "600337.SH", "period_end": date(2026, 9, 30), "roe": -586.17,
    }])

    result = await _call(tmp_path)

    assert result["available_periods"] == 1
    assert "只有 1 期报告期" in result["note"]
    assert "无法做同比" in result["note"]


async def test_extreme_roe_is_flagged_as_distorted(tmp_path) -> None:
    """净资产接近零/为负时 roe 爆表, 必须标出失真, 不让模型当成盈利能力。"""
    _write_metrics(tmp_path, [{
        "symbol": "600337.SH", "period_end": date(2026, 9, 30), "roe": -586.17,
    }])

    result = await _call(tmp_path)

    warnings = result["warnings"]
    assert len(warnings) == 1
    assert "-586.17%" in warnings[0]
    assert "净资产" in warnings[0]
    assert "失真" in warnings[0]


async def test_normal_roe_has_no_warning(tmp_path) -> None:
    """正常区间的 roe 不该被误标 —— 否则警告会被稀释, 模型不再当真。"""
    _write_metrics(tmp_path, [{
        "symbol": "601567.SH", "period_end": date(2026, 9, 30), "roe": 5.94,
    }])

    result = await _call(tmp_path, symbol="601567.SH")

    assert "warnings" not in result


async def test_multi_period_reports_available_count(tmp_path) -> None:
    """多期时 available_periods 是实际期数, 且不误报「无法做同比」。"""
    _write_metrics(tmp_path, [
        {"symbol": "600519.SH", "period_end": date(2026, 3, 31), "roe": 12.0},
        {"symbol": "600519.SH", "period_end": date(2026, 6, 30), "roe": 13.0},
        {"symbol": "600519.SH", "period_end": date(2026, 9, 30), "roe": 14.0},
    ])

    result = await _call(tmp_path, symbol="600519.SH")

    assert result["available_periods"] == 3
    assert "无法做同比" not in result.get("note", "")


def _caliber_lines() -> list[str]:
    """提示词「数据口径」小节的条目行。"""
    lines = build_system_prompt(None).splitlines()
    start = lines.index("数据口径:") + 1
    out: list[str] = []
    for line in lines[start:]:
        if not line.startswith("- "):
            break
        out.append(line)
    return out


def test_prompt_declares_financial_ratios_as_percent() -> None:
    lines = [line for line in _caliber_lines() if "roe" in line and "百分数" in line]
    assert lines, "提示词需声明财务比率字段已是百分数"


def test_prompt_requires_flagging_extreme_roe() -> None:
    lines = [line for line in _caliber_lines() if "index_deduct_weighted_avg_roe" in line]
    assert lines, "提示词需点名扣非加权 ROE 这一失真来源"
    assert any("净资产" in line and "失真" in line for line in lines)


def test_prompt_forbids_yoy_and_trend_from_single_period() -> None:
    lines = [line for line in _caliber_lines() if "available_periods" in line]
    assert lines, "提示词需引用工具返回的 available_periods"
    assert any("同比" in line and "禁止" in line for line in lines)


@pytest.mark.parametrize("periods", [1, 4])
async def test_available_periods_independent_of_requested_periods(tmp_path, periods: int) -> None:
    """请求期数不能改变「本地实际有几期」这个事实。"""
    _write_metrics(tmp_path, [{
        "symbol": "600337.SH", "period_end": date(2026, 9, 30), "roe": -586.17,
    }])

    ctx = assistant_tools.ToolContext.build(data_dir=tmp_path)
    payload = await assistant_tools.execute_assistant_tool(
        "get_financials", {"symbol": "600337.SH", "table": "metrics", "periods": periods}, ctx,
    )

    assert payload["result"]["available_periods"] == 1
