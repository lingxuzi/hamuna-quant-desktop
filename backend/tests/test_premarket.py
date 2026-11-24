"""盘前简报 / 外部风险温度 / 事件传导链 单测。

真实 payload 固定成常量, 不联网 —— 出站接口会变, 测试要的是解析口径不变。
"""
from __future__ import annotations

import pytest

from app.services import premarket

_PAD = "~0" * 25

TENCENT_SAMPLE = (
    'v_usINX="1~标普500~.INX~7237.17~6910.39' + _PAD
    + '~2026-11-23 16:42:33~326.78~4.73~";\n'
    'v_hkHSI="100~恒生指数~HSI~22219.650~22183.000' + _PAD
    + '~2026-11-24 16:08:00~36.65~0.17~";\n'
)

SINA_SAMPLE = (
    'var hq_str_hf_CHA50CFD="12711.900,,12711.000,12713.000,12784.000,12711.000,'
    '18:52:26,12771.000,12770.000,984581,25,42,2026-11-24,富时中国A50期货";\n'
    'var hq_str_fx_susdcnh="18:52:15,6.661200,6.663200,6.666400,92,6.666000,6.668300,'
    '6.659100,6.661200,离岸人民币（香港）,-0.080000,-0.005200,0.00138,";\n'
    'var hq_str_DINIW="18:52:23,104.1043,104.1043,104.3248,3136,104.3412,104.4057,'
    '104.0921,104.1043,美元指数,2026-11-24";\n'
)

FRED_SAMPLE = "observation_date,VIXCLS\n2026-11-19,28.44\n2026-11-20,35.89\n"


def test_parse_tencent_fields_and_pct():
    rows = premarket.parse_tencent(TENCENT_SAMPLE)
    by_key = {r["key"]: r for r in rows}
    assert set(by_key) == {"usINX", "hkHSI"}
    assert by_key["usINX"]["close"] == 7237.17
    assert by_key["usINX"]["previous_close"] == 6910.39
    assert by_key["usINX"]["pct"] == 4.73
    assert by_key["usINX"]["name"] == "标普 500"
    assert by_key["usINX"]["quote_time"] == "2026-11-23 16:42:33"


def test_parse_tencent_derives_pct_when_missing():
    # 涨跌字段为空时按 close/prev 反算, 不能直接丢成 None
    broken = 'v_hkHSI="100~恒生指数~HSI~220.0~200.0~' + "~" * 25 + '~";\n'
    rows = premarket.parse_tencent(broken)
    assert rows and rows[0]["pct"] == 10.0


def test_parse_sina_uses_role_specific_offsets():
    rows = premarket.parse_sina(SINA_SAMPLE)
    by_key = {r["key"]: r for r in rows}
    # 期货型: [0]=最新 [7]=昨收
    assert by_key["hf_CHA50CFD"]["close"] == 12711.9
    assert by_key["hf_CHA50CFD"]["previous_close"] == 12771.0
    assert by_key["hf_CHA50CFD"]["role"] == "scored"
    # 汇率型: [1]=最新 [5]=昨收 [10]=涨跌%
    assert by_key["fx_susdcnh"]["close"] == 6.6612
    assert by_key["fx_susdcnh"]["pct"] == -0.08
    assert by_key["DINIW"]["pct"] is None or isinstance(by_key["DINIW"]["pct"], float)


def test_parse_fred_takes_last_valid_value():
    assert premarket.parse_fred_csv(FRED_SAMPLE) == 35.89
    # FRED 缺失值写作 '.', 必须跳过而不是当成 0
    csv_missing = "observation_date,DGS10\n2026-11-19,4.92\n2026-11-20,.\n"
    assert premarket.parse_fred_csv(csv_missing) == 4.92
    assert premarket.parse_fred_csv("observation_date,DGS10\n") is None


# ---------------------------------------------------------------------------
# 风险温度: 用参考项目 README 里那一组公开数字回放
# ---------------------------------------------------------------------------

def _overview(sh_pct=-0.59, up=776, down=4805, lu=37, ld=33, seal=64.9):
    return {
        "indices": [{"symbol": "000001.SH", "change_pct": sh_pct}],
        "breadth": {"total": up + down, "up": up, "down": down},
        "limit": {"limit_up": lu, "limit_down": ld, "seal_rate": seal},
    }


