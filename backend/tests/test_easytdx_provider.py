"""EasyTdxProvider 契约与单位标准化测试。

不依赖真实网络与 easy_tdx 安装: 用假 EasyTdxClient 返回样例 K 线/快照/除权事件,
验证字段映射、单位口径 (CONTRIBUTING §3: 股→手、快照 vol 手直通、万股→股、
百分数→小数制、除权每股口径、北京时间 naive)、软失败语义、五档单档形态、
能力声明与设置页试拉。
"""

from __future__ import annotations

import math
import types
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from app.plugins.easytdx import provider as ep
from app.plugins.easytdx.client import EasyTdxClient, EasyTdxError, parse_symbol
from app.plugins.easytdx.provider import EasyTdxProvider


# ---------------------------------------------------------------- fakes
class _FakeClient:
    """预置返回值; 记录调用供参数/根数断言。"""

    def __init__(self):
        self.kline_by_symbol: dict[str, list[dict]] = {}
        self.kline_error: Exception | None = None
        self.kline_calls: list[dict] = []
        self.client_ids: list[int] = []
        self.quote_rows: list[dict] = []
        self.quote_calls: list[dict] = []
        self.list_rows: list[dict] = []
        self.xdxr: dict[str, object] = {}
        self.xdxr_calls: list[str] = []
        self.xdxr_client_ids: list[int] = []
        self.forks: list[_FakeClient] = []
        self.prepare_calls = 0
        self.prepare_std_calls = 0
        self.closed = False

    def prepare(self) -> None:
        """真实实现里预热 MAC 通道(选优 + 建连), 各路分叉只建连。"""
        self.prepare_calls += 1

    def prepare_std(self) -> None:
        """真实实现里预热标准协议通道(除权事件用)。"""
        self.prepare_std_calls += 1

    def fork(self) -> _FakeClient:
        """并发分叉出的连接: 共用同一份预置数据, 便于断言调用归属。"""
        clone = _FakeClient()
        clone.__dict__.update(self.__dict__)
        self.forks.append(clone)  # 保留引用: 否则 id 会被回收复用, 断言失真
        return clone

    def close(self) -> None:
        self.closed = True

    def kline(self, symbol, *, period_name, count):
        self.kline_calls.append({"symbol": symbol, "period": period_name, "count": count})
        self.client_ids.append(id(self))
        if self.kline_error:
            raise self.kline_error
        return list(self.kline_by_symbol.get(symbol, []))

    def quotes(self, symbols, preset="BASIC"):
        self.quote_calls.append({"symbols": list(symbols), "preset": preset})
        return list(self.quote_rows)

    def quote_list(self, category_name="A", count=6000):
        self.quote_calls.append({"category": category_name, "count": count})
        return list(self.list_rows)

    def xdxr_events(self, symbol):
        self.xdxr_calls.append(symbol)
        self.xdxr_client_ids.append(id(self))
        item = self.xdxr.get(symbol, [])
        if isinstance(item, Exception):
            raise item
        return list(item)


@pytest.fixture
def provider(monkeypatch):
    fake = _FakeClient()
    p = EasyTdxProvider()
    p._client = fake
    return p, fake


def _bar(d: date, *, o=10.0, h=11.0, low=9.0, c=10.5, vol=1_000_00, amount=1_000_000.0):
    return {"datetime": datetime(d.year, d.month, d.day), "open": o, "high": h,
            "low": low, "close": c, "vol": float(vol), "amount": amount}


# ---------------------------------------------------------------- daily
def test_daily_maps_volume_shares_to_lots(provider):
    p, fake = provider
    d = date(2026, 11, 6)
    fake.kline_by_symbol["600519.SH"] = [_bar(d, vol=4_284_772)]  # 实测茅台当日 4284772 股
    df = p.get_daily(["600519.SH"], None, datetime(2026, 11, 6))
    assert df["volume"].to_list() == [42847]  # 42847 手, 与同刻快照对得上


