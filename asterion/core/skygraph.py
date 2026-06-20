"""Skygraph — 대상 중심 집계(Ph9, 로드맵 §11). 온톨로지의 Target/Plan/Session/Frame/
QualityMetric을 한 대상의 dossier로 묶는다 — "데이터는 파일이 아니라 Target/Observation
중심"(원칙 #6)의 구현. Frame→Target은 T1의 `ObservationSession.plan_id` 1급 엣지로 조인하고,
plan_id가 없던 레거시 세션은 `summary_json`의 target 문자열로 보조 매칭한다.

core 계층 — operation/agent에 의존하지 않고 ontology만 읽는다.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_

from .dso_catalog import DSO, TYPE_KO
from .ontology import (
    CalibrationProduct, Db, Decision, Frame, ObservationPlan, ObservationSession,
    QualityMetric, Target, TelescopeState, WeatherRecord,
)

_CAT_BY: dict[str, dict] | None = None


def _catalog_index() -> dict[str, dict]:
    global _CAT_BY
    if _CAT_BY is None:
        idx: dict[str, dict] = {}
        for o in DSO:
            idx[o["id"].lower()] = o
            if o.get("name"):
                idx[o["name"].lower()] = o
        _CAT_BY = idx
    return _CAT_BY


def _catalog_find(q: str | None) -> dict | None:
    if not q:
        return None
    idx = _catalog_index()
    s = q.strip().lower()
    if s in idx:
        return idx[s]
    if "(" in s and ")" in s:   # 라벨 "이름 (ID)"에서 ID 추출
        inner = s[s.rfind("(") + 1:s.rfind(")")].strip()
        if inner in idx:
            return idx[inner]
    return None


def _transit_alt(lat: float, dec: float | None) -> float | None:
    """대상이 청람에서 도달하는 최고고도(자오선 통과). 시간 무관·의존성 없는 가시성 지표."""
    return None if dec is None else round(90.0 - abs(lat - dec), 1)


def _target_session_ids(s, name: str, plan_ids: list[int]) -> list[int]:
    """대상의 관측 세션 id — plan_id 1급 엣지(T1) ∪ 레거시 summary(json 파싱 정확매칭).
    coarse 프리필터로 좁힌 뒤 json으로 target==name 확인(포맷 비결합·부분일치 오탐 제거)."""
    conds = []
    if plan_ids:
        conds.append(ObservationSession.plan_id.in_(plan_ids))
    conds.append(ObservationSession.summary_json.contains(f'"{name}"'))
    pid_set = set(plan_ids)
    out = []
    for se in s.query(ObservationSession).filter(or_(*conds)).all():
        if se.plan_id in pid_set:
            out.append(se.id)
            continue
        try:
            if json.loads(se.summary_json or "{}").get("target") == name:
                out.append(se.id)
        except Exception:
            pass
    return out


def target_light_frames(db: Db, name: str) -> list[dict]:
    """대상의 LIGHT 프레임 전체(캡 없음, 시간순) — 라이트커브용. {id,date_obs_utc,filter}."""
    name = (name or "").strip()

    def _q(s):
        tgt = s.query(Target).filter(Target.name == name).first()
        plan_ids = ([p.id for p in s.query(ObservationPlan)
                     .filter(ObservationPlan.target_id == tgt.id).all()] if tgt else [])
        sess_ids = _target_session_ids(s, name, plan_ids)
        if not sess_ids:
            return []
        rows = (s.query(Frame)
                .filter(Frame.session_id.in_(sess_ids), Frame.image_type == "LIGHT")
                .order_by(Frame.date_obs_utc.asc()).all())
        return [{"id": f.id, "date_obs_utc": f.date_obs_utc, "filter": f.filter_name}
                for f in rows]
    return db.query(_q)


def quality_timeseries(db: Db, *, target: str = "", session_id: int | None = None,
                       night: str = "", filt: str = "", show_raw: bool = False) -> list[dict]:
    """QualityMetric⨝Frame 시계열(시간순) — PP된 품질값으로 background/fwhm/별수 추이.
    기본은 calibrated=true만(PP 시리즈, 과학 유효). show_raw=true면 raw 포함(태깅). target은
    plan_id∪레거시 세션 매핑(_target_session_ids), session_id/night(YYYY-MM-DD)/filt 필터."""
    target = (target or "").strip()

    def _q(s):
        q = (s.query(Frame.id, Frame.date_obs_utc, Frame.filter_name, Frame.session_id,
                     QualityMetric.fwhm, QualityMetric.background_adu, QualityMetric.star_count,
                     QualityMetric.median_adu, QualityMetric.calibrated)
             .join(QualityMetric, QualityMetric.frame_id == Frame.id)
             .filter(Frame.image_type == "LIGHT"))
        if filt:
            q = q.filter(Frame.filter_name == filt)
        if session_id is not None:
            q = q.filter(Frame.session_id == int(session_id))
        if target:
            tgt = s.query(Target).filter(Target.name == target).first()
            plan_ids = ([p.id for p in s.query(ObservationPlan)
                         .filter(ObservationPlan.target_id == tgt.id).all()] if tgt else [])
            sess_ids = _target_session_ids(s, target, plan_ids)
            q = q.filter(Frame.session_id.in_(sess_ids or [-1]))
        out = []
        for fid, d, fn, sid, fwhm, bg, sc, med, cal in q.order_by(Frame.id.asc()).all():
            calb = bool(cal)
            if not show_raw and not calb:           # 기본 = PP 시리즈만(raw 제외)
                continue
            if night and (d or "")[:10] != night:
                continue
            out.append({"frame_id": fid, "date_obs_utc": d, "filter": fn, "session_id": sid,
                        "fwhm": fwhm, "background_adu": bg, "star_count": sc,
                        "median_adu": med, "calibrated": calb})
        return out
    return db.query(_q)


def quality_facets(db: Db) -> dict[str, list]:
    """품질 추이 드롭다운용 — 실제 촬영된 LIGHT 프레임에 존재하는 대상·필터만(안 찍은 건 제외).
    필터=LIGHT distinct filter_name, 대상=LIGHT 프레임 있는 세션→plan/target 또는 레거시 summary."""
    def _q(s):
        filters = sorted({r[0] for r in s.query(Frame.filter_name)
                          .filter(Frame.image_type == "LIGHT", Frame.filter_name != "")
                          .distinct().all()})
        sess_ids = {r[0] for r in s.query(Frame.session_id)
                    .filter(Frame.image_type == "LIGHT").distinct().all()}
        targets = set()
        for se in (s.query(ObservationSession)
                   .filter(ObservationSession.id.in_(sess_ids or [-1])).all()):
            nm = None
            if se.plan_id:
                pl = s.get(ObservationPlan, se.plan_id)
                if pl and pl.target_id:
                    tg = s.get(Target, pl.target_id)
                    nm = tg.name if tg else None
            if not nm:
                try:
                    nm = json.loads(se.summary_json or "{}").get("target")
                except Exception:
                    nm = None
            if nm:
                targets.add(nm)
        return {"targets": sorted(targets), "filters": filters}
    return db.query(_q)


def target_dossier(db: Db, name: str, lat: float = 36.64) -> dict[str, Any]:
    name = (name or "").strip()

    def _q(s):
        tgt = s.query(Target).filter(Target.name == name).first()
        plans, plan_ids = [], []
        if tgt:
            for p in (s.query(ObservationPlan)
                      .filter(ObservationPlan.target_id == tgt.id)
                      .order_by(ObservationPlan.id.desc()).all()):
                plan_ids.append(p.id)
                try:
                    params = json.loads(p.params_json or "{}")
                except Exception:
                    params = {}
                plans.append({
                    "id": p.id, "status": p.approval_status, "kind": p.kind,
                    "created_utc": p.created_utc, "filters": params.get("filters"),
                    "exposure_s": params.get("exposure_s"),
                    "count_per_filter": params.get("count_per_filter"),
                    "slot_start": params.get("slot_start"),
                    "slot_end": params.get("slot_end")})
        # 세션: plan_id 1급 엣지 ∪ 레거시 summary 문자열 매칭
        sess_ids = _target_session_ids(s, name, plan_ids)
        frames = []
        if sess_ids:
            frows = (s.query(Frame).filter(Frame.session_id.in_(sess_ids))
                     .order_by(Frame.id.desc()).all())
            fids = [f.id for f in frows]
            qms = {}
            if fids:
                for qm in (s.query(QualityMetric)
                           .filter(QualityMetric.frame_id.in_(fids)).all()):
                    qms[qm.frame_id] = qm
            for f in frows:
                qm = qms.get(f.id)
                frames.append({
                    "id": f.id, "image_type": f.image_type, "filter": f.filter_name,
                    "exposure_s": f.exposure_s, "date_obs_utc": f.date_obs_utc,
                    "median_adu": f.median_adu, "flag": f.flag,
                    "verdict": (qm.verdict if qm else None),
                    "saturation_frac": (qm.saturation_frac if qm else None)})
        tdict = (None if not tgt else {
            "id": tgt.id, "name": tgt.name, "ra_hours": tgt.ra_hours,
            "dec_degs": tgt.dec_degs, "type": tgt.type,
            "magnitude": tgt.magnitude, "notes": tgt.notes})
        return tdict, plans, frames

    tgt, plans, frames = db.query(_q)

    # 카탈로그 폴백(좌표/종류/등급) — DB에 없거나 좌표가 비었을 때
    cat = _catalog_find(name)
    ra = dec = typ = mag = None
    disp = name
    if tgt:
        ra, dec, typ, mag = tgt["ra_hours"], tgt["dec_degs"], tgt["type"], tgt["magnitude"]
        disp = tgt["name"]
    if cat:
        ra = ra if ra is not None else cat["ra"]
        dec = dec if dec is not None else cat["dec"]
        typ = typ or cat.get("t")
        mag = mag if mag is not None else cat.get("mag")

    lights = [f for f in frames if (f["image_type"] or "").upper() == "LIGHT"]
    integ_s = sum((f["exposure_s"] or 0) for f in lights)
    by_filter: dict[str, int] = {}
    for f in lights:
        by_filter[f["filter"] or "?"] = by_filter.get(f["filter"] or "?", 0) + 1
    bad = sum(1 for f in lights if (f["flag"] and f["flag"] != "ok")
              or (f["verdict"] and f["verdict"] not in ("", "ok")))

    transit = _transit_alt(lat, dec)
    observable = transit is not None and transit >= 30
    if not observable:
        rec = (f"청람에서 고도 부족(최고 {transit}°)" if transit is not None
               else "좌표 불명 — 관측 판단 불가")
    elif not lights:
        rec = "관측 추천 — 아직 LIGHT 프레임 없음"
    else:
        rec = "재촬영 검토 — 불량 LIGHT 있음" if bad else "추가 적분 가능"

    return {
        "name": disp,
        "in_db": tgt is not None,
        "target": {"ra_hours": ra, "dec_degs": dec, "type": typ,
                   "type_ko": TYPE_KO.get(typ, typ), "magnitude": mag,
                   "notes": (tgt["notes"] if tgt else "")},
        "visibility": {"transit_alt": transit, "observable": observable, "site_lat": lat},
        "requests": plans,
        # 상세 프레임은 최근 200개로 캡(페이로드 제한) — stats는 전체로 계산됨.
        "frames": frames[:200],
        "frames_truncated": len(frames) > 200,
        "stats": {"n_frames": len(frames), "n_lights": len(lights),
                  "integration_s": round(integ_s), "by_filter": by_filter,
                  "bad_lights": bad, "n_requests": len(plans)},
        "recommendation": rec,
    }


def list_targets(db: Db, lat: float = 36.64) -> list[dict[str, Any]]:
    """관측했거나 계획된 대상 요약 목록. 상세는 target_dossier(name)."""
    def _q(s):
        out = []
        for t in s.query(Target).order_by(Target.id.desc()).all():
            n_plans = (s.query(ObservationPlan)
                       .filter(ObservationPlan.target_id == t.id).count())
            out.append({
                "id": t.id, "name": t.name, "ra_hours": t.ra_hours,
                "dec_degs": t.dec_degs, "type": t.type,
                "type_ko": TYPE_KO.get(t.type, t.type), "magnitude": t.magnitude,
                "n_requests": n_plans, "transit_alt": _transit_alt(lat, t.dec_degs)})
        return out
    return db.query(_q)


def frame_provenance(db: Db, frame_id: int) -> dict | None:
    """한 프레임의 완전한 계보(왜·어떻게·어떤 조건에서 만들어졌나, 로드맵 §9.4). 기존 1급 엣지
    Target→Plan→Session→Frame + 품질·망원경상태·그 시각 기상·적용가능 보정·관련 결정을 묶는다."""
    def _q(s):
        fr = s.get(Frame, frame_id)
        if fr is None:
            return None
        sess = s.get(ObservationSession, fr.session_id) if fr.session_id else None
        plan = s.get(ObservationPlan, sess.plan_id) if (sess and sess.plan_id) else None
        tgt = s.get(Target, plan.target_id) if (plan and plan.target_id) else None
        qm = (s.query(QualityMetric).filter(QualityMetric.frame_id == frame_id)
              .order_by(QualityMetric.id.desc()).first())
        ts = (s.get(TelescopeState, fr.telescope_state_id)
              if fr.telescope_state_id else None)
        wx = (s.query(WeatherRecord)
              .filter(WeatherRecord.utc <= (fr.date_obs_utc or "~"))
              .order_by(WeatherRecord.utc.desc()).first())   # 그 시각 직전 기상
        cals = []
        for c in (s.query(CalibrationProduct)
                  .filter(CalibrationProduct.binning == fr.binning)
                  .order_by(CalibrationProduct.id.desc()).all()):
            if c.kind == "flat" and c.filter_name and c.filter_name != fr.filter_name:
                continue   # flat은 필터 일치만
            cals.append({"kind": c.kind, "filter": c.filter_name,
                         "exposure_s": c.exposure_s, "temp_c": c.temperature_c,
                         "n_frames": c.n_frames})
        dec = []
        if tgt and tgt.name:
            for d in (s.query(Decision)
                      .filter(Decision.evidence_json.contains(tgt.name))
                      .order_by(Decision.id.desc()).limit(5).all()):
                dec.append({"source": d.source, "recommendation": d.recommendation[:120],
                            "utc": d.utc})
        chain = " → ".join(x for x in [
            (tgt.name if tgt else None), (f"계획 #{plan.id}" if plan else None),
            (f"세션 #{sess.id}" if sess else None), f"프레임 #{fr.id}"] if x)
        return {
            "frame": {"id": fr.id, "image_type": fr.image_type, "filter": fr.filter_name,
                      "exposure_s": fr.exposure_s, "binning": fr.binning,
                      "date_obs_utc": fr.date_obs_utc, "median_adu": fr.median_adu,
                      "flag": fr.flag, "file_path": fr.file_path, "checksum": fr.checksum},
            "quality": (None if not qm else {
                "verdict": qm.verdict, "reason": qm.reason, "median_adu": qm.median_adu,
                "saturation_frac": qm.saturation_frac}),
            "telescope": (None if not ts else {
                "ra_hours": ts.ra_hours, "dec_degs": ts.dec_degs,
                "alt_degs": ts.alt_degs, "az_degs": ts.az_degs}),
            "session": (None if not sess else {
                "id": sess.id, "kind": sess.kind, "status": sess.status,
                "summary_json": sess.summary_json}),
            "plan": (None if not plan else {
                "id": plan.id, "status": plan.approval_status,
                "params_json": plan.params_json, "created_utc": plan.created_utc}),
            "target": (None if not tgt else {
                "name": tgt.name, "ra_hours": tgt.ra_hours, "dec_degs": tgt.dec_degs,
                "type": tgt.type, "type_ko": TYPE_KO.get(tgt.type, tgt.type),
                "magnitude": tgt.magnitude}),
            "weather": (None if not wx else {
                "utc": wx.utc, "temp_c": wx.temp_c, "humidity": wx.humidity,
                "wind_ms": wx.wind_ms, "cloud_score": wx.cloud_score, "rain": wx.rain,
                "source_id": wx.source_id}),
            "calibration_candidates": cals[:10],
            "decisions": dec,
            "lineage": chain,
        }
    return db.query(_q)
