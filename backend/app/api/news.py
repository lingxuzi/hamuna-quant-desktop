"""要闻与盘前简报 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.overview import peek_overview
from app.services import news as news_service
from app.services import premarket

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("/fast")
def news_fast(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    symbols: str | None = Query(None, description="逗号分隔的标的代码, 只返回命中这些标的的条目"),
):
    """最近要闻。symbols 非空时只返回命中这些标的的条目。"""
    repo = request.app.state.repo
    codes = [s.strip() for s in (symbols or "").split(",") if s.strip()] or None
    rows = news_service.cached_with_names(repo.store.data_dir, limit, codes, repo)
    return {"items": rows, "total": len(rows)}


@router.get("/premarket")
def news_premarket(request: Request):
    """盘前简报: 隔夜外围 + 跨资产 + 外部风险温度 + 事件传导链。"""
    data_dir = request.app.state.repo.store.data_dir
    try:
        overview = peek_overview(request)
    except Exception:  # noqa: BLE001
        overview = {}
    rows = news_service.list_recent(data_dir, 40)
    payload = premarket.premarket_payload(overview, rows)
    if not payload.get("markets") and not payload.get("cross_assets"):
        raise HTTPException(status_code=503, detail="盘前数据暂不可用, 请稍后重试。")
    return payload
