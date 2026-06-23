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

    @app.get("/api/system/devices")
    async def system_devices():
        return {"devices": []}

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

    def test_system_get_requires_admin(self):
        # rank6 — GET /api/system/* 도 admin(상태조회도 민감). viewer/operator 거부, admin 허용.
        self.assertEqual(
            _login(self._client(), "viv", "vpw").get("/api/system/devices").status_code, 403)
        self.assertEqual(
            _login(self._client(), "ops", "opw").get("/api/system/devices").status_code, 403)
        self.assertEqual(
            _login(self._client(), "ann", "apw").get("/api/system/devices").status_code, 200)

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
    """Phase B — serve 신원헤더로 비번 없이 로그인(켜졌을 때만, 매핑된 사용자만, 그리고
    신뢰 프록시=루프백에서 온 요청만 — rank4 헤더 위조 차단)."""

    def _client(self, trust: bool, client=("127.0.0.1", 50000)):
        # client=신뢰 프록시(루프백) 기본 — serve 뒤 정상 경로 모사.
        return TestClient(_make_app(AccessPolicy(_ts_cfg(trust))), client=client)

    def test_mapped_identity_authorized(self):
        c = self._client(True)
        h = {"Tailscale-User-Login": "alice@github"}   # admin 매핑
        self.assertEqual(c.get("/api/status", headers=h).status_code, 200)
        self.assertEqual(c.post("/api/system/connect", headers=h).status_code, 200)

    def test_operator_identity_not_admin(self):
        c = self._client(True)
        h = {"Tailscale-User-Login": "bob@gmail.com"}   # operator 매핑
        self.assertEqual(c.post("/api/actions/mount/stop", headers=h).status_code, 200)
        self.assertEqual(c.post("/api/system/connect", headers=h).status_code, 403)

    def test_unmapped_identity_denied(self):
        c = self._client(True)
        h = {"Tailscale-User-Login": "evil@nope"}
        self.assertEqual(c.get("/api/status", headers=h).status_code, 401)

    def test_header_ignored_when_trust_off(self):
        # 플래그 off면 헤더를 절대 신뢰하지 않음(직접 노출 시 위조 차단)
        c = self._client(False)
        h = {"Tailscale-User-Login": "alice@github"}
        self.assertEqual(c.get("/api/status", headers=h).status_code, 401)

    def test_spoofed_identity_from_untrusted_ip_denied(self):
        # rank4 — trust 켜졌어도 비신뢰 IP(직접 노출/serve 우회)에서 온 신원헤더는 위조로 보고
        # 거부. 헤더값은 admin 매핑이지만 client IP가 루프백이 아니므로 무비밀번호 admin 불가.
        c = self._client(True, client=("203.0.113.7", 51000))   # 공인 IP(비루프백)
        h = {"Tailscale-User-Login": "alice@github"}
        self.assertEqual(c.get("/api/status", headers=h).status_code, 401)
        self.assertEqual(c.post("/api/system/connect", headers=h).status_code, 401)


class TestLoginFailureAlert(unittest.TestCase):
    """Phase C2 — 로그인 실패가 임계를 넘으면 보안 이벤트(브루트포스 탐지)."""

    def _app(self):
        self.events = []
        cfg = DictCfg({"server": {"auth": {
            "enabled": True, "session_secret": "s",
            "login_fail_threshold": 3, "login_fail_window_s": 300,
            "users": {"ann": {"role": "admin", "password": hash_password("apw")}},
            "tokens": {},
        }}})
        policy = AccessPolicy(cfg)
        app = FastAPI()
        app.include_router(build_auth_router(
            policy, on_security_event=lambda t, d: self.events.append((t, d))))
        app.add_middleware(AccessMiddleware, policy=policy)
        return TestClient(app)

    def test_threshold_fires_and_resets(self):
        c = self._app()
        for _ in range(2):
            c.post("/login", json={"username": "ann", "password": "WRONG"})
        self.assertEqual(self.events, [])              # 임계(3) 미만 → 무발화
        c.post("/login", json={"username": "ann", "password": "WRONG"})   # 3회째
        self.assertEqual(len(self.events), 1)          # 임계 도달 → 발화
        self.assertIn("로그인 실패", self.events[0][0])
        c.post("/login", json={"username": "ann", "password": "apw"})     # 성공 → 리셋
        self.events.clear()
        c.post("/login", json={"username": "ann", "password": "WRONG"})
        self.assertEqual(self.events, [])              # 리셋되어 다시 1회부터


class TestSecretFilePerms(unittest.TestCase):
    """rank11 — 자동 생성 세션 시크릿을 쓴 config.local.json을 소유자 전용(0o600)으로 제한."""

    def test_auto_secret_restricts_overlay_perms(self):
        import os
        import stat
        import tempfile
        from pathlib import Path
        f = Path(tempfile.mkdtemp()) / "config.local.json"
        f.write_text("{}")
        cfg = DictCfg({"server": {"auth": {"enabled": True}}})
        cfg.overlay_path = f                      # 실제 Config.overlay_path 흉내
        policy = AccessPolicy(cfg)                # __init__ → _ensure_secret → 생성+권한제한
        self.assertTrue(policy.secret)            # 시크릿 생성됨
        self.assertTrue(cfg.get("server.auth.session_secret"))
        if os.name == "posix":                    # Windows는 chmod가 ACL을 제한 못 함(best-effort)
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)


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
