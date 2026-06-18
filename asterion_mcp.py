"""ASTERION MCP 사이드카 — Claude/Codex 앱이 자연어로 관측소를 몰게 하는 도구 서버.

ASTERION 코어는 *안 건드린다*. 이 서버는 돌아가는 ASTERION의 HTTP API(/api/*)를
호출하는 얇은 래퍼 + 행성 위치는 astropy로 직접 계산한다. 앱(MCP 클라이언트)이
stdio로 이 서버를 띄우고, 사용자가 "화성 보여줘" 하면 LLM이 아래 도구들을 호출한다.

연결(둘 중 쓰는 앱에):
  Claude Desktop  → claude_desktop_config.json 의 "mcpServers"
  Codex           → ~/.codex/config.toml 의 [mcp_servers.asterion]
  (구체 스니펫은 README/대화 참조. command = 이 venv의 python, args = 이 파일 경로)

환경변수 ASTERION_URL 로 대상 서버 지정(기본 http://127.0.0.1:8520).
안전: 실행계 명령은 ASTERION의 안전게이트/사전조건을 그대로 통과한다(여기서 우회 못함).
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("ASTERION_URL", "http://127.0.0.1:8520")
mcp = FastMCP("asterion")

# 지원 천체 (행성 + 달/해) — astropy get_body 이름
_BODIES = {"mercury", "venus", "mars", "jupiter", "saturn", "uranus",
           "neptune", "moon", "sun"}
_KO = {"수성": "mercury", "금성": "venus", "화성": "mars", "목성": "jupiter",
       "토성": "saturn", "천왕성": "uranus", "해왕성": "neptune",
       "달": "moon", "태양": "sun"}
MIN_ALT_DEG = 5.0   # 이 아래면 goto 불가(지평선/한계 고도)


def _get(path: str, **params):
    try:
        r = httpx.get(BASE + path, params=params, timeout=10.0)
        return r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else {"status_code": r.status_code, "text": r.text}
    except Exception as exc:
        return {"error": f"ASTERION 연결 실패({BASE}{path}): {exc} — 서버가 떠 있나요?"}


def _post(path: str, payload: dict | None = None):
    try:
        r = httpx.post(BASE + path, json=payload or {}, timeout=60.0)
        body = r.json() if r.headers.get("content-type", "").startswith(
            "application/json") else {"text": r.text}
        if r.status_code >= 400:
            return {"ok": False, "status": r.status_code,
                    "detail": body.get("detail", body)}
        return {"ok": True, **(body if isinstance(body, dict) else {"result": body})}
    except Exception as exc:
        return {"error": f"ASTERION 호출 실패({path}): {exc}"}


def _planet_altaz(name: str) -> dict:
    """astropy로 천체의 현재 alt/az/radec 계산 (관측소 lat/lon은 /api/status에서)."""
    key = _KO.get(name.strip(), name.strip().lower())
    if key not in _BODIES:
        return {"error": f"'{name}'은(는) 지원 행성/천체가 아닙니다. "
                f"지원: {', '.join(sorted(_BODIES))} (또는 한글명)"}
    st = _get("/api/status")
    geo = (st or {}).get("geo") or {}
    lat, lon = geo.get("lat"), geo.get("lon")
    if lat is None or lon is None:
        return {"error": "관측소 위경도를 못 읽음 (ASTERION /api/status)"}
    from astropy.coordinates import EarthLocation, AltAz, get_body
    from astropy.time import Time
    import astropy.units as u
    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
    t = Time.now()
    body = get_body(key, t, loc)
    aa = body.transform_to(AltAz(obstime=t, location=loc))
    alt = float(aa.alt.deg)
    return {"body": key, "alt_deg": round(alt, 2), "az_deg": round(float(aa.az.deg), 2),
            "ra_hours": round(float(body.ra.hour), 5),
            "dec_degs": round(float(body.dec.deg), 5),
            "above_horizon": alt > 0, "observable": alt >= MIN_ALT_DEG,
            "note": ("관측 가능" if alt >= MIN_ALT_DEG else
                     f"고도 {alt:.1f}° — 지평선/한계({MIN_ALT_DEG}°) 아래라 goto 불가")}


# ---------- 조회 ----------

@mcp.tool()
def observatory_status() -> dict:
    """관측소 현재 상태 요약: 모드, 안전상태(+사유), 마운트(연결/고도/방위/추적),
    돔(셔터/방위), 카메라, 기상, 시각/태양고도. 무엇이든 하기 전에 먼저 확인."""
    s = _get("/api/status")
    if "error" in s:
        return s
    return {
        "mode": s.get("mode"), "site": s.get("site"),
        "time": s.get("time"), "sun": s.get("sun"),
        "safety": s.get("safety"),
        "mount": s.get("mount"), "dome": s.get("dome"),
        "camera": s.get("camera"), "weather": s.get("weather"),
        "orchestrator": s.get("orchestrator"),
    }


@mcp.tool()
def planet_position(name: str) -> dict:
    """행성/달/해의 현재 고도·방위·적경적위와 관측 가능 여부. 예: '화성','mars'.
    고도가 지평선 아래면 observable=false (그 이유로 사용자에게 설명·대안 제시)."""
    return _planet_altaz(name)


@mcp.tool()
def visible_planets() -> dict:
    """지금 지평선 위(관측 가능)인 행성 목록 — '다른 거 볼까요?' 대안 제시용."""
    out = []
    for key in ("mercury", "venus", "mars", "jupiter", "saturn", "moon"):
        r = _planet_altaz(key)
        if r.get("observable"):
            out.append({"body": key, "alt_deg": r["alt_deg"], "az_deg": r["az_deg"]})
    return {"observable": out, "count": len(out)}


@mcp.tool()
def resolve_target(name: str) -> dict:
    """항성/딥스카이 이름을 RA/Dec로 해석(CDS Sesame). 예: 'M42','베가'. 행성은 planet_position."""
    return _get("/api/resolve", name=name)


@mcp.tool()
def list_plans(status: str | None = None) -> dict:
    """관측 계획(ObservationPlan) 목록. status로 필터(draft/approved/done 등)."""
    r = _get("/api/meridian/plans", **({"status": status} if status else {}))
    return {"plans": r} if isinstance(r, list) else r


# ---------- 실행 (ASTERION 안전게이트 통과 — 여기서 우회 불가) ----------

@mcp.tool()
def goto_planet(name: str) -> dict:
    """망원경을 행성으로 슬루('화성 보여줘'). 지평선/한계 아래면 시도 없이 거부하고
    이유를 돌려준다. 가능하면 ASTERION의 안전게이트를 통과해 goto."""
    pos = _planet_altaz(name)
    if "error" in pos:
        return pos
    if not pos["observable"]:
        return {"ok": False, "reason": pos["note"], "alt_deg": pos["alt_deg"]}
    res = _post("/api/actions/mount/goto_radec",
                {"ra": str(pos["ra_hours"]), "dec": str(pos["dec_degs"])})
    return {**res, "target": pos["body"], "alt_deg": pos["alt_deg"]}


@mcp.tool()
def create_plan(target_name: str, filters: list[str], exposure_s: float = 60.0,
                count_per_filter: int = 10, ra: str | None = None,
                dec: str | None = None) -> dict:
    """관측 계획 생성(draft). 나중에 실행/예약할 의도를 기록. 행성은 ra/dec 비워도 됨."""
    payload = {"target_name": target_name, "filters": filters,
               "exposure_s": exposure_s, "count_per_filter": count_per_filter}
    if ra and dec:
        payload["ra"], payload["dec"] = ra, dec
    return _post("/api/meridian/plans", payload)


@mcp.tool()
def run_plan(plan_id: int) -> dict:
    """승인된 계획을 Orchestrator로 실행. 사전조건(승인/연결/안전/유휴) 실패 시 거부 사유 반환."""
    return _post(f"/api/meridian/plans/{plan_id}/run")


@mcp.tool()
def dome_shutter(open: bool) -> dict:
    """돔 셔터 열기/닫기. 수동 셔터 돔이면 거부됨(can_command_shutter=false)."""
    return _post("/api/actions/dome/shutter", {"open": open})


if __name__ == "__main__":
    mcp.run()   # stdio transport (앱이 이 프로세스를 띄워 통신)
