"""피드백 학습 — 관측 결과를 읽어 '다음에 어떻게 찍을지' 추천을 낸다(차별화 A2).

규칙 기반(설명가능): 한 대상의 dossier(품질·불량률·필터편중)와 경량 측광(평균 SNR·포화)을
분석해 노출/적분/재촬영/필터 보강 추천을 만들고, 근거와 함께 `Decision`(source="feedback")으로
적재한다. plan_night(A3)이 이 추천의 노출 힌트를 소비해 적응형으로 계획한다.

analysis 계층 — core(skygraph/ontology) + 같은 계층 framedata에 의존.
"""

from __future__ import annotations

import json
from typing import Any

from ..core import skygraph
from ..core.ontology import Db, Decision
from .framedata import FrameData


def _exposure_hint(mean_snr: float | None, n_sat: int, floor: float) -> str:
    """다음 관측 노출 방향 — A3가 소비. decrease(포화) / increase(저SNR) / keep."""
    if n_sat > 0:
        return "decrease"
    if mean_snr is not None and mean_snr < floor:
        return "increase"
    return "keep"


def target_feedback(db: Db, name: str, snr_floor: float = 15.0,
                    bad_warn: float = 0.3, persist: bool = True) -> dict[str, Any]:
    """대상의 결과 → 추천. signals(불량률·평균SNR·포화·필터분포) + recommendations + 노출힌트."""
    name = (name or "").strip()
    dossier = skygraph.target_dossier(db, name)
    st = dossier.get("stats", {})
    vis = dossier.get("visibility", {})
    n_lights = int(st.get("n_lights", 0))
    bad = int(st.get("bad_lights", 0))
    by_filter = st.get("by_filter", {}) or {}
    bad_ratio = round(bad / n_lights, 2) if n_lights else 0.0

    # 경량 측광으로 평균 SNR·포화(점광원 가정)
    lc = FrameData(db).light_curve(skygraph.target_light_frames(db, name))
    snrs = [p["snr"] for p in lc if p.get("status") == "ok" and p.get("snr") is not None]
    mean_snr = round(sum(snrs) / len(snrs), 1) if snrs else None
    n_sat = sum(1 for p in lc if p.get("saturated"))

    recs: list[str] = []
    if not vis.get("observable"):
        recs.append(f"청람에서 고도 부족(최고 {vis.get('transit_alt')}°) — 후순위로.")
    if n_lights == 0:
        recs.append("아직 LIGHT 프레임 없음 — 첫 관측을 권장.")
    else:
        if n_sat > 0:
            recs.append(f"포화 프레임 {n_sat}개 — 노출/게인을 낮춰라.")
        if bad_ratio >= bad_warn:
            recs.append(f"불량률 {int(bad_ratio * 100)}% — 초점·추적 점검 후 재촬영.")
        if mean_snr is not None and mean_snr < snr_floor:
            recs.append(f"평균 SNR {mean_snr}(<{snr_floor}) — 적분(노출/장수)을 늘려라.")
        if by_filter and max(by_filter.values()) >= 3 * max(1, min(by_filter.values())):
            recs.append("필터별 장수 불균형 — 부족한 필터를 보강하라.")
        if not recs:
            recs.append("양호 — 현 설정 유지, 추가 적분 가능.")

    hint = _exposure_hint(mean_snr, n_sat, snr_floor)
    signals = {"n_lights": n_lights, "bad_ratio": bad_ratio, "mean_snr": mean_snr,
               "n_saturated": n_sat, "by_filter": by_filter,
               "observable": bool(vis.get("observable"))}
    summary = " ".join(recs)

    if persist and db is not None and n_lights > 0:
        db.add(Decision(
            source="feedback", recommendation=summary,
            evidence_json=json.dumps({"target": name, **signals, "exposure_hint": hint},
                                     ensure_ascii=False),
            confidence=0.6, approved_by="", outcome=""))

    return {"target": name, "signals": signals, "recommendations": recs,
            "exposure_hint": hint, "summary": summary}
