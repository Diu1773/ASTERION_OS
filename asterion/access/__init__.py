"""원격 접속 인증/인가 게이트 (REMOTE_ACCESS_PLAN Phase A).

추가형 — 기존 안전계층·라우트 본문을 건드리지 않는다. 기본 꺼짐(config
server.auth.enabled=false)이라 로컬 SIM 개발 워크플로는 무변경.

여기(__init__)는 **의존성 없는 audit 심볼만** 재노출한다. auth/middleware(starlette·
pydantic 의존)는 app.py가 명시 import — core.actions가 audit를 끌어와도 무거운 의존이
딸려오지 않게 한다.
"""

from __future__ import annotations

from .audit import (
    ANON, Principal, actor_label, current_principal, reset_principal,
    set_principal,
)

__all__ = ["ANON", "Principal", "actor_label", "current_principal",
           "reset_principal", "set_principal"]
