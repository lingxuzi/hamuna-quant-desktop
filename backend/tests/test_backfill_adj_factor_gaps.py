"""补齐除权缺口脚本的市场佐证逻辑。

断言的是「参考价推出的涨跌幅带必须覆盖当日最高/最低」—— 这道闸拦下的是
除权明细与行情矛盾的事件 (主源与第二源同时错的行全部倒在这里)。
"""
from __future__ import annotations

from datetime import date

import polars as pl

from scripts.backfill_adj_factor_gaps import _corroborate


def _event(**kw):
    base = {
        "symbol": "000691.SZ",
        "trade_date": date(2026, 1, 5),
        "prev_close": 9.10,
        "dividend": 0.0,
        "bonus": 0.2908,
        "allot": 0.0,
        "allot_price": 0.0,
    }
    base.update(kw)
    return base


def _day(high=7.40, low=7.40, close=7.40):
    return {"high": high, "low": low, "close": close}


def test_accepts_event_matching_market():
    # 扶摇事件: 10 送 2.908 股 → 参考价 7.05, 当日一字收在 7.40 (5% 风险警示带内)
    ok, reason = _corroborate(_event(), 9.10, _day())
    assert ok, reason


def test_limit_uses_integer_cents_not_float_multiplication():
    # 参考价 23.93, 创业板 20%: 浮点 23.93*1.2 = 28.715999 → 看上去最高 28.72 越界;
    # 交易所整数分 = 28.72, 最高正好贴板 → 必须通过。
    from scripts.backfill_adj_factor_gaps import _limit

    assert _limit(23.93, 0.2, up=True) == 28.72
    assert _limit(23.93, 0.2, up=False) == 19.14


def test_rejects_event_whose_band_cannot_cover_market_high():
    # 002970.SZ 2024-05-30: 明细算出参考价 35.85 → 涨停价 39.44, 市场最高 39.45
    ev = _event(symbol="002970.SZ", trade_date=date(2024, 5, 30),
                prev_close=36.35, dividend=0.5, bonus=0.0)
    ok, reason = _corroborate(ev, 36.35, _day(high=39.45, low=35.45, close=39.45))
    assert not ok
    assert "越过涨停价" in reason


def test_rejects_event_anchored_to_another_day():
    ev = _event(prev_close=8.80)
    ok, reason = _corroborate(ev, 9.10, _day())
    assert not ok
    assert "前收不一致" in reason


def test_rejects_event_without_local_context():
    ok, reason = _corroborate(_event(), None, _day())
    assert not ok
    assert "前收盘价" in reason

    ok, reason = _corroborate(_event(), 9.10, None)
    assert not ok
    assert "行情" in reason


def test_limit_hits_tells_right_factor_from_wrong_one():
    """000615.SZ 2025-12-29: 一字收 3.71。

    参考价 3.53 (10送1.841股) → 5% 涨停价正好 3.71; 参考价 3.80 (10送1股)
    在任何一档带下都算不出 3.71 → 存量因子与行情矛盾。
    """
    from scripts.backfill_adj_factor_gaps import _limit_hits

    assert "+5%" in _limit_hits(3.53, 3.71)
    assert _limit_hits(3.80, 3.71) == []


def test_wrong_factor_rows_flags_one_word_day_off_the_limit():
    from scripts.backfill_adj_factor_gaps import _wrong_factor_rows

    store = pl.DataFrame(
        {
            "symbol": ["000615.SZ", "000615.SZ"],
            "trade_date": [date(2025, 12, 29), date(2024, 6, 3)],
            "ex_factor": [1.1, 1.0],
            "prev_close": [4.18, 5.0],
        }
    )
    daily = pl.DataFrame(
        {
            "symbol": ["000615.SZ", "000615.SZ"],
            "_d": [date(2025, 12, 29), date(2024, 6, 3)],
            "open": [3.71, 5.1],
            "high": [3.71, 5.2],
            "low": [3.71, 5.0],
            "close": [3.71, 5.1],
            "_prev_raw": [4.18, 5.0],
        }
    )
    out = _wrong_factor_rows(store, daily)
    assert out.height == 1
    assert out["symbol"][0] == "000615.SZ"
    assert out["trade_date"][0] == date(2025, 12, 29)
