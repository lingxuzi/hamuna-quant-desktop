"""adj_factor 事件明细扩列: 归一化透传 / 存量三列 schema 合并兼容 / 消费方不受影响。

新增列 dividend/bonus/allot/allot_price/prev_close 全部可选可空:
- fuyao 等提供明细的源 → 落库供等差显示投影与全精度因子链重建;
- 存量 all.parquet 只有规范三列 → diagonal 合并自动补 null, 读取方零改动;
- 重跑一次同步即完成存量数据明细回填 (unique keep="last")。
"""

from datetime import date, timedelta

import polars as pl
import pytest

from app.indicators.pipeline import _apply_adj_factor
from app.market_time import cn_today
from app.services.kline_sync import (
    ADJ_DETAIL_COLS,
    _align_adj_factor_frame,
    _atomic_write_parquet,
    _merge_adj_factor_store,
    _normalize_adj_factor,
    recompute_adj_factor_store,
)


def _detail_row(symbol="600519.SH", td=date(2026, 6, 26), f=1.023663, **detail):
    return {"symbol": symbol, "trade_date": td, "ex_factor": f, **detail}


# ── _normalize_adj_factor: 明细列透传 ───────────────────────────────


def test_normalize_keeps_detail_columns_when_present():
    raw = pl.DataFrame([
        _detail_row(dividend=28.019, bonus=0.0, allot=0.0, allot_price=0.0, prev_close=1212.1)
    ])
    out = _normalize_adj_factor(raw)
    assert out.columns == ["symbol", "trade_date", "ex_factor", *ADJ_DETAIL_COLS]


def test_normalize_legacy_three_column_frame_unaffected():
    raw = pl.DataFrame([_detail_row()])
    out = _normalize_adj_factor(raw)
    assert out.columns == ["symbol", "trade_date", "ex_factor"]


def test_normalize_preserves_null_detail_values():
    """明细合法可为 null (如纯送转事件的 dividend), 不得被 drop_nulls 丢行。"""
    raw = pl.DataFrame([
        _detail_row(symbol="000812.SZ", f=1.10, dividend=None, bonus=0.1)
    ])
    out = _normalize_adj_factor(raw)
    assert out.height == 1
    assert out["dividend"][0] is None


# ── _align / _merge: 存量三列 schema 兼容与明细回填 ─────────────────


def test_align_adds_missing_detail_columns_as_null():
    aligned = _align_adj_factor_frame(pl.DataFrame([_detail_row()]))
    assert aligned.columns == ["symbol", "trade_date", "ex_factor", *ADJ_DETAIL_COLS]
    assert all(aligned[c][0] is None for c in ADJ_DETAIL_COLS)


def test_merge_backfills_details_into_legacy_schema(tmp_path):
    """旧行明细为 null、重同步的行以明细覆盖、新事件行计数正确。"""
    out = tmp_path / "all.parquet"
    legacy = pl.DataFrame([
        _detail_row(symbol="600519.SH", td=date(2026, 6, 26), f=1.023663),
        _detail_row(symbol="000001.SZ", td=date(2026, 6, 26), f=1.005),
    ])
    _atomic_write_parquet(legacy, out)

    new = pl.DataFrame([
        # 同事件重同步: 携带明细 → 覆盖旧行
        _detail_row(symbol="600519.SH", td=date(2026, 6, 26), f=1.023663,
                    dividend=28.019, bonus=0.0, allot=0.0, allot_price=0.0,
                    prev_close=1212.1),
        # 新事件
        _detail_row(symbol="000001.SZ", td=date(2025, 12, 19), f=1.017028,
                    dividend=23.959, bonus=0.0, allot=0.0, allot_price=0.0,
                    prev_close=1431.0),
    ])
    added, changed = _merge_adj_factor_store(out, new)
    assert added == 1  # 仅新事件计入
    # 变化判定只看 ex_factor: 600519 同因子仅回填明细 → 不算变化;
    # 000001 新事件 → 变化
    assert changed == ["000001.SZ"]

    merged = pl.read_parquet(out)
    assert merged.columns == ["symbol", "trade_date", "ex_factor", *ADJ_DETAIL_COLS]
    mt = merged.filter(
        (pl.col("symbol") == "600519.SH") & (pl.col("trade_date") == date(2026, 6, 26))
    ).row(0, named=True)
    assert mt["dividend"] == pytest.approx(28.019)  # 重同步回填明细
    old = merged.filter(
        (pl.col("symbol") == "000001.SZ") & (pl.col("trade_date") == date(2026, 6, 26))
    ).row(0, named=True)
    assert old["dividend"] is None  # 未重同步的旧行明细保持 null
    assert merged.height == 3


# ── 变化判定: enriched 局部重算的触发精度 ───────────────────────────


