"""涨跌停口径修复: 风险警示带宽自证 + 名称变更历史 + 上市日回填。

回归背景 (三个缺陷):
1. instruments.name 是同步当日的快照, 今天的「ST」被套到全历史 → 历史上
   非风险警示的日子按 5% 带算, ±6%~9% 的普通波动被误判成涨跌停。
   修法: 涨停日 high == 涨停价, 跌停日 low == 跌停价, 因此
   high > 5%涨停价 / low < 5%跌停价 是「当日带宽 > 5%」的硬证据。
2. 只有当前快照, 无法还原「某个历史交易日当时叫什么」→ 名称变更历史
   (symbol, name, as_of) 按日还原风险警示标记。
3. easytdx 不提供上市日 → 注册制新股上市初期无涨跌幅窗口判不出来,
   上市头几天被误判成涨跌停 → 用本地日K首日回填 (首日不拥挤时)。

另: 风险警示简称除 ST/*ST 外还有「S佳通」这类未冠 ST 的, 以 S 开头即算。
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from app.indicators.pipeline import compute_limit_signals
from app.price_limits import is_risk_warning_name
from app.services import instrument_sync as isync


@pytest.fixture(autouse=True)
def _seeded_signals(seeded_data_dir) -> None:
    """内置涨跌停信号 (signal_limit_*) 运行时住在 data/ 里, 干净环境 (CI) 没有 —— 种进临时目录。"""


def _two_day(
    symbol: str,
    prev_close: float,
    today_close: float,
    *,
    today_high: float | None = None,
    today_low: float | None = None,
    trade_date: date = date(2024, 1, 3),
) -> pl.DataFrame:
    """2 日最小输入: 首日平收, 次日收于 today_close。"""
    high = today_high if today_high is not None else today_close
    low = today_low if today_low is not None else today_close
    return pl.DataFrame({
        "symbol": [symbol, symbol],
        "date": [trade_date - timedelta(days=1), trade_date],
        "raw_close": [prev_close, today_close],
        "close": [prev_close, today_close],
        "raw_high": [prev_close, high],
        "raw_low": [prev_close, low],
        "open": [prev_close, today_close],
        "high": [prev_close, high],
        "low": [prev_close, low],
        "change_pct": [0.0, today_close / prev_close - 1],
        "vol_ratio_5d": [1.0, 1.0],
    })


def _limits(df: pl.DataFrame, instruments: pl.DataFrame, **kwargs) -> pl.DataFrame:
    return compute_limit_signals(df, instruments, **kwargs).sort("date")


# ── 1. 带宽自证 ─────────────────────────────────────────────

def test_band_self_proof_upgrades_wider_band():
    """当前名为 ST, 但当日 high 越过 5% 涨停价 → 证明带宽是 10%, 不是 5%。"""
    df = _two_day("600001.SH", 10.0, 10.8, today_high=10.8, today_low=10.0)
    inst = pl.DataFrame({"symbol": ["600001.SH"], "name": ["*ST测试"]})
    out = _limits(df, inst)
    # 5% 带会把 10.8 误判成涨停; 10% 带下 10.8 是普通波动
    assert out["signal_limit_up"].to_list()[-1] is False
    assert out["limit_up_price"].to_list()[-1] == pytest.approx(10.995)


def test_band_self_proof_keeps_genuine_st_limit():
    """真风险警示股涨停日 high 恰等于 5% 涨停价 (不越界) → 保留 5% 带。"""
    df = _two_day("600001.SH", 10.0, 10.5, today_high=10.5, today_low=10.0)
    inst = pl.DataFrame({"symbol": ["600001.SH"], "name": ["*ST测试"]})
    out = _limits(df, inst)
    assert out["signal_limit_up"].to_list()[-1] is True
    assert out["limit_up_price"].to_list()[-1] == pytest.approx(10.495)


def test_band_self_proof_uses_low_for_limit_down():
    """跌停侧同理: low 越过 5% 跌停价 → 证明带宽 > 5%。"""
    df = _two_day("600001.SH", 10.0, 9.2, today_high=10.0, today_low=9.2)
    inst = pl.DataFrame({"symbol": ["600001.SH"], "name": ["*ST测试"]})
    out = _limits(df, inst)
    assert out["signal_limit_down"].to_list()[-1] is False
    # 判定价含交易所 0.005 容差: 10% 理论跌停价 9.00 → 9.005
    assert out["limit_down_price"].to_list()[-1] == pytest.approx(9.005)


def test_coincidental_close_on_five_pct_band_is_not_a_limit():
    """收盘价恰好等于 5% 涨停价的普通交易日 (low 越界) 不是涨停。

    收盘价落在窄带价格上是巧合, 只有 high/low 越界才是带宽证据。
    """
    df = _two_day("600001.SH", 10.0, 10.5, today_high=10.6, today_low=9.9)
    inst = pl.DataFrame({"symbol": ["600001.SH"], "name": ["*ST测试"]})
    out = _limits(df, inst)
    assert out["signal_limit_up"].to_list()[-1] is False


# ── 2. S 前缀风险警示 ───────────────────────────────────────

def test_s_prefix_name_is_risk_warning():
    assert is_risk_warning_name("S佳通") is True
    assert is_risk_warning_name("*ST海王") is True
    assert is_risk_warning_name("ST海王") is True
    assert is_risk_warning_name("平安银行") is False
    assert is_risk_warning_name(None) is False


def test_s_prefix_stock_uses_five_pct_band():
    """600182.SH (S佳通) 是窄带标的: 5% 涨停价 10.50 应被识别为涨停。"""
    df = _two_day("600182.SH", 10.0, 10.5, today_high=10.5, today_low=10.0)
    inst = pl.DataFrame({"symbol": ["600182.SH"], "name": ["S佳通"]})
    out = _limits(df, inst)
    assert out["signal_limit_up"].to_list()[-1] is True
    assert out["limit_up_price"].to_list()[-1] == pytest.approx(10.495)


# ── 3. 名称变更历史 ─────────────────────────────────────────

def test_name_history_restores_pre_st_name_per_date():
    """当前快照是 *ST, 但历史日期当时还不是 → 那天应按 10% 带算。"""
    df = _two_day("600001.SH", 10.0, 10.8, today_high=10.8, today_low=10.0)
    inst = pl.DataFrame({"symbol": ["600001.SH"], "name": ["*ST测试"]})
    hist = pl.DataFrame({
        "symbol": ["600001.SH", "600001.SH"],
        "name": ["普通测试", "*ST测试"],
        "as_of": [date(2024, 1, 1), date(2024, 6, 1)],
    })
    out = _limits(df, inst, name_history=hist)
    # 交易日 2024-01-03 早于改名时点 → 当日简称不是风险警示 → 10% 带
    assert out["limit_up_price"].to_list()[-1] == pytest.approx(10.995)
    assert out["signal_limit_up"].to_list()[-1] is False


def test_name_history_applies_st_after_rename_date():
    """改名之后的历史日期按新简称 (风险警示) 判 5% 带。"""
    df = _two_day("600001.SH", 10.0, 10.5, today_high=10.5, today_low=10.0,
                  trade_date=date(2024, 7, 1))
    inst = pl.DataFrame({"symbol": ["600001.SH"], "name": ["*ST测试"]})
    hist = pl.DataFrame({
        "symbol": ["600001.SH", "600001.SH"],
        "name": ["普通测试", "*ST测试"],
        "as_of": [date(2024, 1, 1), date(2024, 6, 1)],
    })
    out = _limits(df, inst, name_history=hist)
    assert out["signal_limit_up"].to_list()[-1] is True
    assert out["limit_up_price"].to_list()[-1] == pytest.approx(10.495)


def test_name_history_missing_falls_back_to_snapshot():
    """无历史表时回退当前快照判定 (不报错)。"""
    df = _two_day("600001.SH", 10.0, 10.5, today_high=10.5, today_low=10.0)
    inst = pl.DataFrame({"symbol": ["600001.SH"], "name": ["*ST测试"]})
    out = _limits(df, inst, name_history=None)
    assert out["signal_limit_up"].to_list()[-1] is True


# ── 4. 维表侧: 上市日回填 + 名称变更历史落库 ────────────────

def _write_daily(data_dir: Path, symbol: str, first_day: date) -> None:
    _write_daily_many(data_dir, [symbol], first_day)


def _write_daily_many(data_dir: Path, symbols: list[str], first_day: date) -> None:
    out = data_dir / "kline_daily" / f"date={first_day.isoformat()}"
    out.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "symbol": symbols,
        "date": [first_day] * len(symbols),
        "open": [10.0] * len(symbols), "high": [10.0] * len(symbols),
        "low": [10.0] * len(symbols), "close": [10.0] * len(symbols),
        "volume": [100.0] * len(symbols), "amount": [1000.0] * len(symbols),
    }).write_parquet(out / "part.parquet")


def test_listing_date_backfilled_from_local_daily_first_date(tmp_path, monkeypatch):
    """provider 未给上市日 → 用本地日K首日回填 (近期新股)。"""
    from app.market_time import cn_today

    recent = cn_today() - timedelta(days=10)
    _write_daily(tmp_path, "301999.SZ", recent)
    monkeypatch.setattr(
        isync, "fetch_instruments_via_provider",
        lambda *_: [{
            "symbol": "301999.SZ", "name": "新股测试", "code": "301999",
            "exchange": "SZ", "region": "CN", "type": "stock",
            "listing_date": None, "total_shares": None, "float_shares": None,
            "tick_size": None, "limit_up": None, "limit_down": None,
        }],
    )

    isync.sync_instruments(tmp_path)

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert df["listing_date"][0] == recent


def test_listing_date_not_backfilled_for_old_symbols(tmp_path, monkeypatch):
    """老股的本地日K首日是同步起始日而非上市日 → 不回填。"""
    _write_daily(tmp_path, "600000.SH", date(2015, 1, 5))
    monkeypatch.setattr(
        isync, "fetch_instruments_via_provider",
        lambda *_: [{
            "symbol": "600000.SH", "name": "老股测试", "code": "600000",
            "exchange": "SH", "region": "CN", "type": "stock",
            "listing_date": None, "total_shares": None, "float_shares": None,
            "tick_size": None, "limit_up": None, "limit_down": None,
        }],
    )

    isync.sync_instruments(tmp_path)

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert df["listing_date"][0] is None


def test_listing_date_not_backfilled_on_crowded_first_day(tmp_path, monkeypatch):
    """同一天落下大批标的 → 该首日是同步窗口而非上市日 → 不回填。"""
    crowded_day = date(2026, 3, 2)
    symbols = [f"60{i:04d}.SH" for i in range(20)]
    _write_daily_many(tmp_path, symbols, crowded_day)
    monkeypatch.setattr(
        isync, "fetch_instruments_via_provider",
        lambda *_: [{
            "symbol": s, "name": "批量测试", "code": s.split(".")[0],
            "exchange": "SH", "region": "CN", "type": "stock",
            "listing_date": None, "total_shares": None, "float_shares": None,
            "tick_size": None, "limit_up": None, "limit_down": None,
        } for s in symbols],
    )

    isync.sync_instruments(tmp_path)

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert df["listing_date"].to_list() == [None] * len(symbols)


def test_listing_date_backfilled_for_old_enough_new_listing(tmp_path, monkeypatch):
    """回填按「首日是否拥挤」判定, 不按上市距今多久 —— 2023 年的新股照样回填。"""
    old_ipo_day = date(2023, 4, 10)
    _write_daily(tmp_path, "301888.SZ", old_ipo_day)
    monkeypatch.setattr(
        isync, "fetch_instruments_via_provider",
        lambda *_: [{
            "symbol": "301888.SZ", "name": "老新股测试", "code": "301888",
            "exchange": "SZ", "region": "CN", "type": "stock",
            "listing_date": None, "total_shares": None, "float_shares": None,
            "tick_size": None, "limit_up": None, "limit_down": None,
        }],
    )

    isync.sync_instruments(tmp_path)

    df = pl.read_parquet(tmp_path / "instruments" / "instruments.parquet")
    assert df["listing_date"][0] == old_ipo_day


def test_name_history_records_renames_only(tmp_path, monkeypatch):
    """名称变更才追加; 未变更的标的不重复写入。"""
    def _rows(name: str) -> list[dict]:
        return [{
            "symbol": "600000.SH", "name": name, "code": "600000",
            "exchange": "SH", "region": "CN", "type": "stock",
            "listing_date": None, "total_shares": None, "float_shares": None,
            "tick_size": None, "limit_up": None, "limit_down": None,
        }]

    monkeypatch.setattr(isync, "fetch_instruments_via_provider", lambda *_: _rows("普通测试"))
    isync.sync_instruments(tmp_path)
    hist_path = tmp_path / "instrument_name_history" / "part.parquet"
    assert hist_path.exists()
    first = pl.read_parquet(hist_path)
    assert first.height == 1
    assert first["name"][0] == "普通测试"

    # 同名再同步一次 → 不追加
    isync.sync_instruments(tmp_path)
    assert pl.read_parquet(hist_path).height == 1

    # 改名 → 追加一条
    monkeypatch.setattr(isync, "fetch_instruments_via_provider", lambda *_: _rows("*ST测试"))
    isync.sync_instruments(tmp_path)
    after = pl.read_parquet(hist_path).sort("as_of")
    assert after.height == 2
    assert after["name"].to_list() == ["普通测试", "*ST测试"]


def test_name_history_not_written_inside_instruments_dir(tmp_path, monkeypatch):
    """名称历史不能落进 instruments/ —— 那里按 symbol 唯一, 会破坏维表读取。"""
    monkeypatch.setattr(
        isync, "fetch_instruments_via_provider",
        lambda *_: [{
            "symbol": "600000.SH", "name": "普通测试", "code": "600000",
            "exchange": "SH", "region": "CN", "type": "stock",
            "listing_date": None, "total_shares": None, "float_shares": None,
            "tick_size": None, "limit_up": None, "limit_down": None,
        }],
    )
    isync.sync_instruments(tmp_path)

    inst_files = list((tmp_path / "instruments").glob("*.parquet"))
    assert [f.name for f in inst_files] == ["instruments.parquet"]
