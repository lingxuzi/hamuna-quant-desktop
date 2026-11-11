"""指数/ETF 日K同步的数据源路由 (#502)。

指数/ETF 日K跟随日K数据源: 生效源声明 daily 数据集时接管 (asset_type=index/etf),
源只服务股票 (fuyao) 时空手而归, 由链上下一跳 (内置 easytdx) 接管。
"""
from types import SimpleNamespace

import polars as pl
import pytest

from app.data_providers.capabilities import CapabilitySet
from app.services import index_sync, preferences


class _FakeProvider:
    """自定义源替身: iter_daily/get_daily 记录入参并产出预置帧; frames 为空模拟「仅服务股票」。"""

    def __init__(self, frames: list[pl.DataFrame] | None = None) -> None:
        self.frames = frames or []
        self.iter_calls: list[dict] = []

    def iter_daily(self, symbols, start_time=None, end_time=None, asset_type="stock", on_chunk_done=None):
        self.iter_calls.append({
            "symbols": list(symbols), "asset_type": asset_type,
            "start_time": start_time, "end_time": end_time,
        })
        yield from self.frames

    def get_daily(self, symbols, start_time=None, end_time=None, asset_type="stock", on_chunk_done=None):
        self.iter_calls.append({
            "symbols": list(symbols), "asset_type": asset_type,
            "start_time": start_time, "end_time": end_time,
        })
        return pl.concat(self.frames, how="diagonal_relaxed") if self.frames else pl.DataFrame()


class _FakeRepo:
    def __init__(self, tmp_path) -> None:
        self.tmp = tmp_path
        self.index_rows: list[pl.DataFrame] = []
        self.index_enriched: list[pl.DataFrame] = []
        self.etf_rows: list[pl.DataFrame] = []
        self.etf_enriched: list[pl.DataFrame] = []
        self.refreshed = 0

    @property
    def store(self):
        return SimpleNamespace(data_dir=self.tmp)

    def get_index_instruments(self):
        return pl.DataFrame({"symbol": ["000001.SH", "399001.SZ"]})

    def get_etf_instruments(self):
        return pl.DataFrame({"symbol": ["510300.SH"]})

    def append_index_daily(self, df):
        self.index_rows.append(df)

    def append_index_enriched(self, df):
        self.index_enriched.append(df)

    def append_etf_daily(self, df):
        self.etf_rows.append(df)

    def append_etf_enriched(self, df):
        self.etf_enriched.append(df)

    def refresh_index_views(self):
        self.refreshed += 1


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """路由到 fake_src, 默认日K源固定为内置 easytdx。"""
    monkeypatch.setattr(preferences, "get_daily_data_provider", lambda: "fake_src")
    monkeypatch.setattr(index_sync, "compute_enriched", lambda df, factors=None, instruments=None: df)
    from app.data_providers import custom as custom_sources
    # 默认日K源 = 内置 easytdx (capabilities._default_provider 读 custom.names())
    monkeypatch.setattr(custom_sources, "names", lambda: {"fake_src", "easytdx"})
    return SimpleNamespace(tmp_path=tmp_path, monkeypatch=monkeypatch)


def _route(harness, provider, fallback=None):
    """取数链 = 生效源 fake_src (+ 默认源 easytdx)。"""
    from app.data_providers import custom as custom_sources

    providers = {"fake_src": provider}
    if fallback is not None:
        providers["easytdx"] = fallback
    harness.monkeypatch.setattr(
        custom_sources, "provider_has_dataset",
        lambda name, ds: ds == "daily" and name in providers,
    )
    harness.monkeypatch.setattr(custom_sources, "get_provider", lambda name: providers[name])


def _index_df() -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": ["000001.SH", "399001.SZ"],
        "date": ["2026-10-20", "2026-10-20"],
        "close": [3813.79, 13118.62],
    })


def _etf_df() -> pl.DataFrame:
    return pl.DataFrame({"symbol": ["510300.SH"], "date": ["2026-10-20"], "close": [4.236]})


# ---------------------------------------------------------------- 指数日K
def test_custom_provider_takes_over_index_daily(harness):
    provider = _FakeProvider(frames=[_index_df()])
    fallback = _FakeProvider(frames=[_index_df()])
    _route(harness, provider, fallback)
    repo = _FakeRepo(harness.tmp_path)

    rows = index_sync.sync_and_persist_index_daily(repo, CapabilitySet())

    assert rows == 2
    assert provider.iter_calls[0]["asset_type"] == "index"
    assert provider.iter_calls[0]["symbols"] == ["000001.SH", "399001.SZ"]
    assert repo.index_rows and repo.index_rows[0].height == 2
    assert repo.index_enriched and repo.index_enriched[0].height == 2
    assert repo.refreshed == 1
    assert fallback.iter_calls == []  # 生效源已接管, 不落到默认源


