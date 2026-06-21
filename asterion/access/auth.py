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
        self.secret = self._ensure_secret()

    def _ensure_secret(self) -> str:
        s = str(self.cfg.get("server.auth.session_secret", "") or "")
        if self.enabled and not s:
            # 켜졌는데 시크릿이 없으면 생성·영속(재시작에도 세션 유지). config.local.json에 저장.
            s = secrets.token_hex(32)
            try:
                self.cfg.set("server.auth.session_secret", s)
            except Exception:
                pass
        return s or "dev-insecure-secret"

    # --- 신원 확인 ---

    def _token_principal(self, tok: str) -> Principal | None:
        h = hash_token(tok)
        for name, meta in self.tokens.items():
            stored = str((meta or {}).get("hash", ""))
            if stored and hmac.compare_digest(h, stored):
                scopes = frozenset((meta or {}).get("scopes", []) or [])
                return Principal(name=name, role=None, kind="token", scopes=scopes)
        return None

    def principal_from_headers(self, headers) -> Principal:
        """Bearer 토큰(기계) 우선, 없으면 세션 쿠키(사람). 둘 다 없으면 ANON."""
        authz = headers.get("authorization", "") or ""
        if authz.lower().startswith("bearer "):
            p = self._token_principal(authz[7:].strip())
            if p:
                return p
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


def build_auth_router(policy: AccessPolicy) -> APIRouter:
    router = APIRouter()

    @router.post("/login")
    async def login(req: _LoginReq):
        if not policy.enabled:
            return {"ok": True, "auth": "disabled"}
        p = policy.verify_user(req.username, req.password)
        if not p:
            raise HTTPException(401, "사용자명 또는 비밀번호가 올바르지 않습니다")
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
        p = policy.principal_from_headers(request.headers)
        if p.kind == "anon":
            return {"authenticated": False}
        return {"authenticated": True, "user": p.name, "role": p.role,
                "kind": p.kind}

    return router
