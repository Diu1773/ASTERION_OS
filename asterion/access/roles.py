"""역할 등급 + 경로→최소권한 정책 (secure-by-default).

핵심 설계: **명시 화이트리스트가 아니라 기본 거부형.** 새 엔드포인트가 추가돼도
자동으로 보호되도록 — GET=viewer, 변경계열(POST/PUT/PATCH/DELETE)=operator,
시스템·개발 경로=admin, ingest=토큰. '열어둘 곳'만 PUBLIC에 명시한다.
"""

from __future__ import annotations

ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}

# 인증 없이 접근 가능 — 로그인/정적/SPA 셸/헬스. (접두사 매칭)
PUBLIC_PREFIXES = ("/login", "/logout", "/static/", "/favicon")
# 정확 일치로만 공개(접두사로 너무 넓게 열지 않게)
PUBLIC_EXACT = frozenset({"/", "/api/session/me"})

# 변경계열이라도 admin이 필요한 접두사 (시스템 연결·드라이버 모드·안전 오버라이드 계열)
ADMIN_PREFIXES = ("/api/system", "/api/dev")

# 기계(토큰) 전용 — {경로: 필요 scope}
TOKEN_ENDPOINTS = {"/api/weather/ingest": "ingest"}


def required_access(path: str, method: str, scheme: str) -> str | None:
    """이 요청에 필요한 권한. 반환:
    None(공개) | 'viewer' | 'operator' | 'admin' | 'token:<scope>' | 'metrics'."""
    if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES):
        return None
    # Prometheus 스크레이프 — 기계 토큰(scope=metrics) 또는 로그인 사용자 누구나(아래 authorize).
    if path == "/metrics":
        return "metrics"
    if path in TOKEN_ENDPOINTS:
        return f"token:{TOKEN_ENDPOINTS[path]}"
    if scheme == "websocket":          # 이벤트 스트림 = 읽기
        return "viewer"
    if method in ("GET", "HEAD", "OPTIONS"):
        return "viewer"
    if path.startswith(ADMIN_PREFIXES):
        return "admin"
    return "operator"                  # 그 외 모든 변경계열(기본 거부 → operator+)


def role_satisfies(have: str | None, need: str) -> bool:
    """have 역할이 need 이상인가 (viewer<operator<admin). have 미상=실패(fail-closed)."""
    if have is None:
        return False
    return ROLE_RANK.get(have, 0) >= ROLE_RANK.get(need, 99)
