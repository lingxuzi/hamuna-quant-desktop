"""要闻服务单测: 解析、去重、标的映射、缓存、通知冷却。

RSS 样本取自东财真实返回 (含 data-code 与同类标题), 不联网。
"""
from __future__ import annotations

import json

import pytest

from app.services import news

EASTMONEY_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>东方财富</title>
<item>
  <title><![CDATA[科顺股份：拟以5000万元至1亿元回购公司股份]]></title>
  <link>http://stock.eastmoney.com/news/1,202611243938535879.html</link>
  <description><![CDATA[<p style="display:none;"><br /></p>
  <p><a href="http://quote.eastmoney.com/sz300737.html" data-code="300737|0|2">科顺股份</a>
  公告回购。</p>]]></description>
  <pubDate>Tue, 24 Nov 2026 17:49:02 +0800</pubDate>
</item>
<item>
  <title><![CDATA[凌钢股份：控股股东拟1500万元—3000万元增持公司股份]]></title>
  <link>http://stock.eastmoney.com/news/1,202611243938535880.html</link>
  <description><![CDATA[<p><a href="http://quote.eastmoney.com/sh600231.html" data-code="600231|1|2">凌钢股份</a></p>]]></description>
  <pubDate>Tue, 24 Nov 2026 17:44:00 +0800</pubDate>
</item>
<item>
  <title><![CDATA[半导体板块异动]]></title>
  <link>http://stock.eastmoney.com/news/1,202611243938535881.html</link>
  <description><![CDATA[<p><a href="http://quote.eastmoney.com/bk0740.html" data-code="BK0740|90|2">半导体</a>
  <a href="http://quote.eastmoney.com/us/NVDA.html" data-code="NVDA|105|2">英伟达</a></p>]]></description>
  <pubDate>Tue, 24 Nov 2026 17:30:00 +0800</pubDate>
</item>
<item>
  <title><![CDATA[比特币突破 10 万美元]]></title>
  <link>http://stock.eastmoney.com/news/1,202611243938535882.html</link>
  <description><![CDATA[<p>稳定币概念集体上涨</p>]]></description>
  <pubDate>Tue, 24 Nov 2026 17:20:00 +0800</pubDate>
</item>
</channel></rss>
"""

DUPLICATE_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<item><title><![CDATA[科顺股份：拟以5000万元至1亿元回购公司股份！]]></title>
<link>http://other.example.com/a</link><description><![CDATA[x]]></description>
<pubDate>Tue, 24 Nov 2026 17:50:00 +0800</pubDate></item>
<item><title><![CDATA[万华化学：董事长提议回购3亿元-5亿元公司股份]]></title>
<link>http://other.example.com/b</link>
<description><![CDATA[<a href="http://quote.eastmoney.com/sh600309.html" data-code="600309|1|2">万华化学</a>]]></description>
<pubDate>Tue, 24 Nov 2026 17:31:00 +0800</pubDate></item>
</channel></rss>
"""


def test_parse_feed_normalizes_fields():
    rows = news.parse_feed(EASTMONEY_XML, "东方财富")
    assert len(rows) == 4
    first = rows[0]
    assert first["title"].startswith("科顺股份")
    assert first["source"] == "东方财富"
    assert first["published_date"] == "2026-11-24 17:49"   # RFC822 → 北京时间
    assert first["url"].startswith("http://stock.eastmoney.com")
    assert first["id"] and len(first["id"]) == 16
    # HTML 标签被洗掉, 但正文摘要保留
    assert "<p" not in first["snippet"]


def test_extract_symbols_only_keeps_a_share():
    rows = news.parse_feed(EASTMONEY_XML, "东方财富")
    assert rows[0]["symbols"] == ["300737.SZ"]
    assert rows[1]["symbols"] == ["600231.SH"]
    # 90=板块 105=美股, 本仓库无对应标的 → 丢弃
    assert rows[2]["symbols"] == []


def test_extract_symbols_dedupes_repeated_codes():
    html = ('<a data-code="600519|1|2">a</a><a data-code="600519|1|2">b</a>'
            '<a data-code="000001|0|2">c</a>')
    assert news.extract_symbols(html) == ["600519.SH", "000001.SZ"]


def test_redline_keywords_drop_item():
    rows = news.parse_feed(EASTMONEY_XML, "东方财富")
    titles = [r["title"] for r in rows]
    assert any("比特币" in t for t in titles)     # 解析层不负责过滤
    kept = [r for r in rows if not news._is_redline(r)]
    assert not any("比特币" in r["title"] for r in kept)


