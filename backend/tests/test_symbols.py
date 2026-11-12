"""裸代码 → 交易所后缀的统一规则 (app.symbols) 与外部数据归一 (ext_data)。

北交所 43/83/87/92 开头的代码此前在 ext_data 里被判成 .SZ —— 与日K 同步
的口径分裂。这里锁定唯一一份规则。
"""
from __future__ import annotations

import polars as pl

from app.services.ext_data import normalize_symbol
from app.symbols import exchange_of_code, symbol_of_code


def test_exchange_of_code_covers_main_markets():
    assert exchange_of_code("600519") == "SH"   # 沪主板
    assert exchange_of_code("688981") == "SH"   # 科创板
    assert exchange_of_code("510300") == "SH"   # 沪 ETF
    assert exchange_of_code("900901") == "SH"   # 沪 B股
    assert exchange_of_code("110038") == "SH"   # 沪可转债
    assert exchange_of_code("000001") == "SZ"   # 深主板
    assert exchange_of_code("300750") == "SZ"   # 创业板
    assert exchange_of_code("159558") == "SZ"   # 深 ETF
    assert exchange_of_code("200011") == "SZ"   # 深 B股
    assert exchange_of_code("920374") == "BJ"   # 北交所新代码段
    assert exchange_of_code("830001") == "BJ"   # 北交所存量代码段
    assert exchange_of_code("430047") == "BJ"


def test_exchange_of_code_rejects_codes_outside_a_share():
    assert exchange_of_code("ABCDEF") is None
    assert exchange_of_code("60051") is None
    assert exchange_of_code("700000") is None
    assert symbol_of_code("700000") is None


def test_normalize_symbol_marks_bj_codes_as_bj():
    """北交所代码不再被标成 .SZ。"""
    out = normalize_symbol(pl.Series(["430047", "830001", "920374", "600519", "000001"]))

    assert out.to_list() == [
        "430047.BJ", "830001.BJ", "920374.BJ", "600519.SH", "000001.SZ",
    ]


def test_normalize_symbol_prefers_instruments_lookup():
    out = normalize_symbol(pl.Series(["000001"]), {"000001": "000001.SZ"})

    assert out.to_list() == ["000001.SZ"]


def test_normalize_symbol_keeps_suffixed_and_unknown_unchanged():
    out = normalize_symbol(pl.Series(["600519.SH", "700000"]))

    assert out.to_list() == ["600519.SH", "700000"]
