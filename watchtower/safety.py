"""규칙 기반 안전 상태 판정.

돔이 수동인 동안은 표시·권고용이다. 원격 돔이 들어오면 이 레이어가
액션 사전조건(돔 개폐 등)의 1차 관문이 된다. LLM/ML 판단보다 항상
우선한다 (AI 안전 원칙).
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


def evaluate(mount_connected: bool, camera_connected: bool,
             weather: dict, sun_alt: float,
             session_running: bool) -> dict:
    reasons: list[str] = []

    if not mount_connected or not camera_connected:
        missing = []
        if not mount_connected:
            missing.append("마운트")
        if not camera_connected:
            missing.append("카메라")
        return {"state": FAULT, "reasons": [f"{'/'.join(missing)} 연결 끊김"]}

    rain = bool(weather.get("rain"))
    wind = weather.get("wind") or 0.0
    hum = weather.get("humidity") or 0.0
    cloud = weather.get("cloud") or 0.0

    if rain or wind > 20.0:
        if rain:
            reasons.append("강수 감지")
        if wind > 20.0:
            reasons.append(f"풍속 {wind:.1f} m/s > 20")
        return {"state": EMERGENCY_CLOSE, "reasons": reasons}

    if hum > 90.0 or wind > 15.0 or cloud > 0.8:
        if hum > 90.0:
            reasons.append(f"습도 {hum:.0f}% > 90")
        if wind > 15.0:
            reasons.append(f"풍속 {wind:.1f} m/s > 15")
        if cloud > 0.8:
            reasons.append(f"구름 점수 {cloud:.2f} > 0.8")
        return {"state": WEATHER_HOLD, "reasons": reasons}

    if session_running:
        return {"state": OBSERVING, "reasons": ["자동 세션 실행 중"]}

    if sun_alt > -0.5:
        return {"state": SAFE_CLOSED,
                "reasons": [f"태양 고도 {sun_alt:+.1f}° (주간)"]}
    if sun_alt > -6.0:
        return {"state": READY_CHECK,
                "reasons": [f"태양 고도 {sun_alt:+.1f}° (박명 — 점검/플랫 시간)"]}

    return {"state": OPEN_ALLOWED,
            "reasons": [f"태양 고도 {sun_alt:+.1f}°, 기상 양호"]}