def _snap(equity=(4.73, 6.51, 3.01, 2.35, -0.37), a50=1.31, cnh=-0.07, dxy=-0.03,
          vix=15.44, y10=4.94, health=None):
    # 必须用真实 key —— compute_risk 只对 _SCORED_MARKET_KEYS 里的市场取均值,
    # 用假 key 会让「外围股市」恒为 0 而测试仍然通过。
    keys = ["usINX", "usIXIC", "usDJI", "usVGK", "hkHSI", "N225"]
    markets = [{"key": k, "pct": p, "close": 1.0, "state": "fresh"}
               for k, p in zip(keys, equity, strict=False)]
    return {
        "markets": markets,
        "cross_assets": [
            {"key": "hf_CHA50CFD", "close": 12813.3, "pct": a50, "role": "scored"},
            {"key": "fx_susdcnh", "close": 6.6615, "pct": cnh, "role": "scored"},
            {"key": "DINIW", "close": 104.31, "pct": dxy, "role": "scored"},
        ],
        "macro": {"vix": vix, "us10y": y10, "nikkei225": None},
        "health": health or {"tencent": "fresh", "sina": "fresh", "fred": "fresh",
                             "nikkei": "fresh"},
        "as_of": "2026-11-24 09:00",
    }


def test_compute_risk_matches_reference_readme():
    """README 的拆解表: -4.7 / -14.4 / -3.9 / +20.0 / +7.8 / +1.0 / +0.0 / -5.0 / +0.0。"""
    news = [{"title": "利好 突破 上涨", "snippet": ""},
            {"title": "风险 抛售 衰退", "snippet": ""}]
    risk = premarket.compute_risk(_overview(), _snap(), news)
    got = {s["key"]: s["score"] for s in risk["signals"]}

    assert got["a_momentum"] == pytest.approx(-4.7, abs=0.1)
    assert got["breadth"] == pytest.approx(-14.4, abs=0.2)
    assert got["sentiment"] == pytest.approx(-3.9, abs=0.3)
    assert got["global_equity"] == pytest.approx(20.0, abs=0.2)
    assert got["a50"] == pytest.approx(7.9, abs=0.2)
    assert got["fx"] == pytest.approx(1.0, abs=0.3)
    assert got["vix"] == pytest.approx(0.0)
    assert got["us10y"] == pytest.approx(-5.0)
    assert got["news_tone"] == pytest.approx(0.0)
    # 合计四舍五入到整数
    assert risk["score"] == round(sum(got.values()))
    assert -100 <= risk["score"] <= 100


def test_compute_risk_extreme_inputs_stay_in_range():
    hot = premarket.compute_risk(
        _overview(sh_pct=9.9, up=5000, down=0, lu=500, ld=0, seal=100.0),
        _snap(equity=(9.9,) * 6, a50=9.9, cnh=-9.9, dxy=-9.9, vix=10, y10=2.0),
        [{"title": "利好 突破 上涨 增长", "snippet": ""}],
    )
    cold = premarket.compute_risk(
        _overview(sh_pct=-9.9, up=0, down=5000, lu=0, ld=500, seal=0.0),
        _snap(equity=(-9.9,) * 6, a50=-9.9, cnh=9.9, dxy=9.9, vix=45, y10=5.6),
        [{"title": "暴跌 衰退 抛售 违约", "snippet": ""}],
    )
    assert hot["score"] == 100
    assert cold["score"] == -100


def test_compute_risk_degrades_confidence_when_sources_missing():
    risk = premarket.compute_risk(
        _overview(), _snap(vix=None, y10=None,
                           health={"tencent": "fresh", "sina": "missing",
                                   "fred": "missing", "nikkei": "missing"}),
        [],
    )
    assert risk["confidence"] in {"中", "低"}
    assert risk["degraded"] is True


def test_stance_tiers():
    assert premarket._stance(30) == ("积极但不追高", "#c9211e")
    assert premarket._stance(10) == ("谨慎偏多", "#e05a47")
    assert premarket._stance(0) == ("中性等待", "#8c959f")
    assert premarket._stance(-10) == ("谨慎防守", "#3b9b68")
    assert premarket._stance(-50) == ("防守优先", "#08783e")


def test_every_stance_has_summary_and_advice():
    """五档都要有结论句和研究观察 —— 缺一档会让对应分数区间直接 KeyError。"""
    tiers = {premarket._stance(s)[0] for s in (40, 15, 0, -15, -40)}
    assert tiers == set(premarket._STANCE_TEXT) == {
        "积极但不追高", "谨慎偏多", "中性等待", "谨慎防守", "防守优先"}
    for stance, (summary, advice) in premarket._STANCE_TEXT.items():
        assert len(summary) > 10, stance
        assert 1 <= len(advice) <= 3, stance

    risk = premarket.compute_risk(_overview(), _snap(), [])
    assert risk["summary"] == premarket._STANCE_TEXT[risk["stance"]][0]
    assert risk["advice"] == list(premarket._STANCE_TEXT[risk["stance"]][1])


