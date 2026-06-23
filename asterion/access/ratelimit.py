"""레이트리밋 — 브루트포스(/login)·비용(/api/agent/chat) 보호 (REMOTE_ACCESS_PLAN Phase D).

인증과 무관하게 항상 동작한다(공개 /login도 막아야 하니까). **지정 경로만** 슬라이딩 윈도로
제한하고 나머지는 무제한 — 대시보드 폴링(/api/status 등)엔 영향이 없다. 인메모리(단일 인스턴스).
초과 시 429. config로 끈다([server.ratelimit].enabled). 클라이언트 IP는 proxy-headers로 복원된
scope['client'] 사용(Tailscale serve/프록시 뒤에서도 실제 IP별 카운트).
"""

from __future__ import annotations

import time

from starlette.responses import JSONResponse


class RateLimiter:
    def __init__(self, cfg):
        self.enabled = bool(cfg.get("server.ratelimit.enabled", True))
        # path → (최대요청, 윈도초). 여기 없는 경로는 제한 안 함.
        self.rules: dict[str, tuple[int, float]] = {
            "/login": (int(cfg.get("server.ratelimit.login_max", 10)),
                       float(cfg.get("server.ratelimit.login_window_s", 60.0))),
            "/api/agent/chat": (int(cfg.get("server.ratelimit.agent_max", 30)),
                                float(cfg.get("server.ratelimit.agent_window_s", 60.0))),
        }
        self._hits: dict[tuple[str, str], list[float]] = {}

    def check(self, path: str, ip: str) -> bool:
        """허용이면 True(히트 기록), 윈도 내 한도 초과면 False."""
        rule = self.rules.get(path)
        if rule is None:
            return True
        cap, window = rule
        now = time.monotonic()
        hits = self._hits.setdefault((path, ip), [])
        cutoff = now - window
        hits[:] = [t for t in hits if t >= cutoff]
        if len(hits) >= cap:
            return False
        hits.append(now)
        return True


class RateLimitMiddleware:
    def __init__(self, app, limiter: RateLimiter):
        self.app = app
        self.limiter = limiter

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or not self.limiter.enabled
                or scope.get("path", "") not in self.limiter.rules):
            await self.app(scope, receive, send)
            return
        client = scope.get("client") or ("?", 0)
        ip = client[0] if client else "?"
        if not self.limiter.check(scope["path"], ip):
            await JSONResponse(
                {"detail": "요청이 너무 많습니다 — 잠시 후 다시 시도하세요"},
                status_code=429)(scope, receive, send)
            return
        await self.app(scope, receive, send)
