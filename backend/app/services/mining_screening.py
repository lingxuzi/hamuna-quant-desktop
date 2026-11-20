"""L1 全量因子筛选的后台作业 —— /mining/auto 立即返回, 筛选在 spawn 子进程跑完。

为什么要后台: L1 筛选是全市场因子批量回测 (实测 1-3 分钟)。同步挂在请求上时
请求线程被拖到超时 (前端 30s), 同时 API 进程的重活闸被独占, 其它重活接口
跟着排队。放进 spawn 子进程后重活闸 (进程内单例) 各算各的, API 进程不受影响。

状态落盘而不是只放内存: 多 worker 部署时 POST 与 GET 可能落在不同进程。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from app.backtest.mining import MAX_FINALISTS
from app.backtest.worker import make_worker_task, run_worker_task

# 作业终态 (succeeded 含"无达标因子"与"已建 run"两种)
SCREENING_TERMINAL_STATUSES = ("succeeded", "failed")
# 进度播报粒度: 每 N 个因子换一次文案, 免得日志被上百行刷屏
_LABEL_BUCKET = 10
# queued/running 超过这么久没更新 → 判定为进程重启遗留的僵尸作业
_STALE_AFTER_S = 600.0


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class MiningScreeningStore:
    """一次 L1 筛选作业的状态文件 (data/research/mining/screening/<id>.json)。"""

    def __init__(self, data_dir: Path | str) -> None:
        self._dir = (Path(data_dir).resolve() / "research" / "mining" / "screening")
        self._lock = threading.RLock()

    def create(self) -> dict[str, Any]:
        screening_id = f"scr_{uuid.uuid4().hex[:12]}"
        now = time.time()
        job = {
            "id": screening_id,
            "status": "queued",
            "label": "排队中",
            "done": 0,
            "total": 0,
            "started": False,
            "reason": None,
            "error": None,
            "run_id": None,
            "screening": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            _atomic_write_json(self._path(screening_id), job)
        return job

    def get(self, screening_id: str) -> dict[str, Any] | None:
        path = self._path(screening_id)
        if not path.is_file():
            return None
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if job.get("status") in ("queued", "running") and (
            time.time() - float(job.get("updated_at") or 0) > _STALE_AFTER_S
        ):
            # 进程重启后没人再推进它 → 判死, 免得前端一直转圈
            job["status"] = "failed"
            job["error"] = "因子筛选任务已中断 (后端进程重启), 请重试"
            job["label"] = "因子筛选已中断"
        return job

    def update(self, screening_id: str, **patch: Any) -> dict[str, Any] | None:
        with self._lock:
            job = self.get(screening_id)
            if job is None:
                return None
            job.update(patch)
            job["updated_at"] = time.time()
            _atomic_write_json(self._path(screening_id), job)
            return job

    def _path(self, screening_id: str) -> Path:
        safe = "".join(ch for ch in screening_id if ch.isalnum() or ch in "-_")
        if not safe:
            raise ValueError("invalid screening id")
        return self._dir / f"{safe}.json"


def start_screening_job(
    *,
    store: MiningScreeningStore,
    screening_id: str,
    payload: Any,
    data_dir: Path,
    repo: Any,
    app_state: Any,
    manager: Any,
    screen_end: date,
) -> None:
    """起后台线程驱动筛选子进程 (请求线程立即返回)。

    payload: MiningAutoStartRequest (按属性取值), 线程里只读不写。
    """
    threading.Thread(
        target=_run_screening_job,
        kwargs={
            "store": store,
            "screening_id": screening_id,
            "payload": payload,
            "data_dir": data_dir,
            "repo": repo,
            "app_state": app_state,
            "manager": manager,
            "screen_end": screen_end,
        },
        name=f"mining-screening-{screening_id}",
        daemon=True,
    ).start()


def _run_screening_job(
    *,
    store: MiningScreeningStore,
    screening_id: str,
    payload: Any,
    data_dir: Path,
    repo: Any,
    app_state: Any,
    manager: Any,
    screen_end: date,
) -> None:
    """线程主体: 子进程跑 L1 筛选 → 达标池 → 创建挖掘 run。异常写进作业状态。"""
    from app.services.mining_schedule import build_data_fingerprint

    last_bucket = [-1]

    def _on_progress(event: dict[str, Any]) -> None:
        done = int(event.get("done") or 0)
        total = int(event.get("total") or 0)
        patch: dict[str, Any] = {"status": "running", "done": done, "total": total}
        bucket = done // _LABEL_BUCKET
        if bucket != last_bucket[0] or done >= total:
            last_bucket[0] = bucket
            patch["label"] = str(event.get("label") or f"正在筛选因子（{done}/{total}）")
        store.update(screening_id, **patch)

    store.update(screening_id, status="running", label="正在全量筛选因子（约 1-3 分钟）")
    try:
        task = make_worker_task(
            "screening",
            data_dir,
            {
                "asset_type": payload.asset_type,
                "start": payload.start.isoformat() if payload.start else None,
                "end": screen_end.isoformat(),
                "profile": payload.budget_profile,
            },
        )
        result = run_worker_task(task, progress_cb=_on_progress)
    except Exception as exc:  # noqa: BLE001
        store.update(screening_id, status="failed", error=str(exc), label="因子筛选失败")
        return

    screening = result.get("screening") or {}
    if not screening.get("pool"):
        store.update(
            screening_id,
            status="succeeded",
            started=False,
            reason="no_qualified_factors",
            screening=screening,
            label="没有达标因子",
        )
        return

    worker_request = {
        "factor_names": screening["pool"],
        "strategy_ids": [],
        "symbols": payload.symbols,
        "asset_type": payload.asset_type,
        "start": payload.start.isoformat() if payload.start else None,
        # 与筛选同口径 (前一交易日), 任务区间的结束日不能晚于实际读取的数据
        "end": screen_end.isoformat(),
        "budget_profile": payload.budget_profile,
        "commission_pct": payload.commission_pct,
        "stamp_tax_pct": payload.stamp_tax_pct,
        "slippage_bps": payload.slippage_bps,
        "correlation_threshold": payload.correlation_threshold,
        "max_combination_factors": 4,
        "beam_width": 12,
        "max_finalists": MAX_FINALISTS,
        "auto": True,
        "auto_screening": screening,
    }
    try:
        fingerprint = build_data_fingerprint(repo, app_state, worker_request)
        manifest = manager.start(
            worker_request,
            fingerprint,
            force=payload.force,
            source="auto",
        )
    except Exception as exc:  # noqa: BLE001
        store.update(screening_id, status="failed", error=str(exc), label="创建挖掘任务失败")
        return
    store.update(
        screening_id,
        status="succeeded",
        started=True,
        run_id=str(manifest.get("run_id")),
        screening=screening,
        label="筛选完成，挖掘任务已启动",
    )
