"""인증/인가 ASGI 미들웨어 — 모든 요청(http+websocket)을 게이트.

라우트 본문을 건드리지 않고(추가형) 한 곳에서 경로/메서드 기반 정책을 강제한다.
enabled=false면 완전 무동작(통과). 통과 시 요청 범위 contextvar에 principal을 심어
ActionBus 감사로그에 사람별 신원이 박히게 한다(audit.actor_label).
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from .audit import reset_principal, set_principal
from .auth import AccessPolicy
from .roles import required_access


class AccessMiddleware:
    def __init__(self, app, policy: AccessPolicy):
        self.app = app
        self.policy = policy

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket") or not self.policy.enabled:
            await self.app(scope, receive, send)
            return

        required = required_access(
            scope.get("path", ""), scope.get("method", "GET"), scope["type"])
        if required is None:                       # 공개 경로
            await self.app(scope, receive, send)
            return

        principal = self.policy.principal_from_headers(Headers(scope=scope))
        if not self.policy.authorize(principal, required):
            await self._deny(scope, receive, send, principal)
            return

        token = set_principal(principal)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_principal(token)

    async def _deny(self, scope, receive, send, principal) -> None:
        code = 401 if principal.kind == "anon" else 403
        if scope["type"] == "websocket":
            try:
                await receive()                    # connect 소비 후 정책 위반 코드로 종료
            except Exception:
                pass
            await send({"type": "websocket.close", "code": 1008})
            return
        msg = "인증이 필요합니다" if code == 401 else "권한이 부족합니다"
        await JSONResponse({"detail": msg}, status_code=code)(scope, receive, send)