def test_dedupe_removes_near_identical_titles():
    a = news.parse_feed(EASTMONEY_XML, "东方财富")
    b = news.parse_feed(DUPLICATE_XML, "别的源")
    merged = news.dedupe([*a, *b])
    cs = [r for r in merged if "科顺股份" in r["title"]]
    assert len(cs) == 1, "同一批回购公告的两个近似标题应被判重"
    # 万华化学只有一条, 必须保留
    assert any("万华化学" in r["title"] for r in merged)


def test_dedupe_keeps_short_distinct_titles():
    """短标题不做相似度比较 —— 否则「涨停复盘」和「跌停复盘」会被误判成重复。"""
    items = [
        {"id": "1", "title": "涨停复盘", "symbols": []},
        {"id": "2", "title": "跌停复盘", "symbols": []},
    ]
    assert len(news.dedupe(items)) == 2


def test_order_puts_domestic_sources_first():
    """纯时间序会让 Seeking Alpha 的英文财报占满首屏, 把 A 股消息挤下去。"""
    rows = [
        {"id": "1", "title": "US earnings", "source": "Seeking Alpha", "published_date": "2026-11-24 19:30"},
        {"id": "2", "title": "国内要闻", "source": "东方财富", "published_date": "2026-11-24 17:00"},
        {"id": "3", "title": "海外宏观", "source": "CNBC", "published_date": "2026-11-24 19:40"},
    ]
    assert [r["id"] for r in news.order(rows)] == ["2", "3", "1"]


def test_order_keeps_time_order_within_same_tier():
    rows = [
        {"id": "a", "source": "东方财富", "published_date": "2026-11-24 17:00"},
        {"id": "b", "source": "华尔街见闻", "published_date": "2026-11-24 19:00"},
    ]
    assert [r["id"] for r in news.order(rows)] == ["b", "a"]


def test_normalize_title_strips_punctuation():
    assert news.normalize_title("科顺股份：拟回购！") == news.normalize_title("科顺股份拟回购")


def test_refresh_merges_with_existing_cache(tmp_path, monkeypatch):
    fresh = news.parse_feed(EASTMONEY_XML, "东方财富")
    monkeypatch.setattr(news, "fetch_all", lambda: fresh)

    first = news.refresh(tmp_path)
    assert len(first) == len(fresh)

    # 第二轮只给一条新条目, 旧条目必须靠缓存续上
    only_new = [{"id": "abc123", "title": "全新的一条要闻标题内容", "snippet": "",
                 "source": "X", "url": None, "published_date": "2026-11-24 20:00",
                 "symbols": []}]
    monkeypatch.setattr(news, "fetch_all", lambda: only_new)
    second = news.refresh(tmp_path)
    ids = {r["id"] for r in second}
    assert "abc123" in ids and fresh[0]["id"] in ids

    payload = json.loads((tmp_path / "news" / "date=2026-11-24.json").read_text("utf-8"))
    assert payload["date"] == "2026-11-24"


def test_list_recent_filters_by_symbol_and_reads_yesterday(tmp_path):
    today = news.cn_today()
    yday = today.fromordinal(today.toordinal() - 1)
    today_file = tmp_path / "news" / f"date={today.isoformat()}.json"
    yday_file = tmp_path / "news" / f"date={yday.isoformat()}.json"
    today_file.parent.mkdir(parents=True)
    today_file.write_text(json.dumps({"items": [
        {"id": "t1", "title": "今天", "published_date": "2026-11-24 10:00",
         "symbols": ["600519.SH"]}]}), "utf-8")
    yday_file.write_text(json.dumps({"items": [
        {"id": "y1", "title": "昨天", "published_date": "2026-11-23 10:00",
         "symbols": ["000001.SZ"]}]}), "utf-8")

    assert [r["id"] for r in news.list_recent(tmp_path, 10)] == ["t1", "y1"]
    assert [r["id"] for r in news.list_recent(tmp_path, 10, ["600519.SH"])] == ["t1"]
    assert news.list_recent(tmp_path, 10, ["999999.SZ"]) == []


def test_list_recent_ignores_corrupt_cache(tmp_path):
    f = tmp_path / "news" / f"date={news.cn_today().isoformat()}.json"
    f.parent.mkdir(parents=True)
    f.write_text("{not json", "utf-8")
    assert news.list_recent(tmp_path, 5) == []


# ---------------------------------------------------------------------------
# 通知冷却
# ---------------------------------------------------------------------------

class _Prefs:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def get_system_notify_enabled(self) -> bool:
        return self.enabled


