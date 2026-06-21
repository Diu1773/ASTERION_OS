"""Prometheus /metrics 회귀 — 렌더 정확성 + 접근 게이팅.

실행: 프로젝트 루트에서  python -m unittest tests.test_metrics
"""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from asterion.access.auth import AccessPolicy, build_auth_router, hash_password, hash_token
from asterion.access.middleware import AccessMiddleware
from asterion.watchtower.metrics import render_metrics

SNAP = {
    "mode": "sim",
    "safety": {"state": "OBSERVING"},
    "capture": {"active": True},
    "orchestrator": {"running": False},
    "telemetry_last": {
        "mount.alt": 45.2, "mount.az": 180.0,
        "focuser.temp": None,          # None → 제외
        "weather.rain": True,          # bool → 0/1
        "label": "ignore-me",          # 비수치 → 제외
    },
}


class TestRender(unittest.TestCase):
    def test_empty_snapshot_up_zero(self):
        text = render_metrics(None)
        self.assertIn("asterion_up 0", text)

    def test_gauges_and_labels(self):
        text = render_metrics(SNAP, {"asterion_uptime_seconds": 12})
        lines = text.splitlines()
        self.assertIn("asterion_up 1", lines)
        self.assertIn("asterion_mount_alt 45.2", lines)
        self.assertIn("asterion_mount_az 180.0", lines)
        self.assertIn('asterion_safety_state{state="OBSERVING"} 1', lines)
        self.assertIn('asterion_mode_info{mode="sim"} 1', lines)
        self.assertIn("asterion_capture_active 1", lines)
        self.assertIn("asterion_orchestrator_running 0", lines)
        self.assertIn("asterion_weather_rain 1", lines)   # bool → 1
        self.assertIn("asterion_uptime_seconds 12", lines)

    def test_none_and_nonnumeric_excluded(self):
        text = render_metrics(SNAP)
        self.assertNotIn("focuser_temp", text)   # None
        self.assertNotIn("ignore-me", text)      # 문자열

    def test_type_declared_once(self):
        text = render_metrics(SNAP)
        self.assertEqual(text.count("# TYPE asterion_up gauge"), 1)


def _policy() -> AccessPolicy:
    class DictCfg:
        def __init__(self, d): self.d = d
        def get(self, k, default=None):
            node = self.d
            for p in k.split("."):
                if not isinstance(node, dict) or p not in node:
                    return default
                node = node[p]
            return node
        def set(self, k, v): pass
    return AccessPolicy(DictCfg({"server": {"auth": {
        "enabled": True,
        "session_secret": "s",
        "users": {"viv": {"role": "viewer", "password": hash_password("vpw")}},
        "tokens": {
            "prom": {"scopes": ["metrics"], "hash": hash_token("METRICSTOK")},
            "wx": {"scopes": ["ingest"], "hash": hash_token("INGESTTOK")},
        },
    }}}))


class TestMetricsGate(unittest.TestCase):
    def setUp(self):
        policy = _policy()
        app = FastAPI()

        @app.get("/metrics")
        async def metrics():
            return Response(render_metrics(SNAP), media_type="text/plain")

        app.include_router(build_auth_router(policy))
        app.add_middleware(AccessMiddleware, policy=policy)
        self.app = app

    def test_anon_denied(self):
        self.assertEqual(TestClient(self.app).get("/metrics").status_code, 401)

    def test_metrics_token_allowed(self):
        c = TestClient(self.app)
        r = c.get("/metrics", headers={"Authorization": "Bearer METRICSTOK"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("asterion_up 1", r.text)

    def test_wrong_scope_token_denied(self):
        c = TestClient(self.app)
        r = c.get("/metrics", headers={"Authorization": "Bearer INGESTTOK"})
        self.assertEqual(r.status_code, 403)

    def test_logged_in_user_allowed(self):
        c = TestClient(self.app)
        c.post("/login", json={"username": "viv", "password": "vpw"})
        self.assertEqual(c.get("/metrics").status_code, 200)


if __name__ == "__main__":
    unittest.main()
