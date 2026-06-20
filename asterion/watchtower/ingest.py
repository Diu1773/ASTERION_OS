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

    def _existing(s):
        ex = set()
        for sid, utc in keys:
            if (s.query(WeatherRecord.id)
                    .filter(WeatherRecord.source_id == sid, WeatherRecord.utc == utc)
                    .first()):
                ex.add((sid, utc))
        return ex
    exist = db.query(_existing)

    accepted = 0
    for key, m in keys.items():
        if key in exist:
            continue
        db.add(WeatherRecord(**m))
        accepted += 1
    return {"accepted": accepted, "duplicates": len(mapped) - accepted,
            "rejected": rejected, "sources": sorted({m["source_id"] for m in mapped})}


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