def test_extra_us_indices_do_not_score():
    """easytdx 多给的 NDX/SOX/HXC 只展示 —— 计进外围股市等于把美股加权两次。"""
    snap = _snap(equity=(1.0,) * 6)
    base = premarket.compute_risk(_overview(), snap, [])
    snap["markets"].extend([
        {"key": "usNDX", "pct": 50.0, "close": 1.0, "state": "fresh"},
        {"key": "usSOX", "pct": 50.0, "close": 1.0, "state": "fresh"},
        {"key": "usHXC", "pct": 50.0, "close": 1.0, "state": "fresh"},
    ])
    after = premarket.compute_risk(_overview(), snap, [])
    assert after["score"] == base["score"]


# ---------------------------------------------------------------------------
# 事件链
# ---------------------------------------------------------------------------

def test_build_events_buckets_and_watch_state():
    news = [
        {"title": "美联储宣布降息 25 个基点", "snippet": "", "source": "华尔街见闻",
         "published_date": "2026-11-24 08:00", "url": "https://x/1"},
        {"title": "原油价格上涨 3%", "snippet": "", "source": "CNBC",
         "published_date": "2026-11-24 07:00", "url": "https://x/2"},
    ]
    events = premarket.build_events(news, _snap())
    by_key = {e["key"]: e for e in events}
    assert set(by_key) == {"central_bank", "inflation", "geopolitics", "technology",
                           "commodities", "currency", "domestic_policy"}
    assert by_key["central_bank"]["headline_count"] == 1
    assert by_key["commodities"]["headline_count"] == 1
    assert by_key["geopolitics"]["headline_count"] == 0
    # watch 里每个代号都要有 state, 便于前端区分「最近有效值」和「当前值」
    for item in by_key["central_bank"]["facts"]["watch"]:
        assert item["state"] in {"fresh", "stale", "missing"}
        assert "code" in item
    # US10Y 来自 macro, 快照里没有该键时为 missing 而不是崩
    assert any(w["code"] == "US10Y" for w in by_key["central_bank"]["facts"]["watch"])


def test_build_events_separates_facts_from_inference():
    """事实(标题+指标读数)与推断(传导路径/映射/失效条件)必须是两层。

    AI 复盘会把这段直接塞进 prompt —— 混在一起就会把本系统的假设写成新闻事实。
    """
    news = [{"title": "美联储宣布降息 25 个基点", "snippet": "", "source": "华尔街见闻",
             "published_date": "2026-11-24 08:00", "url": "https://x/1"}]
    ev = next(e for e in premarket.build_events(news, _snap()) if e["key"] == "central_bank")
    assert set(ev["facts"]) == {"headlines", "watch"}
    assert set(ev["inference"]) == {"macro_path", "a_share_lens", "invalidation"}
    assert ev["facts"]["headlines"][0]["title"].startswith("美联储")
    assert "→" in ev["inference"]["macro_path"]
    # 扁平字段不再出现 —— 防止有人又写回混合结构
    assert "headlines" not in ev and "macro_path" not in ev


def test_build_events_same_headline_can_hit_multiple_buckets():
    news = [{"title": "美联储降息带动原油与美元同步波动", "snippet": "",
             "source": "CNBC", "published_date": "2026-11-24 08:00", "url": None}]
    events = premarket.build_events(news, _snap())
    hits = [e["key"] for e in events if e["headline_count"] > 0]
    assert "central_bank" in hits and "commodities" in hits


def test_snapshot_returns_empty_payload_when_all_sources_fail(monkeypatch):
    premarket.reset_cache()

    def boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(premarket, "fetch_all", boom)
    payload = premarket.snapshot(now=1e12)
    assert payload["markets"] == [] and payload["cross_assets"] == []
    assert payload["stale"] is True
    premarket.reset_cache()


def test_snapshot_keeps_last_good_value_on_failure(monkeypatch):
    premarket.reset_cache()
    good = {"markets": [{"key": "usINX", "pct": 1.0}], "cross_assets": [], "macro": {},
            "health": {"tencent": "fresh"}, "as_of": "2026-11-24 09:00"}
    monkeypatch.setattr(premarket, "fetch_all", lambda: good)
    assert premarket.snapshot(now=1000.0)["stale"] is not True

    def boom():
        raise RuntimeError("offline")

    monkeypatch.setattr(premarket, "fetch_all", boom)
    stale = premarket.snapshot(now=1000.0 + premarket._TTL_S + 1)
    assert stale["stale"] is True
    assert stale["markets"] == good["markets"]
    premarket.reset_cache()
