"""信号自检 (check_signal_snapshot) 的行为约束。

核心不变量: signal_* 列是查询时现算的, 永不落盘 —— 只能看内存缓存。
读 parquet 的列来判「信号缺失」必然误报 (磁盘上永远没有信号列)。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl

from app.api.data import check_signal_snapshot
from app.strategy import custom_signals


class _FakeStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir


class _FakeRepo:
    """只实现 check_signal_snapshot 用到的接口。"""

    def __init__(self, data_dir: Path, frame: pl.DataFrame, as_of: date | None = None):
        self.store = _FakeStore(data_dir)
        self._frame = frame
        self._as_of = as_of
        self._enriched_warming = False

    def get_enriched_latest_asset(self, asset_type: str, refresh: bool = True):
        return self._frame, self._as_of

    def _latest_enriched_date_duckdb(self):
        return self._as_of


def _write_signal(data_dir: Path, sig_id: str = "t_sig") -> str:
    """落一个自定义信号定义, 返回它对应的列名。"""
    d = data_dir / "user_data" / "custom_signals"
    d.mkdir(parents=True, exist_ok=True)
    sig = {
        "id": sig_id,
        "enabled": True,
        "conditions": [{"left": "close", "op": ">", "right": 10}],
    }
    (d / f"{sig_id}.json").write_text(json.dumps(sig, ensure_ascii=False), encoding="utf-8")
    return custom_signals.resolve_column(sig)


def _write_partition(data_dir: Path, day: date) -> None:
    """落一个只含基础列的 enriched 分区 (信号列设计上不存在)。"""
    p = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}"
    p.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(
        {"symbol": ["600000.SH"], "date": [day], "open": [10.0], "high": [10.0],
         "low": [10.0], "close": [10.0], "volume": [100.0], "amount": [1000.0]}
    ).write_parquet(p / "part.parquet")


def test_cache_frame_with_signal_column_is_healthy(tmp_path, monkeypatch):
    """缓存帧里有信号列 → 正常, 即使磁盘分区根本没有信号列。"""
    monkeypatch.setattr(custom_signals, "missing_today_signals", set())
    col = _write_signal(tmp_path)
    day = date(2026, 11, 13)
    _write_partition(tmp_path, day)

    frame = pl.DataFrame({"symbol": ["600000.SH"], "date": [day], "close": [10.0], col: [True]})
    report = check_signal_snapshot(_FakeRepo(tmp_path, frame, day))

    assert report["ok"] is True
    assert report["missing"] == []
    assert report["defined"] == 1 and report["compiled"] == 1


def test_cache_frame_missing_signal_column_is_reported(tmp_path, monkeypatch):
    """缓存帧缺信号列 → 报缺失, 供修复按钮触发重算。"""
    monkeypatch.setattr(custom_signals, "missing_today_signals", set())
    col = _write_signal(tmp_path)
    day = date(2026, 11, 13)

    frame = pl.DataFrame({"symbol": ["600000.SH"], "date": [day], "close": [10.0]})
    report = check_signal_snapshot(_FakeRepo(tmp_path, frame, day))

    assert report["ok"] is False
    assert report["missing"] == [col]


def test_degraded_today_signal_is_reported(tmp_path, monkeypatch):
    """当日路径编译不出的信号 → 单独报 today_degraded。"""
    col = _write_signal(tmp_path)
    monkeypatch.setattr(custom_signals, "missing_today_signals", {col})
    day = date(2026, 11, 13)

    frame = pl.DataFrame({"symbol": ["600000.SH"], "date": [day], "close": [10.0], col: [True]})
    report = check_signal_snapshot(_FakeRepo(tmp_path, frame, day))

    assert report["ok"] is False
    assert report["today_degraded"] == [col]
    assert report["missing"] == []


def test_no_definitions_is_healthy(tmp_path, monkeypatch):
    """盘上没有信号定义 → 无从缺失, 判正常。"""
    monkeypatch.setattr(custom_signals, "missing_today_signals", set())
    day = date(2026, 11, 13)
    frame = pl.DataFrame({"symbol": ["600000.SH"], "date": [day], "close": [10.0]})

    report = check_signal_snapshot(_FakeRepo(tmp_path, frame, day))

    assert report["ok"] is True
    assert report["defined"] == 0 and report["compiled"] == 0


def test_warming_cache_is_pending(tmp_path, monkeypatch):
    """缓存预热中返回空表: 不能判异常, 标 pending。"""
    monkeypatch.setattr(custom_signals, "missing_today_signals", set())
    _write_signal(tmp_path)
    repo = _FakeRepo(tmp_path, pl.DataFrame(), None)
    repo._enriched_warming = True

    report = check_signal_snapshot(repo)

    assert report["ok"] is True
    assert report.get("pending") is True
