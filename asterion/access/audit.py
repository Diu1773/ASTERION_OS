"""인증 주체(Principal)의 요청 범위 전파 + 감사 라벨.

contextvar로 '지금 이 요청을 누가 했나'를 흘려, ActionBus 감사로그(actor)에 사람별
신원을 붙인다. **asterion 다른 모듈·외부 패키지에 의존하지 않는다** — core.actions가
안전하게 import할 수 있게(순환·무거운 의존 회피). 인증이 꺼져 principal이 없으면 base
라벨을 그대로 유지하므로 하위호환(기존 'operator' 그대로).
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    name: str
    role: str | None = None            # viewer | operator | admin (사람), None=토큰/익명
    kind: str = "user"                 # user | token | anon
    scopes: frozenset[str] = field(default_factory=frozenset)


ANON = Principal(name="anonymous", role=None, kind="anon")

_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "asterion_principal", default=None)


def set_principal(p: Principal | None):
    """요청 진입 시 미들웨어가 호출 — 반환 토큰을 finally에서 reset."""
    return _principal.set(p)


def reset_principal(token) -> None:
    try:
        _principal.reset(token)
    except (LookupError, ValueError):
        pass


def current_principal() -> Principal | None:
    return _principal.get()


def actor_label(base: str) -> str:
    """ActionBus actor에 현재 인증 사용자명을 붙인다 — 'operator' → 'operator(alice)'.
    인증이 꺼져(principal 없음/익명) 있으면 base 그대로(하위호환)."""
    p = current_principal()
    if p is None or p.kind == "anon" or not p.name or p.name == base:
        return base
    return f"{base}({p.name})"
