"""규칙 기반 안전 상태 판정.

돔이 수동인 동안은 표시·권고용이다. 원격 돔이 들어오면 이 레이어가
액션 사전조건(돔 개폐 등)의 1차 관문이 된다. LLM/ML 판단보다 항상
우선한다 (AI 안전 원칙).

핵심 원칙은 fail-closed다 (로드맵 원칙 #5, §6.4/§6.5): 기상 데이터가
없거나(한 번도 못 받음) 너무 오래되면(stale) 기상값을 신뢰하지 않고
WEATHER_HOLD로 닫는다 — '데이터 없음 = unsafe'.
"""

from __future__ import annotations

# 상태 정의 (로드맵 Watchtower 상태 모델)
SAFE_CLOSED = "SAFE_CLOSED"
READY_CHECK = "READY_CHECK"
OPEN_ALLOWED = "OPEN_ALLOWED"
OBSERVING = "OBSERVING"
WEATHER_HOLD = "WEATHER_HOLD"
EMERGENCY_CLOSE = "EMERGENCY_CLOSE"
FAULT = "FAULT"

# 기상 텔레메트리 신선도 임계 (로드맵 §6.5). status.py가 config로 덮어쓸 수 있다.
WEATHER_WARN_AGE_S = 30.0     # 이 이상 지연 → 경고만 단다 (운영 계속)
WEATHER_UNSAFE_AGE_S = 120.0  # 이 이상 지연(또는 데이터 없음) → WEATHER_HOLD (fail-closed)


def evaluate(*, missing_required: list[str] | None = None,
             weather: dict | None = None, sun_alt: float, session_running: bool,
             weather_age_s: float | None = None,
             warn_age_s: float = WEATHER_WARN_AGE_S,
             unsafe_age_s: float = WEATHER_UNSAFE_AGE_S) -> dict:
    """현재 안전 상태를 판정한다. 장비 키를 모른다 — 호출부(status)가 REGISTRY의
    safety_role로 추려 넘긴다: 'required' 장비 중 미연결된 것의 라벨(missing_required)과
    'weather' 장비 데이터(weather: rain/wind/humidity/cloud).

    weather_age_s: 마지막으로 *연결+유효값* 기상 텔레메트리를 받은 뒤 경과한 초.
      None이면 그런 텔레메트리를 한 번도 못 받은 것(= 데이터 없음). fail-closed
      원칙에 따라 데이터가 없거나(unsafe_age_s↑) 오래되면 기상값을 믿지 않고 닫고,
      30s~ 지연은 경고만 단다.
    """
    if missing_required:
        return {"state": FAULT,
                "reasons": [f"{'/'.join(missing_required)} 연결 끊김"]}
    weather = weather or {}

    # ── 기상 텔레메트리 fail-closed 게이트 ─────────────────────────────────
    # "기상 데이터가 없으면 safe가 아니라 unsafe다." (로드맵 원칙 #5, §6.4/§6.5)
    # 데이터를 한 번도 못 받았거나(None) 너무 오래됐으면, 아래 기상값(아마 결측→0)
    # 을 신뢰하지 말고 닫는다. EMERGENCY_CLOSE(강수/강풍)는 신뢰할 데이터가 있을
    # 때만 의미가 있으므로 stale 게이트가 그보다 앞선다.
    if weather_age_s is None:
        return {"state": WEATHER_HOLD, "weather_stale": True,
                "reasons": ["기상 텔레메트리 없음 — 안전 미확인 (fail-closed)"]}
    if weather_age_s >= unsafe_age_s:
        return {"state": WEATHER_HOLD, "weather_stale": True,
                "reasons": [f"기상 텔레메트리 {weather_age_s:.0f}s 지연 "
                            f"(≥ {unsafe_age_s:.0f}s) — unsafe (fail-closed)"]}

    rain = bool(weather.get("rain"))
    wind = weather.get("wind") or 0.0
    hum = weather.get("humidity") or 0.0
    cloud = weather.get("cloud") or 0.0

    def _result(state: str, reasons: list[str]) -> dict:
        out = {"state": state, "reasons": list(reasons)}
        if weather_age_s >= warn_age_s:
            out["reasons"].append(f"⚠ 기상 텔레메트리 {weather_age_s:.0f}s 지연")
            out["weather_warn"] = True
        return out

    if rain or wind > 20.0:
        reasons: list[str] = []
        if rain:
            reasons.append("강수 감지")
        if wind > 20.0:
            reasons.append(f"풍속 {wind:.1f} m/s > 20")
        return _result(EMERGENCY_CLOSE, reasons)

    if hum > 90.0 or wind > 15.0 or cloud > 0.8:
        reasons = []
        if hum > 90.0:
            reasons.append(f"습도 {hum:.0f}% > 90")
        if wind > 15.0:
            reasons.append(f"풍속 {wind:.1f} m/s > 15")
        if cloud > 0.8:
            reasons.append(f"구름 점수 {cloud:.2f} > 0.8")
        return _result(WEATHER_HOLD, reasons)

    # 주간(태양 > -0.5°)은 세션 실행 중이어도 SAFE_CLOSED로 닫는다. 박명에 정당히
    # 시작된 세션이 일출까지 이어질 때, session_running 분기가 먼저 평가되면 sun_alt와
    # 무관하게 OBSERVING으로 자기마스킹되어 주간 슬루/노출 방지 게이트가 무력화된다.
    # 주간 보호는 fail-safe 기본값이므로 세션 상태가 덮을 수 없다 (주간 검사를 앞으로).
    if sun_alt > -0.5:
        return _result(SAFE_CLOSED, [f"태양 고도 {sun_alt:+.1f}° (주간)"])

    if session_running:
        return _result(OBSERVING, ["자동 세션 실행 중"])

    if sun_alt > -6.0:
        return _result(READY_CHECK,
                       [f"태양 고도 {sun_alt:+.1f}° (박명 — 점검/플랫 시간)"])
    return _result(OPEN_ALLOWED, [f"태양 고도 {sun_alt:+.1f}°, 기상 양호"])