def test_daily_keeps_raw_unadjusted_ohlc(provider):
    p, fake = provider
    fake.kline_by_symbol["600519.SH"] = [_bar(date(2026, 11, 6), o=1450.0, h=1457.72, low=1436.0, c=1453.0)]
    row = p.get_daily(["600519.SH"], None, datetime(2026, 11, 6)).to_dicts()[0]
    assert (row["open"], row["high"], row["low"], row["close"]) == (1450.0, 1457.72, 1436.0, 1453.0)
    assert row["amount"] == 1_000_000.0  # 元直通


def test_daily_date_range_filter(provider):
    p, fake = provider
    fake.kline_by_symbol["000001.SZ"] = [
        _bar(date(2026, 10, 30)), _bar(date(2026, 11, 2)), _bar(date(2026, 11, 6)),
    ]
    df = p.get_daily(["000001.SZ"], datetime(2026, 11, 1), datetime(2026, 11, 5))
    assert df["date"].to_list() == [date(2026, 11, 2)]


def test_daily_requests_max_count_when_window_open(provider):
    p, fake = provider
    p.get_daily(["600519.SH"], None, datetime(2026, 11, 6))
    assert fake.kline_calls[0]["count"] == ep._DAILY_MAX_COUNT
    assert fake.kline_calls[0]["period"] == "DAILY"


def test_daily_symbol_failure_yields_empty_frame_not_raise(provider):
    p, fake = provider
    fake.kline_error = EasyTdxError("节点断连")
    df = p.get_daily(["600519.SH"], None, datetime(2026, 11, 6))
    assert df.is_empty() and df.columns == list(ep._DAILY_SCHEMA)


def test_daily_batches_symbols_into_frames(monkeypatch, provider):
    """帧数 = 标的数 / 每帧标的数 —— 落盘小文件数随之收敛(逐只一帧会爆文件)。"""
    p, fake = provider
    monkeypatch.setattr(ep, "_DAILY_FRAME_SYMBOLS", 3)
    syms = [f"{600000 + i:06d}.SH" for i in range(7)]
    for s in syms:
        fake.kline_by_symbol[s] = [_bar(date(2026, 11, 6))]

    frames = list(p.iter_daily(syms, datetime(2026, 11, 6), datetime(2026, 11, 6)))

    assert [f.height for f in frames] == [3, 3, 1]
    assert sum(f["symbol"].n_unique() for f in frames) == 7
    assert frames[0].columns == list(ep._DAILY_SCHEMA)


def test_daily_uses_independent_connections(provider):
    """并发靠多连接: 共用一条会被 EasyTdxClient 的 RLock 重新串行化。"""
    p, fake = provider
    syms = [f"{600000 + i:06d}.SH" for i in range(4)]
    for s in syms:
        fake.kline_by_symbol[s] = [_bar(date(2026, 11, 6))]

    list(p.iter_daily(syms, datetime(2026, 11, 6), datetime(2026, 11, 6)))

    assert len(set(fake.client_ids)) == len(syms)


def test_daily_progress_callback_counts_every_symbol(provider):
    p, fake = provider
    syms = [f"{600000 + i:06d}.SH" for i in range(5)]
    for s in syms:
        fake.kline_by_symbol[s] = [_bar(date(2026, 11, 6))]
    seen: list[tuple[int, int]] = []

    list(p.iter_daily(syms, datetime(2026, 11, 6), datetime(2026, 11, 6),
                      on_chunk_done=lambda cur, tot: seen.append((cur, tot))))

    assert [cur for cur, _ in seen] == [1, 2, 3, 4, 5]
    assert all(tot == len(syms) for _, tot in seen)


def test_daily_one_symbol_failure_does_not_drop_others(provider):
    p, fake = provider
    syms = ["600000.SH", "600001.SH", "600002.SH"]
    for s in syms:
        fake.kline_by_symbol[s] = [_bar(date(2026, 11, 6))]

    def kline(symbol, *, period_name, count):
        if symbol == "600001.SH":
            raise EasyTdxError("节点断连")
        return fake.kline_by_symbol[symbol]

    fake.kline = kline
    df = p.get_daily(syms, datetime(2026, 11, 6), datetime(2026, 11, 6))

    assert set(df["symbol"].to_list()) == {"600000.SH", "600002.SH"}


