"""ETF 数据 API —— ETF 日K 历史补拉的主动入口。

盘后管道的 ETF 分支只从本地最新分区向前扩 (jobs/daily_pipeline.py 的
etf_start 取 etf_dates[-1]), ETF 历史短于挖掘所需的交易日数时没有任何
入口能补长, 挖掘页的可用性预检只能拦下并报「先去补数据」。
这条端点补上那个口子: 按天向前补拉 ETF 日K, 顺带把同批 enriched 重算到最新。

ETF 独立存储 (data/kline_etf_daily / data/kline_etf_enriched), 不与股票混表。
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.data_providers.capabilities import Cap
from app.market_time import cn_now
from app.services import index_sync
from app.services.pipeline_jobs import job_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/etf", tags=["etf"])


class EtfSyncIn(BaseModel):
    days: int = Field(365, ge=30, le=4000, description="向前补拉的自然日天数")
    batch_size: int = Field(200, ge=20, le=500, description="每批同步的 ETF 只数")


@router.post("/sync_daily")
async def sync_etf_daily(body: EtfSyncIn, request: Request) -> dict:
    """向前补拉 ETF 日K 历史 (后台任务), 返回 job_id 供前端轮询进度。"""
    repo = request.app.state.repo
    capset = request.app.state.capabilities
    if not capset.has(Cap.KLINE_DAILY_BATCH):
        raise HTTPException(status_code=403, detail="需要 Pro+ 权限 (batch K-line)")
    job_id, is_new = job_store.create()
    if not is_new:
        raise HTTPException(
            status_code=409, detail="已有数据任务正在运行, 请等待完成后再补拉 ETF 历史"
        )

    def progress(stage: str, pct: int, message: str, **kwargs) -> None:
        job_store.progress(job_id, stage, pct, message, **kwargs)

    def run() -> dict:
        return _sync_etf_history(
            repo,
            capset,
            days=body.days,
            batch_size=body.batch_size,
            progress=progress,
        )

    def execute() -> None:
        from app.services.pipeline_jobs import (
            JobCancelledError,
            release_run_slot,
            run_with_capacity,
            try_acquire_run_slot,
        )

        try:
            if not try_acquire_run_slot(job_id):
                job_store.fail(job_id, "已有数据任务尚未结束, 请稍后重试")
                return
            result = run_with_capacity(job_id, run)
            job_store.succeed(job_id, {"etf_sync": result})
        except JobCancelledError:
            pass
        except Exception as exc:
            logger.exception("ETF 历史补拉失败: %s", job_id)
            job_store.fail(job_id, str(exc))
        finally:
            release_run_slot(job_id)
            from app.api.data import invalidate_data_cache

            invalidate_data_cache()

    threading.Thread(target=execute, daemon=True, name="etf-sync").start()
    return {"job_id": job_id}


def _sync_etf_history(
    repo,
    capset,
    *,
    days: int,
    batch_size: int,
    progress: Callable[..., None],
) -> dict:
    """按批补拉 ETF 日K; 每批落盘后立刻按该批的完整本地历史重算 enriched。"""
    symbols = _etf_symbols(repo)
    if not symbols:
        raise ValueError("没有可用的 ETF 标的清单, 请先在数据页同步 ETF 维表")
    end = cn_now().replace(tzinfo=None)
    start = end - timedelta(days=days)
    total = len(symbols)
    rows = 0
    for i in range(0, total, batch_size):
        chunk = symbols[i : i + batch_size]
        base_pct = int(100 * i / total)
        span_pct = max(1, int(100 * len(chunk) / total))

        def on_chunk_done(
            done: int,
            chunk_total: int,
            *,
            _base: int = base_pct,
            _span: int = span_pct,
            _at: int = i,
        ) -> None:
            ratio = done / chunk_total if chunk_total else 0.0
            progress(
                "etf_sync",
                min(99, _base + int(_span * ratio)),
                f"补拉 ETF 日K {_at + done}/{total}",
            )

        rows += index_sync.sync_and_persist_etf_daily(
            repo,
            capset,
            start_date=start,
            end_date=end,
            symbols_override=chunk,
            on_chunk_done=on_chunk_done,
        )
    progress("etf_sync", 100, f"ETF 日K 补拉完成, {total} 只 / {rows} 行")
    return {
        "symbols": total,
        "rows_written": rows,
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
    }


def _etf_symbols(repo) -> list[str]:
    instruments = repo.get_etf_instruments()
    if instruments.is_empty() or "symbol" not in instruments.columns:
        index_sync.sync_etf_instruments(repo)
        instruments = repo.get_etf_instruments()
    if instruments.is_empty() or "symbol" not in instruments.columns:
        return []
    return sorted(set(instruments["symbol"].to_list()))
