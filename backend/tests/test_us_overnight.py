"""美股夜盘取数层单测。

不联网, 用假 client 回放 easytdx 的返回形状。这里钉的是「代码 → key」的映射
和失败回退口径 —— 上游 easytdx 换版本时这两处最容易悄悄坏掉。
"""
from __future__ import annotations

import pandas as pd

from app.services import premarket, us_overnight


class _FakeClient:
    """只实现 goods_quotes, 按 (market, code) 返回预置行。"""

    def __init__(self, rows: dict[tuple[int, str], dict]):
        self.rows = rows
        self.calls: list[list[tuple[int, str]]] = []

    def goods_quotes(self, stocks, fields=None):
        self.calls.append(list(stocks))
        out = []
        for mkt, code in stocks:
            row = self.rows.get((int(mkt), code))
            if row is not None:
                out.append({"market": int(mkt), "code": code, "name": row.get("name", ""),
                            **{k: v for k, v in row.items() if k != "name"}})
        return pd.DataFrame(out)


def _quote(close, pre_close, name=""):
    return {"name": name, "close": close, "pre_close": pre_close,
            "open": close, "high": close, "low": close}


def test_fetch_overnight_maps_codes_to_display_names(monkeypatch):
    from easy_tdx.mac.enums import ExMarket

    rows = {
        (int(ExMarket.INTL_INDEX), "A_SPX"): _quote(5000.0, 4800.0),
        (int(ExMarket.INTL_INDEX), "A_SOX"): _quote(9500.0, 8000.0),
        (int(ExMarket.INTL_INDEX), "CNY0"): _quote(12700.0, 12800.0),
        (int(ExMarket.MACRO_INDICATOR), "8_ATY"): _quote(4.92, 4.88),
        (int(ExMarket.US_STOCK), "NVDA"): _quote(157.0, 165.0),
    }
    monkeypatch.setattr(us_overnight, "_get_client", lambda: _FakeClient(rows))
    out = us_overnight.fetch_overnight()

    assert out is not None and out["source"] == "easytdx"
    by_code = {r["code"]: r for r in out["indices"]}
    assert by_code["A_SPX"]["name"] == "标普 500"
    assert by_code["A_SPX"]["region"] == "美国"
    assert by_code["A_SPX"]["pct"] == 4.17
    assert by_code["A_SOX"]["name"] == "费城半导体"
    # 期指与美债涨跌是价格百分比, 与指数同口径
    assert {r["code"] for r in out["futures"]} == {"CNY0"}
    assert out["us10y"] == 4.92
    assert out["leaders"][0]["code"] == "NVDA"
    assert out["leaders"][0]["pct"] == -4.85


def test_zero_close_index_is_dropped_not_scored_as_minus_100(monkeypatch):
    """easytdx 对未更新的指数回 close=0 但保留 pre_close —— 当价格算会得到 -100%。

    实测 2026-11-24: A_IXIC/A_NDX/A_SOX/A_HXC 全是 close=0, 腾讯同期有真实值。
    这些 key 必须缺席, 让腾讯补位; 混进 -100% 会把「外围股市」评分直接打到底。
    """
    from easy_tdx.mac.enums import ExMarket

    rows = {
        (int(ExMarket.INTL_INDEX), "A_SPX"): _quote(5000.0, 4800.0),
        (int(ExMarket.INTL_INDEX), "A_IXIC"): _quote(0.0, 24089.56),
        (int(ExMarket.INTL_INDEX), "A_NDX"): _quote(0.0, 26746.10),
    }
    monkeypatch.setattr(us_overnight, "_get_client", lambda: _FakeClient(rows))
    out = us_overnight.fetch_overnight()

    assert [r["code"] for r in out["indices"]] == ["A_SPX"]
    assert us_overnight._pct(0.0, 24089.56) is None

    # 缺席的 key 由腾讯补位, 最终 markets 里没有 -100%
    markets = premarket._overnight_markets(out)
    assert {m["key"] for m in markets} == {"usINX"}


def test_fetch_overnight_returns_none_on_failure_and_drops_connection(monkeypatch):
    """连接坏了必须返回 None 并置空客户端 —— 否则下一轮还拿着死连接。"""
    class _Boom:
        def goods_quotes(self, *a, **k):
            raise RuntimeError("node down")

    monkeypatch.setattr(us_overnight, "_get_client", lambda: _Boom())
    monkeypatch.setattr(us_overnight, "_client", object(), raising=False)
    assert us_overnight.fetch_overnight() is None
    assert us_overnight._client is None


def test_overnight_rows_merge_ahead_of_tencent_and_reuse_keys(monkeypatch):
    """easytdx 成功的 key 不再被腾讯覆盖; 腾讯只补 easytdx 给不了的。"""
    over = {
        "indices": [{"code": "A_SPX", "name": "标普 500", "region": "美国",
                     "close": 5000.0, "previous_close": 4800.0, "pct": 4.17}],
        "futures": [{"code": "CNY0", "name": "富时中国 A50 期指",
                     "close": 12700.0, "previous_close": 12800.0, "pct": -0.78}],
        "leaders": [], "treasuries": [], "us10y": 4.92,
        "as_of": "2026-11-24 08:00", "source": "easytdx",
    }
    tencent = [
        {"key": "usINX", "name": "标普 500", "region": "美国", "kind": "index",
         "close": 9999.0, "previous_close": 1.0, "pct": 1.0, "state": "fresh"},
        {"key": "hkHSI", "name": "恒生指数", "region": "中国香港", "kind": "index",
         "close": 22219.0, "previous_close": 22000.0, "pct": 1.0, "state": "fresh"},
    ]
    markets = premarket._overnight_markets(over)
    have = {m["key"] for m in markets}
    merged = [*markets, *[m for m in tencent if m["key"] not in have]]

    by_key = {m["key"]: m for m in merged}
    assert by_key["usINX"]["close"] == 5000.0          # easytdx 优先
    assert by_key["hkHSI"]["close"] == 22219.0         # 腾讯补位
    assert by_key["future:CNY0"]["pct"] == -0.78


def test_us10y_prefers_easytdx_over_fred(monkeypatch):
    """FRED 是日频且要 3s; easytdx 有实时 10Y 就用它, 没有才回 FRED。"""
    over = {"indices": [], "futures": [], "leaders": [], "treasuries": [],
            "us10y": 4.92, "as_of": "2026-11-24 08:00", "source": "easytdx"}
    monkeypatch.setattr(us_overnight, "fetch_overnight", lambda: over)
    monkeypatch.setattr(premarket, "_get", lambda *a, **k: "")
    monkeypatch.setattr(premarket, "_fetch_fred", lambda _c, s: 4.88 if s == "DGS10" else None)
    premarket.reset_cache()
    assert premarket.fetch_all()["macro"]["us10y"] == 4.92

    over = {**over, "us10y": None}
    monkeypatch.setattr(us_overnight, "fetch_overnight", lambda: over)
    assert premarket.fetch_all()["macro"]["us10y"] == 4.88
    premarket.reset_cache()
