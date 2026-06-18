"""에이전트 REST — 대시보드 채팅 위젯이 호출. app.py는 include 한 줄."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from .llm import LLMError


class ChatReq(BaseModel):
    message: str
    history: list[dict] | None = None   # [{role, content}, ...] 최근 대화(클라이언트 보관)


class ModelReq(BaseModel):
    model: str


def build_agent_router(agent: Any, cfg: Any = None) -> APIRouter:
    router = APIRouter(tags=["agent"])

    @router.get("/api/agent/status")
    async def agent_status():
        return {"configured": agent.configured,
                "model": getattr(agent.llm, "model", "") or None}

    @router.post("/api/agent/chat")
    async def agent_chat(req: ChatReq):
        return await agent.run(req.message, req.history)

    @router.get("/api/agent/models")
    async def agent_models():
        """provider에서 사용 가능 모델 목록을 가져온다. 실패해도 200 — 현재 모델만 반환."""
        current = getattr(agent.llm, "model", "") or None
        base = [m for m in [current] if m]
        if not agent.configured:
            return {"models": base, "current": current, "error": "모델 미설정"}
        try:
            models = await agent.llm.list_models()
        except LLMError as e:
            return {"models": base, "current": current,
                    "error": f"{e.status}: {e.message}"}
        except Exception as e:  # noqa: BLE001
            return {"models": base, "current": current, "error": str(e)}
        if current and current not in models:
            models = [current, *models]
        return {"models": models, "current": current}

    @router.post("/api/agent/model")
    async def agent_set_model(req: ModelReq):
        """런타임 모델 전환 + config.local.json 영속(다음 부팅에도 유지)."""
        model = (req.model or "").strip()
        if not model:
            return {"ok": False, "error": "빈 모델명"}
        agent.llm.model = model
        if cfg is not None:
            cfg.set("agent.model", model)
        return {"ok": True, "model": model}

    return router
