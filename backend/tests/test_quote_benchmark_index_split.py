"""回归: 基准指数不得被当成股票写进 kline_daily。

历史 bug: quote_service 显式拉取 CORE_INDEX_SYMBOLS | BENCHMARK_INDEX_SYMBOLS,
但分流只用 get_index_symbol_set() (指数维表只有 101 行) 判定, 000002.SH(上证A指) /
399107.SZ(深证A指) / 399102.SZ(创业板综) 不在维表里 → 被当股票写进 kline_daily。

后果是盘面上多出「收盘价 3777 的个股」, 全市场扫描/筛选/回测/AI 助手都会把它当个股。
修法是调用方把这轮显式请求的指数代码并入 index 集合, 这里锁住分流函数本身。
"""
from __future__ import annotations

from app.services.quote_service import _split_by_asset_type


def test_benchmark_index_not_classified_as_stock() -> None:
    """000002.SH 不在指数维表时, 调用方并入 index 集合后必须整体进 index 分支。"""
    records = [
        {"symbol": "000002.SH"},   # 上证A指: 基准指数, 不在 101 行维表
        {"symbol": "399107.SZ"},   # 深证A指: 同上
        {"symbol": "600519.SH"},   # 真个股
    ]
    idx, etf, stock = _split_by_asset_type(
        records,
        index_symbols={"000001.SH", "399001.SZ", "000002.SH", "399107.SZ"},
        etf_symbols=set(),
    )
    assert {r["symbol"] for r in idx} == {"000002.SH", "399107.SZ"}
    assert etf == []
    assert [r["symbol"] for r in stock] == ["600519.SH"]


def test_without_extra_index_symbols_pollution_happens() -> None:
    """反证: 不把基准指数并入 index 集合时, 它确实会被当股票 —— 这就是原 bug。"""
    records = [{"symbol": "000002.SH"}, {"symbol": "600519.SH"}]
    _idx, _etf, stock = _split_by_asset_type(
        records, index_symbols={"000001.SH"}, etf_symbols=set(),
    )
    assert "000002.SH" in {r["symbol"] for r in stock}


def test_etf_not_counted_as_stock() -> None:
    """ETF 也不能漏进个股分区 (ETF 价格 1-3 元会污染低价股筛选)。"""
    records = [{"symbol": "510300.SH"}, {"symbol": "000001.SH"}]
    idx, etf, stock = _split_by_asset_type(
        records, index_symbols={"000001.SH"}, etf_symbols={"510300.SH"},
    )
    assert {r["symbol"] for r in idx} == {"000001.SH"}
    assert {r["symbol"] for r in etf} == {"510300.SH"}
    assert stock == []


def test_index_wins_when_both_sets_contain_symbol() -> None:
    """指数集合优先: 三份分流互斥, 不重叠, 每条记录只落一处。"""
    records = [{"symbol": "X"}, {"symbol": "Y"}, {"symbol": "Z"}]
    idx, etf, stock = _split_by_asset_type(
        records, index_symbols={"X"}, etf_symbols={"X", "Y"},
    )
    assert [r["symbol"] for r in idx] == ["X"]
    assert [r["symbol"] for r in etf] == ["Y"]
    assert [r["symbol"] for r in stock] == ["Z"]
