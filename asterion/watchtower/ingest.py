"""분산 기상 수집(§7 중앙 수신부) — 원격 PC의 Weather Agent가 올린 표준 JSON을 검증·매핑·
재정렬(중복제거)해 WeatherRecord(Weather Store)에 적재한다. 수신·저장·조회만 담당하고 로컬
davis/sampler·safety 흐름은 건드리지 않는다(추가형). 안전 게이트 연동은 별도(범위 밖).

§7 JSON: {source_id, sensor_id?, timestamp(ISO+TZ), temperature_c, humidity_percent,
wind_speed_ms, wind_dir_deg?, rain, cloud_index}.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, func

from ..core.ontology import Db, WeatherRecord

# §7 외부 키 → WeatherRecord 속성
_FIELD_MAP = {
    "temperature_c": "temp_c", "humidity_percent": "humidity",
    "dew_point_c": "dew_point_c", "wind_speed_ms": "wind_ms",
    "wind_dir_deg": "wind_dir_deg", "cloud_index": "cloud_score",
}


def _canonical_utc(ts: str) -> str:
    """ISO 타임스탬프를 *정규 UTC* 문자열로(파싱 가능하면). 그래야 utc 컬럼의 사전식 비교/MAX가
    실제 시간순과 일치하고(rank3) 같은 순간의 오프셋 표현이 한 키로 합쳐진다(rank8 dedup). naive는
    UTC로 간주. 파싱 불가면 원본 유지(하류 age 파싱도 실패→fresh 제외=fail-safe)."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return ts
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _to_record(rec: dict) -> dict | None:
    """§7 dict → WeatherRecord 필드 dict. source_id·timestamp 필수, 없으면 None(거부).
    timestamp는 정규 UTC로 변환 저장 — 사전식 utc 비교/dedup이 시간순과 일치하게(rank3/8)."""
    src = str(rec.get("source_id") or "").strip()
    ts = str(rec.get("timestamp") or "").strip()
    if not src or not ts:
        return None
    out: dict[str, Any] = {"source_id": src, "utc": _canonical_utc(ts)}
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


def current_weather(db: Db, max_age_s: float = 120.0,
                    dropout_window_s: float = 600.0,
                    dropout_holds: bool = True) -> dict[str, Any] | None:
    """신선한(max_age_s 내) *모든* 원격 소스의 **worst-case 합성** 기상 dict(+age_s)를 반환,
    신선한 소스가 하나도 없으면 None. 샘플러가 로컬 기상 장치 없을 때 폴백으로 호출(분산 §7).

    fail-closed 핵심(rank2): 단일 '최신 utc' record 하나만 안전판정에 넘기면, pc01=맑음/
    pc02=강수가 동시에 와도 utc 최신 한쪽만 채택돼 다른 소스의 위험이 영영 사라진다 — '한
    센서라도 위험이면 닫는다'가 '가장 신선한 1건이 맑으면 OBSERVING'으로 뒤집힌다. 그래서
    신선한 소스들을 모아 worst-case로 합성한다: rain=any, wind/humidity/cloud=max. (온도/이슬점
    /utc/age는 가장 신선한 소스 기준 — 표시·신선도용.)

    신선도는 *소스별 최신 관측시각(utc)* 기준(rank19): 소스별 max(utc)를 SQL로 골라 id 윈도에
    의존하지 않으므로, 엣지 store-and-forward 에이전트의 대량 backfill(옛 record가 더 큰 id로
    유입)에도 진짜 신선한 record가 윈도 밖으로 밀려 false-stale 되지 않는다. age<0(원격 시계
    앞섬)은 신선으로 인정하지 않는다(미래 신선도로 stale 우회 방지).

    소스 dropout fail-closed(rank6): worst-case 합성은 *신선한* 소스만 본다. 그래서 위험을
    보고하던 소스(pc02=강수)가 침묵(stale)하면 fresh 집합에서 빠져 위험이 합성에서 소실되고,
    남은 맑은 소스만으로 '신선+맑음'이 돼 위험이 마스킹된다(rank2 worst-case도 못 막는 구멍).
    그래서 *최근(dropout_window_s 내) 보고하던 소스가 지금 stale*이면 — 그 소스가 위험을 보던
    것일 수 있으므로 — dropout_holds(기본 True)면 None을 반환해 fail-closed로 닫는다. 오래(>
    dropout_window_s) 침묵한 소스는 '폐기'로 보아 무시(영구 HOLD 방지). dropout_window_s<=
    max_age_s면 이 기능은 사실상 비활성."""
    from datetime import datetime, timezone

    def _q(s):
        # 소스별 최신 관측시각(utc) 1건 — id가 아닌 utc 기준, 전수(limit 없음)라 backfill 안전.
        # 같은 소스의 utc는 (source_id,utc) 중복제거로 유일하므로 소스당 정확히 1행.
        sub = (s.query(WeatherRecord.source_id,
                       func.max(WeatherRecord.utc).label("mu"))
               .filter(WeatherRecord.source_id != "")
               .group_by(WeatherRecord.source_id).subquery())
        rows = (s.query(WeatherRecord)
                .join(sub, and_(WeatherRecord.source_id == sub.c.source_id,
                                WeatherRecord.utc == sub.c.mu)).all())
        return [{"source_id": r.source_id, "utc": r.utc, "temp_c": r.temp_c,
                 "humidity": r.humidity, "wind_ms": r.wind_ms,
                 "cloud_score": r.cloud_score, "rain": r.rain,
                 "dew_point_c": r.dew_point_c} for r in rows]
    rows = db.query(_q)
    if not rows:
        return None

    now = datetime.now(timezone.utc)
    fresh: list[tuple[float, dict]] = []
    dropped: list[str] = []              # 최근 활동했으나 지금 stale인 소스(드롭아웃)
    for rec in rows:
        try:
            ts = datetime.fromisoformat(rec["utc"])
        except (ValueError, TypeError):
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now - ts).total_seconds()
        if 0 <= age <= max_age_s:        # 미래(age<0)·stale 제외
            fresh.append((age, rec))
        elif max_age_s < age <= dropout_window_s:   # 최근 보고하다 침묵 → 드롭아웃 후보
            dropped.append(rec["source_id"])
    if not fresh:
        return None
    # rank6 — 최근 보고하던 소스가 침묵(dropout)하면 그 소스가 위험을 보던 것일 수 있다. 신선한
    # 소스만으로 worst-case를 내면 그 위험이 마스킹되므로, 기본 fail-closed(HOLD)로 닫는다.
    if dropped and dropout_holds:
        return None

    fresh.sort(key=lambda x: x[0])       # 신선한 순(작은 age 먼저)
    freshest_age, freshest = fresh[0]

    def _maxv(key: str) -> float | None:
        vals = [r[key] for _, r in fresh if r.get(key) is not None]
        return max(vals) if vals else None

    return {
        "source_id": freshest["source_id"],
        "utc": freshest["utc"],
        "temp_c": freshest.get("temp_c"),
        "dew_point_c": freshest.get("dew_point_c"),
        "rain": any(bool(r.get("rain")) for _, r in fresh),   # 한 소스라도 강수 → 강수
        "wind_ms": _maxv("wind_ms"),                          # 최악(최대) 풍속
        "humidity": _maxv("humidity"),                        # 최악(최대) 습도
        "cloud_score": _maxv("cloud_score"),                  # 최악(최대) 운량
        "age_s": freshest_age,
        "sources": sorted({r["source_id"] for _, r in fresh}),
        "stale_sources": sorted(set(dropped)),                # 드롭아웃(holds=False일 때 가시성)
    }


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
