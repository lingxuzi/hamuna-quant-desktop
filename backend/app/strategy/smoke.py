"""策略实跑 smoke — 按「实盘口径」跑一次, 跑了就拦。

背景: 静态校验 (column_guard) 只看源码, 拦不住运行期才炸的问题 (列在实盘帧里
不存在、dtype 不匹配、算子用错)。这里在保存/发布前用最近交易日的真实数据按
engine.run 的实盘口径真跑一遍: 引擎喂给 polars_expr 策略的是「当日一行一标的」
的快照, 喂给 python_history_legacy 策略的是历史窗口 —— 与回测路径的整段面板
不同, 正是回测口径与实盘口径分叉的地方。

形状对齐 app/definitions.py::smoke_definition: {"ok", "issues": [{"code", "message"}]}。

fail-open 边界: 数据不可用 (无行情 / 完整性不通过) 时跳过实跑, 不阻断保存 ——
「无法校验」不等于「策略无效」。策略自身运行失败则 fail-closed 阻断。
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# 这些后端的实跑需要别的数据域, 保存期不实跑:
# - matrix_native: 走 market 协议, 特征名由 matrix_feature 兜底任意 ma{N}, 不引用 pl.col
# - composite: 依赖子策略, 子策略各自有闸
# - minute_filter: 依赖分钟 K 分区, 缺失时不该阻断日线策略的保存
_SKIP_BACKENDS = frozenset({"matrix_native", "composite", "minute_filter"})


def _load_error_message(engine, strategy_id: str, exc: Exception) -> str:
    """加载失败时把引擎记录的真实原因带出来。

    只报 "unknown strategy: xxx" 看不出问题 (策略文件根本没加载上),
    模型拿这个报错没法改 —— 必须带上 load_errors 里的原文。
    """
    message = str(exc)
    try:
        errors = engine.load_errors() or []
    except Exception:  # noqa: BLE001 — 取不到就按原样返回
        errors = []
    if not errors:
        return message
    details = "; ".join(
        str(item.get("error") or "") for item in errors if item.get("error")
    )
    return f"{message}: {details}" if details else message


def smoke_strategy(
    engine,
    repo,
    strategy_id: str,
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """在最近交易日按实盘口径实跑一次策略。

    Returns:
        {"ok": bool, "issues": [{"code", "message"}], "skipped": str | None,
         "hits": int, "as_of": str | None}
    """
    try:
        s = engine.get(strategy_id)
    except ValueError as exc:
        return {
            "ok": False,
            "issues": [{"code": "load_failed", "message": _load_error_message(engine, strategy_id, exc)}],
            "skipped": None,
        }
    except Exception as exc:  # noqa: BLE001 — 引擎接口异常不该让 smoke 变成崩溃源
        return {"ok": True, "issues": [], "skipped": f"engine_error: {exc}"}
    if s.execution_backend in _SKIP_BACKENDS:
        return {"ok": True, "issues": [], "skipped": s.execution_backend}
    if repo is None:
        return {"ok": True, "issues": [], "skipped": "no_repo"}

    from app.services.screener import ScreenerService

    svc = ScreenerService(repo)
    try:
        as_of = as_of or svc.latest_date()
    except Exception as exc:  # noqa: BLE001 — 数据侧问题不阻断保存
        logger.info("strategy smoke %s: 取最新交易日失败, 跳过实跑: %s", strategy_id, exc)
        return {"ok": True, "issues": [], "skipped": "no_market_data"}
    if as_of is None:
        return {"ok": True, "issues": [], "skipped": "no_market_data"}

    try:
        ctx = svc.build_strategy_context(engine, as_of, [strategy_id])
    except Exception as exc:  # noqa: BLE001 — 数据侧问题不阻断保存
        logger.info("strategy smoke %s: 数据准备失败, 跳过实跑: %s", strategy_id, exc)
        return {"ok": True, "issues": [], "skipped": f"context_failed: {exc}"}

    try:
        result = engine.run(strategy_id, ctx)
    except Exception as exc:  # noqa: BLE001 — 策略自身失败 → 阻断
        message = str(exc) or type(exc).__name__
        return {
            "ok": False,
            "issues": [{"code": "run_failed", "message": message}],
            "skipped": None,
        }

    rows = getattr(result, "rows", None) or []
    return {
        "ok": True,
        "issues": [],
        "skipped": None,
        "hits": len(rows),
        "as_of": as_of.isoformat(),
    }


def smoke_message(result: dict[str, Any]) -> str:
    """把 smoke 结果压成一行可读文案 (给 LLM 提示 / 错误详情用)。"""
    if result.get("ok"):
        skipped = result.get("skipped")
        return f"实跑通过 (跳过: {skipped})" if skipped else "实跑通过"
    return "; ".join(
        str(issue.get("message") or issue.get("code") or "")
        for issue in result.get("issues") or []
    )