def test_merge_detail_only_backfill_reports_no_changed_symbols(tmp_path):
    """扩表后重拉全历史: 因子值全部未变, 只是补明细 → 不触发任何 enriched 重算。"""
    out = tmp_path / "all.parquet"
    legacy = pl.DataFrame([
        _detail_row(symbol="600519.SH", td=date(2026, 6, 26), f=1.023663),
        _detail_row(symbol="000001.SZ", td=date(2025, 12, 19), f=1.017028),
    ])
    _atomic_write_parquet(legacy, out)
    backfill = pl.DataFrame([
        _detail_row(symbol="600519.SH", td=date(2026, 6, 26), f=1.023663, dividend=28.019),
        _detail_row(symbol="000001.SZ", td=date(2025, 12, 19), f=1.017028, dividend=23.959),
    ])
    added, changed = _merge_adj_factor_store(out, backfill)
    assert added == 0
    assert changed == []  # 关键: 明细回填不改变复权价, 不触发重算


def test_merge_changed_factor_value_reports_symbol(tmp_path):
    """同键因子值修正 (如切换数据源后微差) → 该标的计入变化, 行数不增。"""
    out = tmp_path / "all.parquet"
    legacy = pl.DataFrame([_detail_row(symbol="600519.SH", f=1.023663)])
    _atomic_write_parquet(legacy, out)
    corrected = pl.DataFrame([
        _detail_row(symbol="600519.SH", f=1.0237, dividend=28.019)  # 值差 > 1e-9
    ])
    added, changed = _merge_adj_factor_store(out, corrected)
    assert added == 0
    assert changed == ["600519.SH"]


def test_merge_tiny_factor_diff_within_tolerance_not_changed(tmp_path):
    """值差 ≤ 1e-9 视为未变 (浮点噪声保护)。"""
    out = tmp_path / "all.parquet"
    legacy = pl.DataFrame([_detail_row(symbol="600519.SH", f=1.023663)])
    _atomic_write_parquet(legacy, out)
    noisy = pl.DataFrame([_detail_row(symbol="600519.SH", f=1.0236630000001)])
    added, changed = _merge_adj_factor_store(out, noisy)
    assert added == 0
    assert changed == []


def test_merge_first_write_all_symbols_changed(tmp_path):
    """库不存在时全部落库, 全部标的都算变化 (下游局部重算语义自然成立)。"""
    out = tmp_path / "all.parquet"
    first = pl.DataFrame([
        _detail_row(symbol="600519.SH", f=1.023663),
        _detail_row(symbol="000001.SZ", td=date(2025, 12, 19), f=1.017028),
    ])
    added, changed = _merge_adj_factor_store(out, first)
    assert added == 2
    assert changed == ["000001.SZ", "600519.SH"]

# ── 存量因子归一化 (公式口径修正后) ────────────────────────────────

# 000009.SZ 2023-08-03: 前收 11.41 存成 11.40999984741211, 派息 0.035 存成
# 0.034999999403953555, 相减压在 11.375 半分界上 → 交易所参考价 11.38,
# 旧 float 口径算出 11.37 → 因子差 0.09% → 除权日限价差 1 分。
_NOISY_PREV_CLOSE = 11.40999984741211
_NOISY_DIVIDEND = 0.034999999403953555


def _noisy_legacy_frame() -> pl.DataFrame:
    return pl.DataFrame([_detail_row(
        symbol="000009.SZ", td=date(2023, 8, 3),
        f=_NOISY_PREV_CLOSE / 11.37,           # 旧 float 口径的因子
        dividend=_NOISY_DIVIDEND, bonus=0.0, allot=0.0, allot_price=0.0,
        prev_close=_NOISY_PREV_CLOSE,
    )])


def test_recompute_store_normalizes_stored_factor(tmp_path):
    """离线归一化: 存量因子按明细列重算 → 变化行计入受影响标的。"""
    out = tmp_path / "all.parquet"
    _atomic_write_parquet(_noisy_legacy_frame(), out)

    rows, changed = recompute_adj_factor_store(out)

    assert rows == 1
    assert changed == ["000009.SZ"]
    stored = pl.read_parquet(out)
    assert stored["ex_factor"][0] == pytest.approx(_NOISY_PREV_CLOSE / 11.38)


def test_recompute_store_is_idempotent(tmp_path):
    """再跑一遍无变化 (幂等): 不重复触发 enriched 重算。"""
    out = tmp_path / "all.parquet"
    _atomic_write_parquet(_noisy_legacy_frame(), out)
    recompute_adj_factor_store(out)

    rows, changed = recompute_adj_factor_store(out)

    assert rows == 0
    assert changed == []