def test_daily_keeps_input_symbol_order(provider):
    """并发完成顺序是乱的, 落盘顺序必须跟输入一致(重跑/合并结果才稳定)。"""
    p, fake = provider
    syms = [f"{600000 + i:06d}.SH" for i in range(8)]
    for s in syms:
        fake.kline_by_symbol[s] = [_bar(date(2026, 11, 6))]

    df = p.get_daily(syms, datetime(2026, 11, 6), datetime(2026, 11, 6))

    assert df["symbol"].to_list() == syms


# ---------------------------------------------------------------- adj_factor
def test_adj_factor_matches_exchange_formula(provider):
    """P=10、每股派息 1 元 → 参考价 9.00 → 因子 10/9, 除权日涨跌幅在板限内。"""
    p, fake = provider
    fake.xdxr["600000.SH"] = [{"date": "2026-06-26", "category": 1, "fenhong": 1.0,
                               "peigujia": 0.0, "songzhuangu": 0.0, "peigu": 0.0}]
    fake.kline_by_symbol["600000.SH"] = [
        _bar(date(2026, 6, 25), c=10.0), _bar(date(2026, 6, 26), o=9.0, c=9.05),
    ]
    df = p.get_adj_factors(["600000.SH"], datetime(2026, 6, 1), datetime(2026, 6, 30))
    row = df.to_dicts()[0]
    assert row["trade_date"] == date(2026, 6, 26)
    assert row["ex_factor"] == pytest.approx(10.0 / 9.0)
    assert row["dividend"] == 1.0 and row["prev_close"] == 10.0


def test_adj_factor_uses_per_share_values_without_extra_divide(provider):
    """库内已 ÷10 成每股: fenhong=0.3 表示每股 0.3 元, 再除 10 会让因子全错。"""
    p, fake = provider
    fake.xdxr["600000.SH"] = [{"date": "2026-06-26", "category": 1, "fenhong": 0.3,
                               "peigujia": 0.0, "songzhuangu": 0.3, "peigu": 0.0}]
    fake.kline_by_symbol["600000.SH"] = [
        _bar(date(2026, 6, 25), c=20.0), _bar(date(2026, 6, 26), c=15.0),
    ]
    row = p.get_adj_factors(["600000.SH"], datetime(2026, 6, 1), datetime(2026, 6, 30)).to_dicts()[0]
    assert row["dividend"] == pytest.approx(0.3)
    assert row["bonus"] == pytest.approx(0.3)
    expected_ref = math.floor((20.0 - 0.3) / 1.3 * 100 + 0.5) / 100
    assert row["ex_factor"] == pytest.approx(20.0 / expected_ref)


def test_adj_factor_drops_limit_violating_event(provider):
    """错误事件会让复权后除权日涨跌幅超板限 → 自检剔除。"""
    p, fake = provider
    fake.xdxr["600000.SH"] = [{"date": "2026-06-26", "category": 1, "fenhong": 5.0,
                               "peigujia": 0.0, "songzhuangu": 0.0, "peigu": 0.0}]
    fake.kline_by_symbol["600000.SH"] = [
        _bar(date(2026, 6, 25), c=10.0), _bar(date(2026, 6, 26), c=10.0),
    ]
    df = p.get_adj_factors(["600000.SH"], datetime(2026, 6, 1), datetime(2026, 6, 30))
    assert df.is_empty()


def test_adj_factor_ignores_non_xdxr_category(provider):
    p, fake = provider
    fake.xdxr["600000.SH"] = [{"date": "2026-06-26", "category": 2, "fenhong": 1.0,
                               "peigujia": 0.0, "songzhuangu": 0.0, "peigu": 0.0}]
    fake.kline_by_symbol["600000.SH"] = [_bar(date(2026, 6, 25), c=10.0)]
    assert p.get_adj_factors(["600000.SH"], None, datetime(2026, 6, 30)).is_empty()


