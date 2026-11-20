"""助手工具: 裸 6 位代码必须补后缀, 财务结果必须带名称。

线上症状: 用户问「分析 600337」, 工具拿裸代码去查分区一律 0 行,
get_financials 的返回体又没有 name 字段 —— 模型于是凭训练记忆翻译代码,
把 600337(ST美克) 说成「东软集团」、把 601567(三星电气) 说成「三星重工」,
整篇分析套在错误的公司上, 且看起来很可信。

两条不变量:
① _validate_symbol 把裸代码补成 代码.交易所, 下游查询才可能命中;
② 任何带 symbol 的查询结果都带 name, 模型不需要 (也不允许) 自己翻译。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import pytest

from app.custom.assistant import tools as assistant_tools

_NAMES = {"600337.SH": "ST美克", "601567.SH": "三星电气", "600519.SH": "贵州茅台"}


class _Repo:
    """repo 桩: 只认带交易所后缀的 symbol (与真实分区键一致)。"""

    def __init__(self) -> None:
        self.queried: list[str] = []

    def get_name_map(self, symbols: list[str] | None = None) -> dict[str, str]:
        if symbols is None:
            return dict(_NAMES)
        return {s: _NAMES[s] for s in symbols if s in _NAMES}

    def resolve_asset_type(self, symbol: str) -> str:
        return "stock"

    def get_daily_asset(self, asset_type: str, symbol: str, start: Any, end: Any) -> pl.DataFrame:
        self.queried.append(symbol)
        if symbol not in _NAMES:
            return pl.DataFrame()
        return pl.DataFrame({
            "date": [date(2026, 11, 18)],
            "open": [3.70],
            "high": [3.76],
            "low": [3.68],
            "close": [3.72],
            "volume": [100.0],
        })


@pytest.mark.parametrize("raw,expected", [
    ("600337", "600337.SH"),
    ("000001", "000001.SZ"),
    ("920992", "920992.BJ"),
    ("600337.SH", "600337.SH"),  # 已带后缀原样返回
])
def test_validate_symbol_appends_exchange(raw: str, expected: str) -> None:
    assert assistant_tools._validate_symbol(raw) == expected


def test_validate_symbol_still_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="无效的证券代码"):
        assistant_tools._validate_symbol("not a symbol!!")


async def test_bare_code_reaches_partition_and_returns_name() -> None:
    repo = _Repo()
    ctx = assistant_tools.ToolContext.build(repo=repo)
    payload = await assistant_tools.execute_assistant_tool(
        "get_stock_analysis", {"symbol": "600337"}, ctx,
    )
    assert payload["ok"] is True
    assert repo.queried == ["600337.SH"], "裸代码必须补后缀后再查分区"
    assert payload["result"]["name"] == "ST美克"


async def test_financials_returns_name(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import app.services.financial_sync as financial_sync

    df = pl.DataFrame({
        "symbol": ["600337.SH"],
        "period_end": ["2026-09-30"],
        "roe": [-586.17],
    })
    monkeypatch.setattr(financial_sync, "get_financial_df", lambda data_dir, table: df)
    ctx = assistant_tools.ToolContext.build(repo=_Repo(), data_dir=tmp_path)
    payload = await assistant_tools.execute_assistant_tool(
        "get_financials", {"symbol": "600337"}, ctx,
    )
    assert payload["ok"] is True
    assert payload["result"]["name"] == "ST美克"
    assert payload["result"]["symbol"] == "600337.SH"
