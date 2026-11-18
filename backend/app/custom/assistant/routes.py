"""AI 助手 HTTP 端点 — 薄层: 参数校验 + 流式响应映射。

POST /api/custom/assistant/chat      NDJSON 事件流(协议见模块 __init__)
GET  /api/custom/assistant/status     供应商配置状态(前端门控展示)
GET  /api/custom/assistant/suggests   空会话快捷指令
GET  /api/custom/assistant/sessions   会话历史(服务端, 跨设备)
PUT  /api/custom/assistant/sessions   会话历史整体保存(前端防抖写入)
POST /api/custom/assistant/actions/{call_id}/decision
                                     动作工具确认/拒绝(确认卡按钮回调)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.custom.assistant import actions as assistant_actions
from app.custom.assistant import experts as assistant_experts
from app.custom.assistant import sessions as assistant_sessions
from app.custom.assistant import tools as assistant_tools
from app.custom.assistant.chat_service import chat_stream
from app.market_time import cn_today
from app.services.ai_provider import (
    ai_configured,
    current_ai_model,
    current_ai_provider,
)
from app.services.ndjson_heartbeat import with_heartbeat


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)
    reasoning_content: str | None = None
    reasoning_context: str | None = Field(default=None, max_length=64)


class AssistantContext(BaseModel):
    page: str = Field(default="", max_length=100)
    symbol: str = Field(default="", max_length=32)


class ChatRequest(BaseModel):
    messages: list[HistoryMessage] = Field(min_length=1, max_length=200)
    context: AssistantContext | None = None
    # 本轮 @ 引用的专家名 —— 正文直接进 system prompt (后端再校验启用状态)。
    experts: list[str] = Field(default_factory=list, max_length=assistant_experts.MAX_REFERENCED_EXPERTS)


class ActionDecisionRequest(BaseModel):
    approve: bool


class AssistantSessionsPayload(BaseModel):
    """前端 store.ts 的持久化载荷: 会话数组 + 活跃会话 id。

    sessions 用宽松的 list[dict] (消息形状由前端定义, 后端只裁剪条数),
    上限与前端一致, 防御异常客户端把文件写爆。字段名对齐前端 (activeId)。
    """

    sessions: list[dict] = Field(max_length=assistant_sessions.MAX_SESSIONS)
    active_id: str = Field(default="", max_length=64, alias="activeId")

    model_config = {"populate_by_name": True}


class ExpertLayoutPayload(BaseModel):
    """分类体系 + 专家的分类归属/启用状态 (整份保存)。"""

    categories: list[str] = Field(default_factory=list, max_length=assistant_experts.MAX_CATEGORIES)
    assignments: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ExpertCreatePayload(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=assistant_experts.MAX_CONTENT_CHARS)
    category: str = Field(default="", max_length=assistant_experts.MAX_CATEGORY_LEN)


class ExpertUpdatePayload(BaseModel):
    """改专家正文 —— 分类与启用状态走 layout 接口。"""

    description: str | None = Field(default=None, max_length=500)
    content: str | None = Field(default=None, max_length=assistant_experts.MAX_CONTENT_CHARS)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api/custom/assistant", tags=["custom-assistant"])

    @router.post("/chat")
    async def chat(request: Request, req: ChatRequest):
        engine = getattr(request.app.state, "strategy_engine", None)
        repo = getattr(request.app.state, "repo", None)
        data_dir = str(repo.store.data_dir) if repo is not None else None
        context = req.context.model_dump() if req.context else None

        async def gen():
            async for line in with_heartbeat(chat_stream(
                history=[m.model_dump(exclude_none=True) for m in req.messages],
                context=context,
                engine=engine,
                data_dir=data_dir,
                repo=repo,
                quote_service=getattr(request.app.state, "quote_service", None),
                depth_service=getattr(request.app.state, "depth_service", None),
                capabilities=getattr(request.app.state, "capabilities", None),
                financial_scheduler=getattr(request.app.state, "financial_scheduler", None),
                experts=req.experts,
            )):
                yield line + "\n"

        return StreamingResponse(
            gen(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/status")
    def status() -> dict:
        configured = ai_configured()
        return {
            "configured": configured,
            "provider": current_ai_provider(),
            "model": current_ai_model(),
            "supports_tools": configured,
        }

    @router.get("/suggests")
    def suggests() -> dict:
        return {"suggests": assistant_tools.QUICK_SUGGESTS}

    @router.get("/sessions")
    def sessions(request: Request) -> dict:
        repo = getattr(request.app.state, "repo", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="数据目录不可用")
        return assistant_sessions.load_sessions(Path(repo.store.data_dir))

    @router.put("/sessions")
    def put_sessions(request: Request, req: AssistantSessionsPayload) -> dict:
        repo = getattr(request.app.state, "repo", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="数据目录不可用")
        n = assistant_sessions.save_sessions(
            Path(repo.store.data_dir),
            {"sessions": req.sessions, "activeId": req.active_id},
        )
        return {"ok": True, "sessions": n}

    @router.post("/actions/{call_id}/decision")
    async def action_decision(call_id: str, req: ActionDecisionRequest) -> dict:
        """确认卡回调: 唤醒挂起中的动作工具(批准执行或拒绝)。"""
        action = await assistant_actions.registry.resolve(call_id, req.approve)
        if action is None:
            raise HTTPException(status_code=404, detail="确认请求不存在或已过期")
        return {"ok": True, "call_id": call_id, "decision": "approved" if req.approve else "denied"}

    # ── 专家 (skill) 管理: 分类体系 + 专家正文 ──

    def _data_dir(request: Request) -> Path:
        repo = getattr(request.app.state, "repo", None)
        if repo is None:
            raise HTTPException(status_code=503, detail="数据目录不可用")
        return Path(repo.store.data_dir)

    @router.get("/experts")
    def list_experts(request: Request) -> dict:
        """专家总览: 内置 + 用户专家, 带分类归属与启用状态。"""
        try:
            return assistant_experts.list_experts(_data_dir(request))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.put("/experts/layout")
    def save_expert_layout(request: Request, req: ExpertLayoutPayload) -> dict:
        """保存分类体系 + 专家的分类归属/启用状态 (整份保存)。

        声明在 /experts/{name} 之前 —— 否则会被那个路由当成 name="layout"。
        """
        try:
            return assistant_experts.save_layout(_data_dir(request), req.categories, req.assignments)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/experts/export")
    def export_experts(request: Request) -> Response:
        """导出专家包 (zip 附件) —— 分类体系 + 自定义专家正文。

        声明在 /experts/{name} 之前 —— 否则会被那个路由当成 name="export"。
        """
        try:
            blob = assistant_experts.export_zip(_data_dir(request))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        filename = f"experts-{cn_today().isoformat()}.zip"
        return Response(
            content=blob,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @router.post("/experts/import")
    async def import_experts(
        request: Request,
        file: UploadFile = File(...),
        overwrite: bool = Form(False),
    ) -> dict:
        """导入专家包 (zip 文件): 合并分类 + 写入专家 (同名按 overwrite 决定跳过还是覆盖)。"""
        raw = await file.read()
        try:
            report = assistant_experts.import_zip(
                _data_dir(request), raw, overwrite=overwrite,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {**assistant_experts.list_experts(_data_dir(request)), "report": report}

    @router.get("/experts/{name}")
    def get_expert(name: str, request: Request) -> dict:
        """单个专家详情 (编辑表单回填: 正文 + 分类 + 启用状态)。"""
        try:
            return assistant_experts.read_expert(_data_dir(request), name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/experts")
    def create_expert(request: Request, req: ExpertCreatePayload) -> dict:
        """新建用户专家 (写 <data_dir>/assistant/skills/<name>/SKILL.md)。"""
        try:
            assistant_experts.create_expert(
                _data_dir(request),
                name=req.name,
                description=req.description,
                content=req.content,
                category=req.category,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return assistant_experts.list_experts(_data_dir(request))

    @router.put("/experts/{name}")
    def update_expert(name: str, request: Request, req: ExpertUpdatePayload) -> dict:
        """改专家正文 (内置专家只读, 只能改分类与启用状态)。"""
        try:
            assistant_experts.update_expert(
                _data_dir(request), name, description=req.description, content=req.content,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return assistant_experts.list_experts(_data_dir(request))

    @router.delete("/experts/{name}")
    def delete_expert(name: str, request: Request) -> dict:
        """删除用户专家 (内置专家只能停用)。"""
        try:
            assistant_experts.delete_expert(_data_dir(request), name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return assistant_experts.list_experts(_data_dir(request))

    return router