def test_adj_factor_event_error_propagates(provider):
    """事件接口失败不得当成「该股无除权」——必须上抛让任务层标记失败。"""
    p, fake = provider
    fake.xdxr["600000.SH"] = EasyTdxError("xdxr 超时")
    with pytest.raises(EasyTdxError):
        p.get_adj_factors(["600000.SH"], None, datetime(2026, 6, 30))


def test_adj_factor_skips_unparsable_symbol_instead_of_aborting(provider):
    """代码缺后缀是调用方的数据问题, 不能让整轮全市场除权失败。"""
    p, fake = provider
    fake.xdxr["600000.SH"] = [{"date": "2026-06-26", "category": 1, "fenhong": 1.0,
                               "peigujia": 0.0, "songzhuangu": 0.0, "peigu": 0.0}]
    fake.kline_by_symbol["600000.SH"] = [_bar(date(2026, 6, 25), c=10.0)]
    df = p.get_adj_factors(["000001", "600000.SH"], None, datetime(2026, 6, 30))
    assert not df.is_empty()
    assert set(df["symbol"].to_list()) == {"600000.SH"}


def test_adj_factor_uses_independent_connections(provider):
    """除权事件走标准协议: 共用一条连接会被 RLock 重新串行化。"""
    p, fake = provider
    syms = [f"{600000 + i:06d}.SH" for i in range(4)]
    for s in syms:
        fake.xdxr[s] = []
    fake.kline_by_symbol["600519.SH"] = [_bar(date(2026, 6, 25), c=10.0)]

    p.get_adj_factors(syms, datetime(2026, 6, 1), datetime(2026, 6, 30))

    assert len(set(fake.xdxr_client_ids)) == len(syms)
    assert fake.prepare_std_calls == 1  # 标准协议主机只选优一次, 各路直连


def test_adj_factor_progress_callback_counts_every_symbol(provider):
    p, fake = provider
    syms = [f"{600000 + i:06d}.SH" for i in range(5)]
    for s in syms:
        fake.xdxr[s] = []
    seen: list[tuple[int, int]] = []

    p.get_adj_factors(syms, datetime(2026, 6, 1), datetime(2026, 6, 30),
                      on_chunk_done=lambda cur, tot: seen.append((cur, tot)))

    assert [cur for cur, _ in seen] == [1, 2, 3, 4, 5]
    assert all(tot == len(syms) for _, tot in seen)


# ---------------------------------------------------------------- realtime
def test_realtime_change_pct_is_decimal(provider):
    p, fake = provider
    fake.list_rows = [{"market": 1, "code": "600519", "name": "贵州茅台", "pre_close": 1452.0,
                       "open": 1450.0, "high": 1457.72, "low": 1436.0, "close": 1453.0,
                       "vol": 42847.0, "amount": 6.2e9, "turnover": 0.342754}]
    row = p.get_realtime()[0]
    assert row["symbol"] == "600519.SH"
    assert row["change_pct"] == pytest.approx((1453.0 - 1452.0) / 1452.0)
    assert row["change_amount"] == pytest.approx(1.0)
    assert abs(row["change_pct"]) < 1.0  # 小数制, 不是百分数


def test_realtime_volume_is_lots_passthrough(provider):
    p, fake = provider
    fake.list_rows = [{"market": 0, "code": "000001", "pre_close": 11.64, "close": 11.81,
                       "vol": 1640276.0, "amount": 1.9e9, "turnover": 0.845255}]
    row = p.get_realtime()[0]
    assert row["volume"] == 1640276  # 快照 vol 已在手, 不得再 /100
    assert row["turnover_rate"] == pytest.approx(0.00845255)  # 百分数 → 小数制