def test_custom_index_daily_via_get_daily_only_provider(harness):
    """仅实现 get_daily (无 iter_daily) 的源: 包装为单帧, 同样接管。"""
    provider = _FakeProvider(frames=[_index_df()])
    provider.iter_daily = None  # 摘掉 iter_daily, 强制走 get_daily 分支
    fallback = _FakeProvider(frames=[_index_df()])
    _route(harness, provider, fallback)
    repo = _FakeRepo(harness.tmp_path)

    rows = index_sync.sync_and_persist_index_daily(repo, CapabilitySet())

    assert rows == 2
    assert provider.iter_calls[0]["asset_type"] == "index"
    assert fallback.iter_calls == []


def test_custom_provider_stock_only_falls_back_to_easytdx(harness):
    """源对 asset_type=index 空手而归 (fuyao 只服务股票) → 回退默认源 easytdx。"""
    provider = _FakeProvider(frames=[])
    fallback = _FakeProvider(frames=[_index_df()])
    _route(harness, provider, fallback)
    repo = _FakeRepo(harness.tmp_path)

    rows = index_sync.sync_and_persist_index_daily(repo, CapabilitySet())

    assert rows == 2
    assert provider.iter_calls[0]["asset_type"] == "index"
    assert fallback.iter_calls[0]["asset_type"] == "index"
    assert repo.index_rows and repo.index_rows[0].height == 2
    assert repo.refreshed == 1


def test_provider_without_daily_dataset_falls_back_to_easytdx(harness):
    from app.data_providers import custom as custom_sources

    provider = _FakeProvider(frames=[_index_df()])
    fallback = _FakeProvider(frames=[_index_df()])
    _route(harness, provider, fallback)
    # 生效源没声明 daily → 链上只剩默认源 easytdx
    harness.monkeypatch.setattr(
        custom_sources, "provider_has_dataset",
        lambda name, ds: ds == "daily" and name == "easytdx",
    )
    repo = _FakeRepo(harness.tmp_path)

    rows = index_sync.sync_and_persist_index_daily(repo, CapabilitySet())

    assert rows == 2
    assert provider.iter_calls == []
    assert fallback.iter_calls[0]["asset_type"] == "index"


def test_easytdx_selected_keeps_builtin_path(harness):
    """生效源就是内置 easytdx: 链上只有一跳, 数据由它直接提供。"""
    harness.monkeypatch.setattr(preferences, "get_daily_data_provider", lambda: "easytdx")
    fallback = _FakeProvider(frames=[_index_df()])
    _route(harness, _FakeProvider(frames=[_index_df()]), fallback)
    repo = _FakeRepo(harness.tmp_path)

    rows = index_sync.sync_and_persist_index_daily(repo, CapabilitySet())

    assert rows == 2
    assert fallback.iter_calls[0]["asset_type"] == "index"


# ---------------------------------------------------------------- ETF 日K
def test_custom_provider_takes_over_etf_daily(harness):
    provider = _FakeProvider(frames=[_etf_df()])
    fallback = _FakeProvider(frames=[_etf_df()])
    _route(harness, provider, fallback)
    repo = _FakeRepo(harness.tmp_path)

    rows = index_sync.sync_and_persist_etf_daily(repo, CapabilitySet())

    assert rows == 1
    assert provider.iter_calls[0]["asset_type"] == "etf"
    assert repo.etf_rows and repo.etf_rows[0].height == 1
    assert repo.etf_enriched and repo.etf_enriched[0].height == 1
    assert fallback.iter_calls == []


def test_custom_etf_stock_only_falls_back_to_easytdx(harness):
    provider = _FakeProvider(frames=[])
    fallback = _FakeProvider(frames=[_etf_df()])
    _route(harness, provider, fallback)
    repo = _FakeRepo(harness.tmp_path)

    rows = index_sync.sync_and_persist_etf_daily(repo, CapabilitySet())

    assert rows == 1
    assert provider.iter_calls[0]["asset_type"] == "etf"
    assert fallback.iter_calls[0]["asset_type"] == "etf"
    assert repo.etf_rows and repo.etf_rows[0].height == 1
