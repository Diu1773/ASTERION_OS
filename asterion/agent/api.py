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


class ProviderReq(BaseModel):
    name: str


def build_agent_router(agent: Any, cfg: Any = None) -> APIRouter:
    router = APIRouter(tags=["agent"])
    hub = agent.llm   # ProviderHub (또는 LLM 호환 객체)

    @router.get("/api/agent/status")
    async def agent_status():
        return {"configured": agent.configured,
                "model": getattr(hub, "model", "") or None,
                "provider": getattr(hub, "active", None)}

    @router.post("/api/agent/chat")
    async def agent_chat(req: ChatReq):
        return await agent.run(req.message, req.history)

    @router.get("/api/agent/providers")
    async def agent_providers():
        """등록된 공급자 메타(키 값은 미노출) + 현재 active."""
        provs = hub.list_providers() if hasattr(hub, "list_providers") else []
        return {"providers": provs, "active": getattr(hub, "active", None)}

    @router.post("/api/agent/provider")
    async def agent_set_provider(req: ProviderReq):
        """active 공급자 전환 + 영속. 그 공급자의 model이 함께 활성화."""
        if not hasattr(hub, "set_provider") or not hub.set_provider(req.name):
            return {"ok": False, "error": f"알 수 없는 공급자: {req.name}"}
        return {"ok": True, "provider": req.name, "model": getattr(hub, "model", ""),
                "configured": agent.configured}

    @router.get("/api/agent/models")
    async def agent_models():
        """active 공급자에서 사용 가능 모델 목록. 실패해도 200 — 현재 모델만 반환."""
        current = getattr(hub, "model", "") or None
        base = [m for m in [current] if m]
        if not agent.configured:
            return {"models": base, "current": current, "error": "공급자 미설정"}
        try:
            models = await hub.list_models()
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
        """active 공급자의 모델 전환 + config.local.json 영속(다음 부팅에도 유지)."""
        model = (req.model or "").strip()
        if not model:
            return {"ok": False, "error": "빈 모델명"}
        if hasattr(hub, "set_model"):
            hub.set_model(model)
        else:                          # LLM 단일 객체 호환
            hub.model = model
            if cfg is not None:
                cfg.set("agent.model", model)
        return {"ok": True, "model": model}

    return router
