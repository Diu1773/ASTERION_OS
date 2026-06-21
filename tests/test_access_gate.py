"""원격 접속 인증/인가 게이트 회귀 (REMOTE_ACCESS_PLAN Phase A).

전체 create_app(하드웨어 sim·lifespan)을 띄우지 않고, 동일 미들웨어·정책을 미니 FastAPI에
배선해 검증한다 — 빠르고 hermetic. 검증: enabled=false 통과(하위호환) / 무인증 거부 / 역할
차등(viewer·operator·admin) / 기계 토큰 scope / 세션 위조 거부 / 감사 신원 라벨.

실행: 프로젝트 루트에서  python -m unittest tests.test_access_gate
"""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from asterion.access.audit import (
    Principal, actor_label, current_principal, reset_principal, set_principal,
)
from asterion.access.auth import (
    AccessPolicy, build_auth_router, hash_password, hash_token, make_session,
    read_session,
)
from asterion.access.middleware import AccessMiddleware


class DictCfg:
    """get/set 둘 다 지원하는 dotted-key cfg 스텁 (config.local.json 흉내)."""

    def __init__(self, data: dict):
        self.data = data

    def get(self, dotted, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted, value):
        node = self.data
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = value


def _make_app(policy: AccessPolicy) -> FastAPI:
    app = FastAPI()

    @app.get("/api/status")
    async def status():
        return {"ok": True}

    @app.post("/api/actions/mount/stop")
    async def action():
        return {"ok": True}

    @app.post("/api/system/connect")
    async def system():
        return {"ok": True}

    @app.post("/api/weather/ingest")
    async def ingest():
        return {"ok": True}

    @app.get("/")
    async def root():
        return {"shell": True}

    app.include_router(build_auth_router(policy))
    app.add_middleware(AccessMiddleware, policy=policy)
    return app


def _enabled_cfg() -> DictCfg:
    return DictCfg({"server": {"auth": {
        "enabled": True,
        "session_secret": "test-secret-0123456789",
        "users": {
            "ann":  {"role": "admin",    "password": hash_password("apw")},
            "ops":  {"role": "operator", "password": hash_password("opw")},
            "viv":  {"role": "viewer",   "password": hash_password("vpw")},
        },
        "tokens": {
            "wx01": {"scopes": ["ingest"], "hash": hash_token("WXSECRET")},
        },
    }}})


