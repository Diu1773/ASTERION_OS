"""Alert — 위험 상황 알림 룰 평가(무인 운영 안전 루프). 이미 구조화된 안전 스냅샷
(safety.evaluate 출력: state·reasons·weather_stale)을 룰로 평가해 Alert를 발화한다.
**신규 안전 판단은 없다** — 출력을 소비만 한다. 같은 룰이 쿨다운 내면 재발화 안 함.
외부 채널(SMS/SMTP)은 범위 밖. 기존 safety/dome 경로는 읽기만(무수정).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..core.ontology import Alert, Db

CRITICAL = "critical"
WARN = "warn"


def _reasons(snap: dict) -> str:
    return " · ".join((snap.get("safety") or {}).get("reasons", []))


def _rule_emergency(snap: dict):
    if (snap.get("safety") or {}).get("state") == "EMERGENCY_CLOSE":
        return "비상 폐쇄 필요", _reasons(snap)
    return None


def _rule_fault(snap: dict):
    if (snap.get("safety") or {}).get("state") == "FAULT":
        return "장비 연결 끊김", _reasons(snap)
    return None


def _rule_dome_unsafe_open(snap: dict):
    saf = snap.get("safety") or {}
    dome = snap.get("dome") or {}
    if saf.get("state") in ("EMERGENCY_CLOSE", "WEATHER_HOLD"):
        sh = dome.get("shutter") or dome.get("shutter_state")
        if sh in ("open", "opening") or dome.get("open") is True:
            return "위험 상태인데 돔 셔터 열림", f"{saf.get('state')} · 셔터 {sh or 'open'}"
    return None


def _rule_weather_stale(snap: dict):
    if (snap.get("safety") or {}).get("weather_stale"):
        return "기상 텔레메트리 stale", _reasons(snap)
    return None


def _rule_weather_hold(snap: dict):
    saf = snap.get("safety") or {}
    if saf.get("state") == "WEATHER_HOLD" and not saf.get("weather_stale"):
        return "기상 보류(관측 불가)", _reasons(snap)
    return None


# (rule_id, level, cooldown_s, fn). 쿨다운: 같은 룰 재발화 억제(스팸 방지).
RULES = [
    ("emergency_close", CRITICAL, 60.0, _rule_emergency),
    ("safety_fault", CRITICAL, 60.0, _rule_fault),
    ("dome_unsafe_open", CRITICAL, 30.0, _rule_dome_unsafe_open),
    ("weather_stale", WARN, 300.0, _rule_weather_stale),
    ("weather_hold", WARN, 300.0, _rule_weather_hold),
]


class AlertManager:
    def __init__(self, db: Db, events: Any = None):
        self.db = db
        self.events = events

    def _in_cooldown(self, rule_id: str, cooldown_s: float) -> bool:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=cooldown_s)).isoformat(timespec="seconds")

        def _q(s):
            return (s.query(Alert.id)
                    .filter(Alert.rule_id == rule_id, Alert.utc >= cutoff)
                    .first() is not None)
        return self.db.query(_q)

    def evaluate(self, snap: dict) -> list[dict[str, Any]]:
        """스냅샷을 룰로 평가 → 발화분(쿨다운 통과)을 DB 적재 + (events 있으면) 브로드캐스트."""
        snap = snap or {}
        state = (snap.get("safety") or {}).get("state", "")
        fired = []
        for rule_id, level, cooldown, fn in RULES:
            res = fn(snap)
            if res is None or self._in_cooldown(rule_id, cooldown):
                continue
            title, detail = res
            row = self.db.add(Alert(rule_id=rule_id, level=level, title=title,
                                    detail=detail, state=state))
            rec = {"id": row.id, "rule_id": rule_id, "level": level,
                   "title": title, "detail": detail, "state": state}
            fired.append(rec)
            if self.events is not None and hasattr(self.events, "alert"):
                self.events.alert(rec)
        return fired
