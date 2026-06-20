"""분산 기상 수집(§7 중앙 수신부) — 원격 PC의 Weather Agent가 올린 표준 JSON을 검증·매핑·
재정렬(중복제거)해 WeatherRecord(Weather Store)에 적재한다. 수신·저장·조회만 담당하고 로컬
davis/sampler·safety 흐름은 건드리지 않는다(추가형). 안전 게이트 연동은 별도(범위 밖).

§7 JSON: {source_id, sensor_id?, timestamp(ISO+TZ), temperature_c, humidity_percent,
wind_speed_ms, wind_dir_deg?, rain, cloud_index}.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func

from ..core.ontology import Db, WeatherRecord

# §7 외부 키 → WeatherRecord 속성
_FIELD_MAP = {
    "temperature_c": "temp_c", "humidity_percent": "humidity",
    "dew_point_c": "dew_point_c", "wind_speed_ms": "wind_ms",
    "wind_dir_deg": "wind_dir_deg", "cloud_index": "cloud_score",
}


def _to_record(rec: dict) -> dict | None:
    """§7 dict → WeatherRecord 필드 dict. source_id·timestamp 필수, 없으면 None(거부)."""
    src = str(rec.get("source_id") or "").strip()
    ts = str(rec.get("timestamp") or "").strip()
    if not src or not ts:
        return None
    out: dict[str, Any] = {"source_id": src, "utc": ts}
    for k, attr in _FIELD_MAP.items():
        if rec.get(k) is not None:
            try:
                out[attr] = float(rec[k])
            except (TypeError, ValueError):
                pass
    if rec.get("rain") is not None:
        out["rain"] = bool(rec["rain"])
    return out


def ingest_records(db: Db, payload: Any) -> dict[str, Any]:
    """단일 dict 또는 배열을 수신 → 매핑·검증 → 재정렬(중복제거) → 적재.
    같은 (source_id, utc)는 배치 내·DB 모두 중복으로 스킵. 반환 카운트."""
    recs = payload if isinstance(payload, list) else [payload]
    mapped, rejected = [], 0
    for r in recs:
        m = _to_record(r) if isinstance(r, dict) else None
        if m is None:
            rejected += 1
        else:
            mapped.append(m)
    if not mapped:
        return {"accepted": 0, "duplicates": 0, "rejected": rejected, "sources": []}

    keys = {(m["source_id"], m["utc"]): m for m in mapped}   # 배치 내 중복 합침(나중 것)

    # DB 기존 중복은 단일 쿼리로(소스·시각 IN) — N+1 회피. 네트워크 복구 후 대량 백필
    # 재전송(§7.4)에도 쿼리 1번. 교집합은 배치 키로만 판정하므로 IN 교차곱 초과는 무해.
    src_ids = list({sid for sid, _ in keys})
    utcs = list({utc for _, utc in keys})

    def _existing(s):
        rows = (s.query(WeatherRecord.source_id, WeatherRecord.utc)
                .filter(WeatherRecord.source_id.in_(src_ids),
                        WeatherRecord.utc.in_(utcs)).all())
        return {(sid, utc) for sid, utc in rows}
    exist = db.query(_existing)

    accepted = 0
    for key, m in keys.items():
        if key in exist:
            continue
        db.add(WeatherRecord(**m))
        accepted += 1
    return {"accepted": accepted, "duplicates": len(mapped) - accepted,
            "rejected": rejected, "sources": sorted({m["source_id"] for m in mapped})}


def current_weather(db: Db, max_age_s: float = 120.0) -> dict[str, Any] | None:
    """가장 최신 원격 기상 record가 max_age_s 내로 신선하면 표준 dict(+age_s) 반환, 아니면
    None. 샘플러가 로컬 기상 장치 없을 때 폴백으로 호출(분산 §7). age는 record utc(ISO) 기준 —
    오래됐거나 시각 파싱 실패면 None → fail-closed stale 판정으로 흘러감."""
    from datetime import datetime, timezone

    def _q(s):
        row = (s.query(WeatherRecord).filter(WeatherRecord.source_id != "")
               .order_by(WeatherRecord.id.desc()).first())
        if row is None:
            return None
        return {"source_id": row.source_id, "utc": row.utc, "temp_c": row.temp_c,
                "humidity": row.humidity, "wind_ms": row.wind_ms,
                "cloud_score": row.cloud_score, "rain": row.rain,
                "dew_point_c": row.dew_point_c}
    rec = db.query(_q)
    if rec is None:
        return None
    try:
        ts = datetime.fromisoformat(rec["utc"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).total_seconds()
    except (ValueError, TypeError):
        return None
    if age < 0 or age > max_age_s:
        return None
    rec["age_s"] = age
    return rec


def latest_per_source(db: Db) -> list[dict[str, Any]]:
    """source_id별 최신(가장 큰 id) 1건 — 분산 대시보드/상태용."""
    def _q(s):
        sub = (s.query(func.max(WeatherRecord.id))
               .filter(WeatherRecord.source_id != "")
               .group_by(WeatherRecord.source_id))
        rows = s.query(WeatherRecord).filter(WeatherRecord.id.in_(sub)).all()
        return [{"source_id": r.source_id, "utc": r.utc, "temp_c": r.temp_c,
                 "humidity": r.humidity, "wind_ms": r.wind_ms,
                 "cloud_score": r.cloud_score, "rain": r.rain} for r in rows]
    return db.query(_q)
