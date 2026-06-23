"""레이트리밋 회귀 (REMOTE_ACCESS_PLAN Phase D).

핵심: /login·/api/agent/chat 만 윈도 한도로 제한(초과 429), 나머지 경로 무제한, IP별 독립,
disabled면 무동작.

실행: 프로젝트 루트에서  python -m unittest tests.test_ratelimit
"""

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from asterion.access.ratelimit import RateLimiter, RateLimitMiddleware


class Cfg:
    def __init__(self, **kw):
        self.kw = kw

    def get(self, k, d=None):
        return self.kw.get(k, d)


def _limiter(enabled=True, login_max=2):
    return RateLimiter(Cfg(**{
        "server.ratelimit.enabled": enabled,
        "server.ratelimit.login_max": login_max,
        "server.ratelimit.login_window_s": 60.0,
        "server.ratelimit.agent_max": 2,
        "server.ratelimit.agent_window_s": 60.0,
    }))


class TestRateLimiterUnit(unittest.TestCase):
    def test_cap_then_block(self):
        rl = _limiter(login_max=2)
        self.assertTrue(rl.check("/login", "1.2.3.4"))
        self.assertTrue(rl.check("/login", "1.2.3.4"))
        self.assertFalse(rl.check("/login", "1.2.3.4"))   # 3번째 초과

    def test_per_ip_independent(self):
        rl = _limiter(login_max=1)
        self.assertTrue(rl.check("/login", "a"))
        self.assertFalse(rl.check("/login", "a"))
        self.assertTrue(rl.check("/login", "b"))          # 다른 IP는 별도 카운트

    def test_unlisted_path_unlimited(self):
        rl = _limiter(login_max=1)
        for _ in range(50):
            self.assertTrue(rl.check("/api/status", "a"))

    def test_disabled_via_middleware(self):
        # disabled면 미들웨어가 통과(여기선 check도 호출 안 됨)
        rl = _limiter(enabled=False)
        self.assertFalse(rl.enabled)


class TestRateLimitMiddleware(unittest.TestCase):
    def _client(self, **kw):
        app = FastAPI()

        @app.post("/login")
        async def login():
            return {"ok": True}

        @app.post("/api/agent/chat")
        async def chat():
            return {"ok": True}

        @app.get("/api/status")
        async def status():
            return {"ok": True}

        app.add_middleware(RateLimitMiddleware, limiter=_limiter(**kw))
        return TestClient(app)

    def test_login_throttled(self):
        c = self._client(login_max=2)
        self.assertEqual(c.post("/login").status_code, 200)
        self.assertEqual(c.post("/login").status_code, 200)
        self.assertEqual(c.post("/login").status_code, 429)   # 초과

    def test_status_never_throttled(self):
        c = self._client(login_max=1)
        for _ in range(10):
            self.assertEqual(c.get("/api/status").status_code, 200)

    def test_agent_chat_throttled(self):
        c = self._client()
        self.assertEqual(c.post("/api/agent/chat").status_code, 200)
        self.assertEqual(c.post("/api/agent/chat").status_code, 200)
        self.assertEqual(c.post("/api/agent/chat").status_code, 429)

    def test_disabled_no_throttle(self):
        c = self._client(enabled=False, login_max=1)
        for _ in range(5):
            self.assertEqual(c.post("/login").status_code, 200)


if __name__ == "__main__":
    unittest.main()