def test_merge_normalizes_existing_rows(tmp_path):
    """除权因子同步 (任意窗口) 也会把存量行归一化到当前公式口径。"""
    out = tmp_path / "all.parquet"
    _atomic_write_parquet(_noisy_legacy_frame(), out)
    empty = pl.DataFrame(schema={
        "symbol": pl.String, "trade_date": pl.Date, "ex_factor": pl.Float64,
        "dividend": pl.Float64, "bonus": pl.Float64, "allot": pl.Float64,
        "allot_price": pl.Float64, "prev_close": pl.Float64,
    })

    added, changed = _merge_adj_factor_store(out, empty)

    assert added == 0
    assert changed == ["000009.SZ"]
    stored = pl.read_parquet(out)
    assert stored["ex_factor"][0] == pytest.approx(_NOISY_PREV_CLOSE / 11.38)


# ── 消费方回归: 带明细列的 factors 不影响复权计算 ───────────────────


def test_apply_adj_factor_tolerates_detail_columns():
    raw = pl.DataFrame({
        "symbol": ["A"] * 3,
        "date": [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)],
        "open": [10.0] * 3, "high": [10.5] * 3, "low": [9.5] * 3,
        "close": [10.0, 9.0, 9.9],
    })
    factors = pl.DataFrame([
        _detail_row(symbol="A", td=date(2026, 1, 5), f=1.25,
                    dividend=2.0, bonus=0.0, allot=0.0, allot_price=0.0, prev_close=10.0)
    ])
    out = _apply_adj_factor(raw, factors)
    assert out["close"][0] == pytest.approx(10.0 / 1.25)  # 事件前按因子折算
    assert out["close"][1] == pytest.approx(9.0)          # 事件起锚定原始价
    assert out["close"][2] == pytest.approx(9.9)


# ── 未来除权事件不得提前折入 ─────────────────────────────────────────


def test_apply_adj_factor_ignores_events_after_today():
    """因子源会给已公告、尚未发生的除权日 (实测库内超前 3 天)。

    锚点取「今天」而非「因子表最后一条」: 否则 today 与全部历史都被未来除权
    提前压低, 到除权日真实发生还会再跳一次。
    """
    today = cn_today()
    d0, d1, d_future = today - timedelta(days=5), today - timedelta(days=4), today + timedelta(days=3)
    raw = pl.DataFrame({
        "symbol": ["A", "A", "A"],
        "date": [d0, d1, today],
        "open": [10.0] * 3, "high": [10.5] * 3, "low": [9.5] * 3,
        "close": [10.0, 9.0, 9.1],
    })
    factors = pl.DataFrame([
        _detail_row(symbol="A", td=d1, f=1.01),        # 已发生
        _detail_row(symbol="A", td=d_future, f=1.02),  # 未来
    ])
    out = _apply_adj_factor(raw, factors)
    # 只累计已发生的那次: 事件前 10/1.01, 事件后锚原始价。
    # 若把未来事件也算进锚点 → 10/1.0302 与 9.0/1.01*1.01/1.0302=8.74 (系统性偏低 2%)
    assert out["close"][0] == pytest.approx(10.0 / 1.01)
    assert out["close"][1] == pytest.approx(9.0)
    assert out["close"][2] == pytest.approx(9.1)
    # 首个事件之前 join_asof 不匹配 → 必须是 False 而非 null
    assert out["ex_rights"].to_list() == [False, True, False]


def test_apply_adj_factor_symbol_with_only_future_events_unadjusted():
    today = cn_today()
    raw = pl.DataFrame({
        "symbol": ["B", "B"],
        "date": [today - timedelta(days=3), today - timedelta(days=2)],
        "open": [8.0, 8.0], "high": [8.2, 8.2], "low": [7.8, 7.8],
        "close": [8.0, 8.1],
    })
    factors = pl.DataFrame([_detail_row(symbol="B", td=today + timedelta(days=3), f=1.02)])
    out = _apply_adj_factor(raw, factors)
    # 无已发生事件 → 原始价即复权价 (不能凭空乘 1/1.02)
    assert out["close"].to_list() == pytest.approx([8.0, 8.1])


def test_apply_adj_factor_historical_window_still_uses_later_events():
    """补算历史区间 (数据末端早于今天): 之后发生的除权仍须折入。

    否则同一根 K 线在全量同步与历史补算两条路径下算出不同复权价。
    """
    today = cn_today()
    raw = pl.DataFrame({
        "symbol": ["C"], "date": [today - timedelta(days=30)],
        "open": [5.0], "high": [5.1], "low": [4.9], "close": [5.0],
    })
    factors = pl.DataFrame([_detail_row(symbol="C", td=today - timedelta(days=10), f=1.25)])
    out = _apply_adj_factor(raw, factors)
    assert out["close"][0] == pytest.approx(5.0 / 1.25)