def _login(client: TestClient, user: str, pw: str) -> TestClient:
    r = client.post("/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return client


class TestAuthDisabled(unittest.TestCase):
    """enabled=false → 게이트 완전 무동작(기존 동작 보존)."""

    def setUp(self):
        self.client = TestClient(_make_app(AccessPolicy(DictCfg({}))))

    def test_everything_passes(self):
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        self.assertEqual(self.client.post("/api/actions/mount/stop").status_code, 200)
        self.assertEqual(self.client.post("/api/system/connect").status_code, 200)

    def test_session_me_reports_disabled(self):
        body = self.client.get("/api/session/me").json()
        self.assertTrue(body["authenticated"])
        self.assertEqual(body.get("auth"), "disabled")


class TestAuthEnabled(unittest.TestCase):
    def setUp(self):
        self.policy = AccessPolicy(_enabled_cfg())
        self.app = _make_app(self.policy)

    def _client(self):
        return TestClient(self.app)

    def test_public_paths_open(self):
        c = self._client()
        self.assertEqual(c.get("/").status_code, 200)
        self.assertFalse(c.get("/api/session/me").json()["authenticated"])

    def test_unauthenticated_denied(self):
        c = self._client()
        self.assertEqual(c.get("/api/status").status_code, 401)
        self.assertEqual(c.post("/api/actions/mount/stop").status_code, 401)

    def test_viewer_reads_not_writes(self):
        c = _login(self._client(), "viv", "vpw")
        self.assertEqual(c.get("/api/status").status_code, 200)
        self.assertEqual(c.post("/api/actions/mount/stop").status_code, 403)
        self.assertEqual(c.post("/api/system/connect").status_code, 403)

    def test_operator_commands_not_system(self):
        c = _login(self._client(), "ops", "opw")
        self.assertEqual(c.get("/api/status").status_code, 200)
        self.assertEqual(c.post("/api/actions/mount/stop").status_code, 200)
        self.assertEqual(c.post("/api/system/connect").status_code, 403)

    def test_admin_all(self):
        c = _login(self._client(), "ann", "apw")
        self.assertEqual(c.post("/api/actions/mount/stop").status_code, 200)
        self.assertEqual(c.post("/api/system/connect").status_code, 200)

    def test_bad_password(self):
        c = self._client()
        self.assertEqual(
            c.post("/login", json={"username": "ann", "password": "nope"}).status_code,
            401)

    def test_machine_token_ingest_scope(self):
        c = self._client()
        hdr = {"Authorization": "Bearer WXSECRET"}
        self.assertEqual(c.post("/api/weather/ingest", headers=hdr).status_code, 200)
        # 토큰은 역할 엔드포인트(operator) 접근 불가
        self.assertEqual(
            c.post("/api/actions/mount/stop", headers=hdr).status_code, 403)
        # 잘못된 토큰 → 거부
        self.assertEqual(
            c.post("/api/weather/ingest",
                   headers={"Authorization": "Bearer WRONG"}).status_code, 401)

    def test_logout_clears(self):
        c = _login(self._client(), "ann", "apw")
        self.assertEqual(c.get("/api/status").status_code, 200)
        c.post("/logout")
        self.assertEqual(c.get("/api/status").status_code, 401)


class TestSessionSigning(unittest.TestCase):
    def test_roundtrip(self):
        tok = make_session("s3cr3t", "ann", "admin", 3600)
        self.assertEqual(read_session("s3cr3t", tok), ("ann", "admin"))

    def test_tamper_rejected(self):
        tok = make_session("s3cr3t", "ann", "admin", 3600)
        body, _sig = tok.split(".", 1)
        forged = body + "." + "0" * 64
        self.assertIsNone(read_session("s3cr3t", forged))

    def test_wrong_secret_rejected(self):
        tok = make_session("s3cr3t", "ann", "admin", 3600)
        self.assertIsNone(read_session("other", tok))

    def test_expired_rejected(self):
        tok = make_session("s3cr3t", "ann", "admin", -1)
        self.assertIsNone(read_session("s3cr3t", tok))


def _ts_cfg(trust: bool) -> DictCfg:
    return DictCfg({"server": {"auth": {
        "enabled": True,
        "session_secret": "test-secret-ts",
        "trust_tailscale_identity": trust,
        "tailscale_users": {"alice@github": "admin", "bob@gmail.com": "operator"},
        "users": {}, "tokens": {},
    }}})


class TestTailscaleIdentity(unittest.TestCase):
    """Phase B — serve 신원헤더로 비번 없이 로그인(켜졌을 때만, 매핑된 사용자만)."""

    def _app(self, trust: bool):
        return _make_app(AccessPolicy(_ts_cfg(trust)))

    def test_mapped_identity_authorized(self):
        c = TestClient(self._app(True))
        h = {"Tailscale-User-Login": "alice@github"}   # admin 매핑
        self.assertEqual(c.get("/api/status", headers=h).status_code, 200)
        self.assertEqual(c.post("/api/system/connect", headers=h).status_code, 200)

    def test_operator_identity_not_admin(self):
        c = TestClient(self._app(True))
        h = {"Tailscale-User-Login": "bob@gmail.com"}   # operator 매핑
        self.assertEqual(c.post("/api/actions/mount/stop", headers=h).status_code, 200)
        self.assertEqual(c.post("/api/system/connect", headers=h).status_code, 403)

    def test_unmapped_identity_denied(self):
        c = TestClient(self._app(True))
        h = {"Tailscale-User-Login": "evil@nope"}
        self.assertEqual(c.get("/api/status", headers=h).status_code, 401)

    def test_header_ignored_when_trust_off(self):
        # 플래그 off면 헤더를 절대 신뢰하지 않음(직접 노출 시 위조 차단)
        c = TestClient(self._app(False))
        h = {"Tailscale-User-Login": "alice@github"}
        self.assertEqual(c.get("/api/status", headers=h).status_code, 401)


class TestAuditLabel(unittest.TestCase):
    def test_label_with_principal(self):
        t = set_principal(Principal(name="alice", role="operator", kind="user"))
        try:
            self.assertEqual(actor_label("operator"), "operator(alice)")
        finally:
            reset_principal(t)

    def test_label_without_principal(self):
        # principal 없음(인증 꺼짐/로컬) → base 그대로
        self.assertIsNone(current_principal())
        self.assertEqual(actor_label("operator"), "operator")


if __name__ == "__main__":
    unittest.main()
