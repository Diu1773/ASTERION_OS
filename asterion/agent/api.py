"""에이전트 REST — 대시보드 채팅 위젯이 호출. app.py는 include 한 줄."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel


class ChatReq(BaseModel):
    message: str
    history: list[dict] | None = None   # [{role, content}, ...] 최근 대화(클라이언트 보관)


def build_agent_router(agent: Any) -> APIRouter:
    router = APIRouter(tags=["agent"])

    @router.get("/api/agent/status")
    async def agent_status():
        return {"configured": agent.configured,
                "model": getattr(agent.llm, "model", "") or None}

    @router.post("/api/agent/chat")
    async def agent_chat(req: ChatReq):
        return await agent.run(req.message, req.history)

    return router
