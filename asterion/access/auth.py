"""인증 핵심 — 비밀번호/토큰 해시, 서명 세션 쿠키, AccessPolicy, 로그인 라우터.

stdlib만 사용(추가 의존 없음): pbkdf2(비번)·sha256(토큰)·hmac 서명 세션. 시크릿·사용자·
토큰은 git 제외 config.local.json에 두고 cfg로 읽는다. enabled=false면 정책은 통과만 한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from http.cookies import SimpleCookie

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .audit import ANON, Principal
from .roles import role_satisfies

_PBKDF2_ROUNDS = 200_000


# ---------- 비밀번호 (pbkdf2) ----------

def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(stored: str, pw: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"),
                                 bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# ---------- 토큰 (sha256) ----------

def hash_token(tok: str) -> str:
    return hashlib.sha256(tok.encode("utf-8")).hexdigest()


# ---------- 서명 세션 (hmac, 무상태) ----------

def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_session(secret: str, name: str, role: str | None, ttl_s: int) -> str:
    payload = {"u": name, "r": role, "exp": int(time.time()) + ttl_s}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"),
                   hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_session(secret: str, cookie: str) -> tuple[str, str | None] | None:
    """서명·만료 검증 통과 시 (username, role), 아니면 None."""
    try:
        body, sig = cookie.split(".", 1)
        expect = hmac.new(secret.encode("utf-8"), body.encode("ascii"),
                          hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_b64d(body))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return str(payload["u"]), payload.get("r")
    except (ValueError, KeyError, TypeError):
        return None


def _cookie_value(cookie_header: str, name: str) -> str | None:
    if not cookie_header:
        return None
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:
        return None
    m = jar.get(name)
    return m.value if m else None


# ---------- 정책 ----------

class AccessPolicy:
    """cfg(server.auth.*)에서 사용자/토큰/시크릿을 읽어 인증·인가를 판정."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = bool(cfg.get("server.auth.enabled", False))
        self.cookie_name = str(cfg.get("server.auth.cookie_name", "asterion_session"))
        self.cookie_secure = bool(cfg.get("server.auth.cookie_secure", False))
        self.ttl_s = int(cfg.get("server.auth.session_ttl_hours", 12)) * 3600
        self.users = dict(cfg.get("server.auth.users", {}) or {})
        self.tokens = dict(cfg.get("server.auth.tokens", {}) or {})
        # Tailscale serve 뒤(Phase B): serve가 주입하는 신원 헤더로 비번 없이 로그인.
        # ⚠ serve/신뢰 프록시 뒤에서만 켤 것 — 직접 노출 시 헤더 위조 가능(fail-safe: 기본 off).
        self.trust_ts = bool(cfg.get("server.auth.trust_tailscale_identity", False))
        self.ts_users = dict(cfg.get("server.auth.tailscale_users", {}) or {})
        # 신원 헤더(Tailscale-*)는 *신뢰 프록시(=serve)*에서 온 요청에서만 신뢰한다(rank4). serve는
        # 로컬 루프백에서 프록시하므로 기본 {127.0.0.1, ::1}. 직접 노출(LAN/0.0.0.0/serve 우회)로
        # 앱에 닿은 요청은 client IP가 신뢰목록에 없어 헤더만으로 admin 위조 불가(fail-safe).
        self.trusted_proxy_ips = set(
            cfg.get("server.auth.trusted_proxy_ips", ["127.0.0.1", "::1"]) or [])
        # 로그인 실패 추적(브루트포스 탐지, Phase C2) — 윈도 내 누적 실패 수.
        self.login_fail_threshold = int(cfg.get("server.auth.login_fail_threshold", 5))
        self.login_fail_window_s = float(cfg.get("server.auth.login_fail_window_s", 300.0))
        self._login_fails: dict[str, list[float]] = {}
        self.secret = self._ensure_secret()

    def record_login_failure(self, key: str) -> int:
        """실패 1건 기록 후 윈도 내 누적 실패 수 반환(브루트포스 임계 판정용)."""
        now = time.monotonic()
        win = self._login_fails.setdefault(key, [])
        win.append(now)
        cutoff = now - self.login_fail_window_s
        win[:] = [t for t in win if t >= cutoff]
        return len(win)

    def clear_login_failures(self, key: str) -> None:
        self._login_fails.pop(key, None)

    def _ensure_secret(self) -> str:
        s = str(self.cfg.get("server.auth.session_secret", "") or "")
        if self.enabled and not s:
            # 켜졌는데 시크릿이 없으면 생성·영속(재시작에도 세션 유지). config.local.json에 저장.
            s = secrets.token_hex(32)
            try:
                self.cfg.set("server.auth.session_secret", s)
                self._restrict_secret_file()
            except Exception:
                pass
        return s or "dev-insecure-secret"

    def _restrict_secret_file(self) -> None:
        """시크릿을 쓴 config.local.json을 소유자 전용(0o600)으로 제한 — 유출 시 임의 역할(admin)
        HMAC 쿠키 위조로 인증 전면 우회를 막는다(rank11). POSIX는 chmod로 group/other 읽기 차단;
        Windows는 chmod가 ACL을 제한하지 못하므로 best-effort(기동 시 app.py 권한 경고가 보완)."""
        import os
        from pathlib import Path
        path = getattr(self.cfg, "overlay_path", None)
        if not path:
            return
        try:
            p = Path(path)
            if p.exists():
                os.chmod(p, 0o600)
        except OSError:
            pass

    # --- 신원 확인 ---

    def _token_principal(self, tok: str) -> Principal | None:
        h = hash_token(tok)
        for name, meta in self.tokens.items():
            stored = str((meta or {}).get("hash", ""))
            if stored and hmac.compare_digest(h, stored):
                scopes = frozenset((meta or {}).get("scopes", []) or [])
                return Principal(name=name, role=None, kind="token", scopes=scopes)
        return None

    def principal_from_headers(self, headers, client_ip: str | None = None) -> Principal:
        """Bearer 토큰(기계) 우선, 없으면 세션 쿠키(사람). 둘 다 없으면 ANON.

        client_ip: 요청의 (프록시 직전) 소켓 IP. Tailscale 신원 헤더는 이 IP가 신뢰 프록시
        목록에 있을 때만 신뢰한다 — 직접 노출 시 헤더 위조로 admin 획득하는 것을 차단(rank4).
        """
        authz = headers.get("authorization", "") or ""
        if authz.lower().startswith("bearer "):
            p = self._token_principal(authz[7:].strip())
            if p:
                return p
        # Tailscale serve 신원(켜진 경우만) — serve가 검증해 주입한 헤더. 단, 그 요청이 *신뢰
        # 프록시(루프백)*에서 왔을 때만 신뢰(헤더 위조 차단). 매핑된 사용자만.
        if self.trust_ts and client_ip is not None and client_ip in self.trusted_proxy_ips:
            login = (headers.get("tailscale-user-login", "") or "").strip()
            if login and login in self.ts_users:
                return Principal(name=login,
                                 role=str(self.ts_users[login]), kind="user")
        raw = _cookie_value(headers.get("cookie", "") or "", self.cookie_name)
        if raw:
            got = read_session(self.secret, raw)
            if got:
                user, _role = got
                meta = self.users.get(user)
                if meta:                # 사용자가 config에서 사라졌으면 무효
                    return Principal(name=user,
                                     role=str(meta.get("role", "viewer")),
                                     kind="user")
        return ANON

    def authorize(self, principal: Principal, required: str | None) -> bool:
        if required is None:
            return True
        if required == "metrics":   # Prometheus: metrics 토큰 또는 로그인 사용자 누구나
            if principal.kind == "token":
                return "metrics" in principal.scopes
            return principal.kind == "user" and principal.role is not None
        if required.startswith("token:"):
            scope = required.split(":", 1)[1]
            return principal.kind == "token" and scope in principal.scopes
        if principal.kind != "user":    # 역할 엔드포인트는 사람만(토큰 차단)
            return False
        return role_satisfies(principal.role, required)

    def verify_user(self, username: str, password: str) -> Principal | None:
        meta = self.users.get(username)
        if not meta:
            return None
        if not verify_password(str(meta.get("password", "")), password):
            return None
        return Principal(name=username, role=str(meta.get("role", "viewer")),
                         kind="user")

    def session_cookie(self, p: Principal) -> str:
        return make_session(self.secret, p.name, p.role, self.ttl_s)


