"""回归: sync_and_persist_daily_batch 必须接受 failed_out 出参。

daily_pipeline (#514) 的修复/定向分支传 failed_out=repair_failed_symbols,
但本函数签名漏接该参数 → TypeError, 数据修正/boot 自检修复路径必炸
(v0.3.21/v0.3.22 用户同步失败截图: "got an unexpected keyword argument
'failed_out'")。

新契约 (TickFlow 下线后): 日K 只走 provider 链。provider 契约没有逐票失败
通道 —— 取不到数据的标的按「入库 0 行」回报 (zero_row_out), failed_out 保留
为空; 源明确请求失败 (ProviderRequestError) 时异常上抛, 不静默当成功。
"""
from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest

from app.data_providers.base import ProviderRequestError
from app.services import kline_sync
from app.data_providers.repository import DataStore, KlineRepository


class _Provider:
    """假日K源: 默认空 payload; error 非空时抛 ProviderRequestError。"""

    def __init__(self, error: Exception | None = None):
        self._error = error
        self.requested: list[str] = []

    def get_daily(self, symbols, *args, **kwargs):
        self.requested = list(symbols)
        if self._error is not None:
            raise self._error
        return pl.DataFrame()


@pytest.fixture
def repo(tmp_path):
    return KlineRepository(DataStore(tmp_path))


@pytest.fixture(autouse=True)
def _daily_source(monkeypatch):
    """本文件测的是日K同步入口的 failed_out 透传, 把生效日K源固定到假 provider
    (默认源已是 easytdx, 否则会走内置源发真实请求)。"""
    from app.data_providers import custom

    provider = _Provider()
    monkeypatch.setattr(
        kline_sync.preferences, "get_data_provider", lambda dataset: "mock_src",
    )
    monkeypatch.setattr(
        custom, "resolve_provider", lambda name, dataset: (provider, name),
    )
    return provider


def test_accepts_failed_out_and_reports_zero_rows(repo):
    """failed_out 出参被接受 (不抛 TypeError); 0 行标的按调用方写法回报。"""
    capset = SimpleNamespace(has=lambda cap: True, limits=lambda cap: None)
    failed: list[str] = []
    zero: list[str] = []

    written = kline_sync.sync_and_persist_daily_batch(
        ["600519.SH", "000001.SZ"], repo, capset,
        zero_row_out=zero, failed_out=failed,
    )

    assert written == 0
    # provider 契约无逐票失败通道 → failed_out 不填充
    assert failed == []
    # 取不到数据的标的按 0 行回报 (调用方原始写法)
    assert zero == ["600519.SH", "000001.SZ"]


def test_failed_out_omitted_still_works(repo):
    capset = SimpleNamespace(has=lambda cap: True, limits=lambda cap: None)

    assert kline_sync.sync_and_persist_daily_batch(
        ["600519.SH"], repo, capset) == 0


def test_provider_request_failure_is_not_silently_treated_as_success(repo, _daily_source):
    """源明确请求失败 → 异常上抛 (不静默当成功, 交由上层记 stage_error)。"""
    _daily_source._error = ProviderRequestError("日K源不可用")
    capset = SimpleNamespace(has=lambda cap: True, limits=lambda cap: None)

    with pytest.raises(ProviderRequestError):
        kline_sync.sync_and_persist_daily_batch(
            ["600519.SH"], repo, capset, failed_out=[])