@pytest.fixture
def notify_capture(monkeypatch):
    sent: list[tuple[str, str]] = []

    def fake_notify(title, message, icon=None):
        sent.append((title, message))
        return True

    import app.services.notify_adapter as adapter
    import app.services.preferences as prefs_mod

    monkeypatch.setattr(adapter, "notify", fake_notify)
    monkeypatch.setattr(prefs_mod, "get_system_notify_enabled", lambda: True)
    monkeypatch.setattr(news, "_watch_targets", lambda _d: {"600519.SH"})
    return sent


def test_notify_fires_once_per_cooldown(tmp_path, notify_capture):
    poller = news.NewsPoller(data_dir=tmp_path)
    items = [{"title": "贵州茅台上调出厂价", "symbols": ["600519.SH"]}]

    poller._notify_hits(items)
    assert len(notify_capture) == 1
    assert "贵州茅台" in notify_capture[0][1]

    # 冷却窗口内再来一次 → 不重复弹
    poller._notify_hits(items)
    assert len(notify_capture) == 1

    # 过期后可以再弹
    poller._cooldown["600519.SH"] -= news._NOTIFY_COOLDOWN_S + 1
    poller._notify_hits(items)
    assert len(notify_capture) == 2


def test_notify_shows_symbol_name_not_code(tmp_path, notify_capture):
    """通知正文给人看, 不能出现 600519.SH 这种代码。"""

    class _Repo:
        def get_name_map(self, symbols):
            return {"600519.SH": "贵州茅台"}

    poller = news.NewsPoller(data_dir=tmp_path, repo=_Repo())
    poller._notify_hits([{"title": "贵州茅台上调出厂价", "symbols": ["600519.SH"]}])
    assert "贵州茅台" in notify_capture[0][1]
    assert "600519.SH" not in notify_capture[0][1]


def test_notify_falls_back_to_code_when_name_missing(tmp_path, notify_capture):
    """仓库查不到名称时仍然要通知, 不能静默丢掉。"""

    class _Repo:
        def get_name_map(self, symbols):
            raise RuntimeError("dim table gone")

    news.NewsPoller(data_dir=tmp_path, repo=_Repo())._notify_hits(
        [{"title": "某公司公告", "symbols": ["600519.SH"]}])
    assert len(notify_capture) == 1
    assert "600519.SH" in notify_capture[0][1]


def test_notify_skips_non_watchlist_symbol(tmp_path, notify_capture):
    poller = news.NewsPoller(data_dir=tmp_path)
    poller._notify_hits([{"title": "无关个股公告", "symbols": ["000002.SZ"]}])
    assert notify_capture == []


def test_notify_respects_switch_off(tmp_path, monkeypatch):
    import app.services.notify_adapter as adapter
    import app.services.preferences as prefs_mod

    sent: list = []
    monkeypatch.setattr(adapter, "notify", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(prefs_mod, "get_system_notify_enabled", lambda: False)
    monkeypatch.setattr(news, "_watch_targets", lambda _d: {"600519.SH"})

    news.NewsPoller(data_dir=tmp_path)._notify_hits(
        [{"title": "x", "symbols": ["600519.SH"]}])
    assert sent == []


def test_notify_requires_symbols(tmp_path, notify_capture):
    """境外源没有标的映射 —— 不能因为标题里有中文就误报。"""
    news.NewsPoller(data_dir=tmp_path)._notify_hits(
        [{"title": "美股收盘涨跌不一", "symbols": []}])
    assert notify_capture == []


def test_cached_with_names_attaches_name_map(tmp_path):
    f = tmp_path / "news" / f"date={news.cn_today().isoformat()}.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"items": [
        {"id": "a", "title": "茅台公告", "published_date": "2026-11-24 10:00",
         "symbols": ["600519.SH"]}]}), "utf-8")

    class _Repo:
        def get_name_map(self, symbols):
            return {"600519.SH": "贵州茅台"}

    rows = news.cached_with_names(tmp_path, 5, None, _Repo())
    assert rows[0]["symbol_names"] == {"600519.SH": "贵州茅台"}


def test_cached_with_names_survives_repo_failure(tmp_path):
    f = tmp_path / "news" / f"date={news.cn_today().isoformat()}.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"items": [
        {"id": "a", "title": "茅台公告", "symbols": ["600519.SH"]}]}), "utf-8")

    class _Bad:
        def get_name_map(self, symbols):
            raise RuntimeError("dim table gone")

    rows = news.cached_with_names(tmp_path, 5, None, _Bad())
    assert len(rows) == 1 and "symbol_names" not in rows[0]