# ---------- 라우터 (/login, /logout, /api/session/me) ----------

class _LoginReq(BaseModel):
    username: str
    password: str


def build_auth_router(policy: AccessPolicy, on_security_event=None) -> APIRouter:
    """on_security_event(title, detail): 로그인 실패 임계 초과 시 호출(보안 Alert 발화용)."""
    router = APIRouter()

    @router.post("/login")
    async def login(req: _LoginReq):
        if not policy.enabled:
            return {"ok": True, "auth": "disabled"}
        p = policy.verify_user(req.username, req.password)
        if not p:
            n = policy.record_login_failure(req.username or "?")
            if on_security_event is not None and n >= policy.login_fail_threshold:
                try:
                    on_security_event(
                        "로그인 실패 임계 초과",
                        f"'{req.username}' {n}회 실패 "
                        f"(최근 {int(policy.login_fail_window_s)}s)")
                except Exception:
                    pass
            raise HTTPException(401, "사용자명 또는 비밀번호가 올바르지 않습니다")
        policy.clear_login_failures(req.username or "?")
        resp = JSONResponse({"ok": True, "user": p.name, "role": p.role})
        resp.set_cookie(policy.cookie_name, policy.session_cookie(p),
                        max_age=policy.ttl_s, httponly=True, samesite="lax",
                        secure=policy.cookie_secure, path="/")
        return resp

    @router.post("/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(policy.cookie_name, path="/")
        return resp

    @router.get("/api/session/me")
    async def session_me(request: Request):
        if not policy.enabled:
            return {"authenticated": True, "auth": "disabled", "role": "admin"}
        p = policy.principal_from_headers(
            request.headers,
            client_ip=request.client.host if request.client else None)
        if p.kind == "anon":
            return {"authenticated": False}
        return {"authenticated": True, "user": p.name, "role": p.role,
                "kind": p.kind}

    return router