def test_realtime_drops_suspended_rows_with_zero_price(provider):
    """停牌票快照 OHLC 全 0 → change_pct 会是 -100% 假跌停, 必须剔除。"""
    p, fake = provider
    fake.list_rows = [
        {"market": 0, "code": "002623", "name": "亚玛顿", "pre_close": 19.28,
         "open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "vol": 0.0},
        {"market": 0, "code": "000001", "name": "平安银行", "pre_close": 11.64, "close": 11.81,
         "open": 11.62, "high": 11.85, "low": 11.57, "vol": 1640276.0},
    ]
    syms = [r["symbol"] for r in p.get_realtime()]
    assert syms == ["000001.SZ"]  # 零价票不出现

def test_realtime_bj_rows_are_kept(provider):
    p, fake = provider
    fake.list_rows = [{"market": 2, "code": "920085", "pre_close": 10.0, "close": 10.5, "vol": 100.0}]
    assert p.get_realtime()[0]["symbol"] == "920085.BJ"


def test_realtime_soft_failure_returns_empty_list(provider):
    p, fake = provider
    fake.quote_list = lambda *a, **kw: (_ for _ in ()).throw(EasyTdxError("连接超时"))
    assert p.get_realtime() == []


def test_realtime_indices_failure_returns_none(provider):
    p, fake = provider
    fake.quotes = lambda *a, **kw: (_ for _ in ()).throw(EasyTdxError("节点不可用"))
    assert p.get_realtime_indices(["000001.SH"]) is None


def test_realtime_indices_empty_request_returns_empty_list(provider):
    p, _ = provider
    assert p.get_realtime_indices([]) == []


# ---------------------------------------------------------------- depth5
def test_depth_batch_shape_is_single_level(provider):
    """上游只有买一/卖一: 数组单元素, 2-5 档留空(不补零)。"""
    p, fake = provider
    fake.quote_rows = [{"market": 1, "code": "600519", "bid_price": 1452.0, "ask_price": 1453.0,
                        "bid_volume": 2.0, "ask_volume": 2.0}]
    depth = p.get_depth_batch(["600519.SH"])["600519.SH"]
    assert depth["bid_prices"] == [1452.0] and depth["bid_volumes"] == [2]
    assert depth["ask_prices"] == [1453.0] and depth["ask_volumes"] == [2]
    assert len(depth["bid_prices"]) == 1 and len(depth["ask_prices"]) == 1
    assert isinstance(depth["timestamp"], int) and depth["timestamp"] > 10**12  # 毫秒
    assert fake.quote_calls[-1]["preset"] == "QUOTE"


def test_depth_batch_isolates_single_batch_failure(provider):
    """一个批次失败不影响另一批次, 且不跨源回退。"""
    p, fake = provider
    calls = {"n": 0}

    def flaky(symbols, preset="BASIC"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise EasyTdxError("第一批超时")
        return [{"market": 0, "code": "000001", "bid_price": 11.8, "ask_price": 11.81,
                 "bid_volume": 10.0, "ask_volume": 20.0}]

    fake.quotes = flaky
    # 两批都在 _QUOTE_BATCH 内, 用 81 只触发两批
    syms = ["600519.SH"] + [f"{i:06d}.SZ" for i in range(1, 81)]
    out = p.get_depth_batch(syms)
    assert set(out) == {"000001.SZ"}


def test_depth_entry_drops_row_without_prices(provider):
    p, fake = provider
    fake.quote_rows = [{"market": 1, "code": "600519", "bid_price": None, "ask_price": 1.0}]
    assert p.get_depth_batch(["600519.SH"]) == {}


# ---------------------------------------------------------------- minute
def test_minute_datetime_is_naive_beijing_wallclock(provider):
    p, fake = provider
    fake.kline_by_symbol["000001.SZ"] = [
        {"datetime": datetime(2026, 11, 6, 9, 31), "open": 1.0, "high": 1.0, "low": 1.0,
         "close": 1.0, "vol": 100.0, "amount": 1000.0},
    ]
    df = p.get_minute(["000001.SZ"], None, datetime(2026, 11, 6, 15, 0))
    dt = df["datetime"].to_list()[0]
    assert dt == datetime(2026, 11, 6, 9, 31) and dt.tzinfo is None


def test_minute_volume_shares_to_lots(provider):
    p, fake = provider
    fake.kline_by_symbol["600519.SH"] = [
        {"datetime": datetime(2026, 11, 6, 15, 0), "open": 1453.0, "high": 1453.0,
         "low": 1453.0, "close": 1453.0, "vol": 282600.0, "amount": 4.1e8},
    ]
    row = p.get_minute(["600519.SH"], None, datetime(2026, 11, 6, 15, 0)).to_dicts()[0]
    assert row["volume"] == pytest.approx(2826.0)  # 282600 股 = 2826 手
    assert row["amount"] == pytest.approx(4.1e8)


def test_minute_amount_null_when_missing(provider):
    p, fake = provider
    fake.kline_by_symbol["600519.SH"] = [
        {"datetime": datetime(2026, 11, 6, 15, 0), "open": 1.0, "high": 1.0,
         "low": 1.0, "close": 1.0, "vol": 100.0},
    ]
    row = p.get_minute(["600519.SH"], None, datetime(2026, 11, 6, 15, 0)).to_dicts()[0]
    assert row["amount"] is None  # 不伪造


def test_minute_window_filter_and_freq_guard(provider):
    p, fake = provider
    fake.kline_by_symbol["600519.SH"] = [
        {"datetime": datetime(2026, 11, 3, 9, 31), "open": 1.0, "high": 1.0, "low": 1.0,
         "close": 1.0, "vol": 100.0, "amount": 1.0},
        {"datetime": datetime(2026, 11, 6, 9, 31), "open": 1.0, "high": 1.0, "low": 1.0,
         "close": 1.0, "vol": 100.0, "amount": 1.0},
    ]
    df = p.get_minute(["600519.SH"], datetime(2026, 11, 5), datetime(2026, 11, 6, 15, 0))
    assert df["datetime"].to_list() == [datetime(2026, 11, 6, 9, 31)]
    assert p.get_minute(["600519.SH"], None, None, freq="5m").is_empty()


def test_minute_failure_raises_for_task_layer(provider):
    p, fake = provider
    fake.kline_error = EasyTdxError("节点断连")
    with pytest.raises(EasyTdxError):
        p.get_minute(["600519.SH"], None, datetime(2026, 11, 6))


def test_minute_failure_in_one_symbol_aborts_whole_round(provider):
    """并发下任一标的失败仍要中止整轮 —— 不能让任务层误以为同步成功。"""
    p, fake = provider

    def kline(symbol, *, period_name, count):
        if symbol == "000001.SZ":
            raise EasyTdxError("节点断连")
        return []

    fake.kline = kline
    with pytest.raises(EasyTdxError):
        p.get_minute(["600000.SH", "000001.SZ"], None, datetime(2026, 11, 6))


def test_minute_uses_independent_connections(provider):
    p, fake = provider
    syms = [f"{600000 + i:06d}.SH" for i in range(4)]
    for s in syms:
        fake.kline_by_symbol[s] = [
            {"datetime": datetime(2026, 11, 6, 9, 31), "open": 1.0, "high": 1.0, "low": 1.0,
             "close": 1.0, "vol": 100.0, "amount": 100.0},
        ]

    df = p.get_minute(syms, None, datetime(2026, 11, 6, 15, 0))

    assert len(set(fake.client_ids)) == len(syms)
    assert df["symbol"].to_list() == syms


def test_minute_history_days_declared(provider):
    p, _ = provider
    assert p.minute_history_days == ep._MINUTE_HISTORY_DAYS == 20


# ---------------------------------------------------------------- full_minute
def test_intraday_batch_keeps_only_today(provider):
    """修复轮只要当日窗口: count 模式尾部的前一日余量必须滤掉。"""
    p, fake = provider
    now = datetime.now()
    fake.kline_by_symbol["600519.SH"] = [
        {"datetime": datetime(now.year, now.month, now.day) - timedelta(days=1), "open": 1.0,
         "high": 1.0, "low": 1.0, "close": 1.0, "vol": 100.0, "amount": 100.0},
        {"datetime": now.replace(hour=9, minute=31, second=0, microsecond=0), "open": 2.0,
         "high": 2.0, "low": 2.0, "close": 2.0, "vol": 200.0, "amount": 200.0},
    ]
    df = p.get_intraday_batch(["600519.SH"], count=3)
    assert df.height == 1
    assert df["close"].to_list() == [2.0]
    assert df["volume"].to_list() == [2.0]  # 200 股 → 2 手


def test_intraday_batch_isolates_symbol_failure(provider):
    """单标的失败不拖垮整轮修复。"""
    p, fake = provider
    now = datetime.now()

    def kline(symbol, *, period_name, count):
        if symbol == "000001.SZ":
            raise EasyTdxError("节点断连")
        return [{"datetime": now.replace(hour=9, minute=31, second=0, microsecond=0),
                 "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                 "vol": 100.0, "amount": 100.0}]

    fake.kline = kline
    df = p.get_intraday_batch(["600519.SH", "000001.SZ"], count=3)
    assert df["symbol"].to_list() == ["600519.SH"]


def test_intraday_batch_empty_symbols_returns_schema(provider):
    p, _ = provider
    df = p.get_intraday_batch([], count=3)
    assert df.is_empty() and df.columns == list(ep._MINUTE_SCHEMA)


def test_intraday_batch_uses_independent_connections(provider):
    p, fake = provider
    now = datetime.now()
    syms = [f"{600000 + i:06d}.SH" for i in range(4)]
    for s in syms:
        fake.kline_by_symbol[s] = [
            {"datetime": now.replace(hour=9, minute=31, second=0, microsecond=0), "open": 1.0,
             "high": 1.0, "low": 1.0, "close": 1.0, "vol": 100.0, "amount": 100.0},
        ]

    df = p.get_intraday_batch(syms, count=3)

    assert len(set(fake.client_ids)) == len(syms)
    assert df["symbol"].to_list() == syms


def test_test_dataset_full_minute_returns_preview(provider):
    p, fake = provider
    now = datetime.now()
    fake.kline_by_symbol["600519.SH"] = [
        {"datetime": now.replace(hour=9, minute=31, second=0, microsecond=0), "open": 1.0,
         "high": 1.0, "low": 1.0, "close": 1.0, "vol": 100.0, "amount": 100.0},
    ]
    out = p.test_dataset("full_minute", ["600519.SH"])
    assert out["rows"] == 1 and isinstance(out["preview"][0]["datetime"], str)


# ---------------------------------------------------------------- instruments
def test_instruments_converts_wan_shares_to_shares(provider):
    p, fake = provider
    fake.list_rows = [{"market": 1, "code": "600519", "name": "贵州茅台",
                       "total_shares": 125008.15625, "float_shares": 125008.15625,
                       "buy_price_limit": 1597.2, "sell_price_limit": 1306.8}]
    item = p.get_instruments("stock")[0]
    assert item["symbol"] == "600519.SH" and item["exchange"] == "SH"
    assert item["ext"]["float_shares"] == pytest.approx(125008.15625 * 10_000)  # ≈1.25e9 股
    assert item["ext"]["limit_up"] == pytest.approx(1597.2)
    assert item["ext"]["listing_date"] is None  # 上游不提供, 不伪造


def test_instruments_rejects_non_stock(provider):
    p, _ = provider
    assert p.get_instruments("etf") == []


# ---------------------------------------------------------------- 声明与可用性
# ---------------------------------------------------------------- client.fork
def test_fork_copies_only_prepared_channels(monkeypatch):
    """fork 只复制主连接**已建好**的通道。

    没 prepare 过的通道不在分叉时现连 —— from_best_host 要 ping 全部节点,
    16 路各来一次比串行还慢; 除权(标准协议)与日K(MAC)是两条独立通道。
    """
    made: list[str] = []

    class _StubMac:
        def __init__(self, *a, **k):
            self.connected = False
            made.append("mac")

        def connect(self):
            self.connected = True

    class _StubStd:
        def __init__(self, *a, **k):
            self.connected = False
            made.append("std")

        def connect(self):
            self.connected = True

    monkeypatch.setattr(
        EasyTdxClient, "_require",
        lambda self, module: types.SimpleNamespace(MacClient=_StubMac, TdxClient=_StubStd),
    )

    base = EasyTdxClient()
    clone = base.fork()
    assert clone._mac is None and clone._std is None
    assert made == []  # 主连接没通道 → 分叉也不建

    base._mac = object()
    base._std = object()
    clone = base.fork()
    assert isinstance(clone._mac, _StubMac) and clone._mac.connected
    assert isinstance(clone._std, _StubStd) and clone._std.connected
    assert made == ["mac", "std"]


def test_symbol_parsing():
    assert parse_symbol("600519.SH") == (1, "600519")
    assert parse_symbol("000001.sz") == (0, "000001")
    assert parse_symbol("920085.BJ") == (2, "920085")
    with pytest.raises(EasyTdxError):
        parse_symbol("600519")


def test_declared_datasets_match_plugin_yaml():
    from app.data_providers import custom

    yaml_path = ep.__file__.rsplit("/", 1)[0] + "/plugin.yaml"
    import yaml as _yaml

    with open(yaml_path) as fh:
        meta = _yaml.safe_load(fh)
    assert set(meta["datasets"]) == set(ep._DATASETS)
    assert meta["entry"] == "app.plugins.easytdx.provider:EasyTdxProvider"
    assert meta["check"] == "app.plugins.easytdx.provider:availability"
    assert custom is not None


def test_provider_does_not_declare_financial(provider):
    """财务需 658 个无名位置字段的映射字典, 未接入 → 回退 fuyao/TickFlow。"""
    p, _ = provider
    assert "financial" not in p.config.datasets


def test_availability_false_when_module_missing(monkeypatch):
    monkeypatch.setattr(ep, "_module_available", lambda: False)
    ok, reason = ep.availability()
    assert ok is False and "安装" in reason


def test_availability_true_when_module_present(monkeypatch):
    monkeypatch.setattr(ep, "_module_available", lambda: True)
    assert ep.availability() == (True, "ok")


def test_close_resets_client():
    p = EasyTdxProvider()
    fake = _FakeClient()
    p._client = fake
    p.close()
    assert fake.closed is True and p._client is None


# ---------------------------------------------------------------- 试拉
def test_test_dataset_reports_error_for_undeclared_dataset(provider):
    p, _ = provider
    out = p.test_dataset("financial")
    assert out["rows"] == 0 and "回退默认源" in out["error"]


def test_test_dataset_returns_serializable_preview(provider):
    p, fake = provider
    fake.kline_by_symbol["600519.SH"] = [_bar(date(2026, 11, 6))]
    out = p.test_dataset("daily")
    assert out["rows"] == 1 and isinstance(out["preview"][0]["date"], str)


def test_test_dataset_realtime_empty_reports_error(provider):
    p, fake = provider
    fake.list_rows = []
    out = p.test_dataset("realtime")
    assert out["rows"] == 0 and "error" in out


def test_depth_frame_schema_matches_contract(provider):
    """盘口数组长度可为 1, 服务层只用 [0] 索引。"""
    p, fake = provider
    fake.quote_rows = [{"market": 1, "code": "600519", "bid_price": 1452.0,
                        "ask_price": 1453.0, "bid_volume": 2.0, "ask_volume": 2.0}]
    out = p.test_dataset("depth5")
    assert pl is not None
    assert out["rows"] == 1
    assert out["preview"][0]["bid_prices"] == [1452.0]


# ---------------------------------------------------------------- 复权共享模块
def test_shared_ex_factor_ref_price_rounds_half_up():
    from app.data_providers.ex_factor import price_limit, ref_price

    assert ref_price(10.0, 1.0, 0.0, 0.0, 0.0) == 9.0
    assert ref_price(10.0, 0.0, 0.0, 0.3, 5.0) == 8.85  # (10 + 0.3×5) / 1.3 = 8.8462 → 8.85
    assert price_limit("300750.SZ") == 0.20
    assert price_limit("920085.BJ") == 0.30
    assert price_limit("600519.SH") == 0.10
    assert price_limit("688981.SH") == 0.20