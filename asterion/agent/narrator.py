"""Narrator — 능동 내레이션(먼저 말하기).

발화된 Alert를 '관측 + 왜 + 권고' 한 줄로 바꿔 챗/로그에 *먼저* 띄운다. **사건이 생길 때만**
말한다 — 인사·잡담·주기적 상태 보고는 하지 않는다(로드맵 §13.4 "의미 있을 때만"). 규칙 기반
이라 모델 없이도 동작한다(모델 polish는 후속 — 문장을 다듬는 층). 안전 원칙: **추천만 —
스스로 행동하지 않는다**(AI는 제안, 사람/정책이 승인).

경보는 이미 AlertManager가 쿨다운·룰로 걸러 발화하므로(스팸 없음), 여기선 그걸 자연어로
풀어 권고를 붙이기만 한다. EventHub.alert()가 발화 시 이 narrate를 부른다.
"""

from __future__ import annotations

from typing import Any

# rule_id → '관측 + 권고' 한 줄. {detail}=경보 상세. 미등록 rule은 경보 title+detail로 폴백.
_TEMPLATES: dict[str, str] = {
    "emergency_close":
        "⚠ 비상 폐쇄 조건 — {detail}. 돔 닫힘·추적 정지를 확인하세요.",
    "safety_fault":
        "⚠ 장비 연결 끊김 — {detail}. 관측 게이트가 막혔어요, 연결 점검 필요.",
    "dome_unsafe_open":
        "⚠ 위험 상태인데 셔터 열림 — {detail}. 즉시 닫아야 합니다.",
    "weather_stale":
        "📡 기상 텔레메트리 끊김 — {detail}. 데이터 복구까지 개방 보류(fail-closed).",
    "weather_hold":
        "🌥 기상 보류 — {detail}. 회복까지 관측 대기 권고.",
    "session_deadman":
        "🛰 원격 세션 데드맨 발화 — {detail}. 세이프-스테이트로 전환됐어요.",
    "session_deadman_shutter_stuck":
        "⚠ 수동 셔터 자동 폐쇄 불가 — {detail}. 즉시 현장에서 슬릿을 닫아야 합니다.",
    "session_deadman_no_park":
        "⚠ 가대 파킹 미지원 — {detail}. 가대가 하늘 좌표에 잔류, 현장 확인 필요.",
    "weather_forecast_rain":
        "🌧 강수 예보 — {detail}. 새 긴 노출은 보류 권고(실제 닫힘은 센서가 처리).",
    "login_failures":
        "🔒 로그인 실패 임계 초과 — {detail}. 무단 접근 시도 가능성, 확인 필요.",
    "solar_override_active":
        "☀ 태양 회피 오버라이드 활성 — {detail}. OTA 태양 근접 슬루 허용 상태(위험).",
}


class Narrator:
    """발화된 Alert dict → 능동 내레이션 한 줄(또는 None). 규칙 기반 순수 함수."""

    @staticmethod
    def narrate(alert: dict[str, Any] | None) -> str | None:
        if not alert:
            return None
        rule = str(alert.get("rule_id", "") or "")
        detail = str(alert.get("detail", "") or "").strip()
        title = str(alert.get("title", "") or "").strip()
        tmpl = _TEMPLATES.get(rule)
        if tmpl is not None:
            return tmpl.format(detail=(detail or title or rule))
        # 미등록 rule → 경보 자체 title+detail로 폴백('먼저 말하기'는 유지). 둘 다 없으면 침묵.
        base = " — ".join(x for x in (title, detail) if x)
        return f"ℹ {base}. 확인이 필요해요." if base else None
