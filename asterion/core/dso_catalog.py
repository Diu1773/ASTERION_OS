"""AI 야간계획(plan_night)용 DSO 카탈로그 — 유명 대상 큐레이션.

프론트 web/catalog.js(SKY_CATALOG)와 별개로 백엔드가 서버측에서 쓰는 데이터.
(향후 단일 소스로 통합 권장 — 지금은 정적이라 중복 허용.) ra=시간(h), dec=도,
mag=겉보기등급, t=종류(gx 은하·gc 구상성단·oc 산개성단·pn 행성상성운·neb 성운·snr 잔해).
"""

from __future__ import annotations

DSO: list[dict] = [
    {"id": "M31", "name": "안드로메다 은하", "ra": 0.712, "dec": 41.27, "mag": 3.4, "t": "gx"},
    {"id": "M33", "name": "삼각형자리 은하", "ra": 1.564, "dec": 30.66, "mag": 5.7, "t": "gx"},
    {"id": "M81", "name": "보데 은하", "ra": 9.926, "dec": 69.07, "mag": 6.9, "t": "gx"},
    {"id": "M82", "name": "시가 은하", "ra": 9.931, "dec": 69.68, "mag": 8.4, "t": "gx"},
    {"id": "M51", "name": "소용돌이 은하", "ra": 13.498, "dec": 47.20, "mag": 8.4, "t": "gx"},
    {"id": "M101", "name": "바람개비 은하", "ra": 14.053, "dec": 54.35, "mag": 7.9, "t": "gx"},
    {"id": "M63", "name": "해바라기 은하", "ra": 13.264, "dec": 42.03, "mag": 8.6, "t": "gx"},
    {"id": "M64", "name": "검은눈 은하", "ra": 12.945, "dec": 21.68, "mag": 8.5, "t": "gx"},
    {"id": "M104", "name": "솜브레로 은하", "ra": 12.667, "dec": -11.62, "mag": 8.0, "t": "gx"},
    {"id": "M106", "name": "", "ra": 12.316, "dec": 47.30, "mag": 8.4, "t": "gx"},
    {"id": "NGC253", "name": "조각가자리 은하", "ra": 0.793, "dec": -25.29, "mag": 7.1, "t": "gx"},
    {"id": "NGC891", "name": "", "ra": 2.376, "dec": 42.35, "mag": 9.9, "t": "gx"},
    {"id": "M42", "name": "오리온 대성운", "ra": 5.591, "dec": -5.39, "mag": 4.0, "t": "neb"},
    {"id": "M8", "name": "석호성운", "ra": 18.060, "dec": -24.38, "mag": 6.0, "t": "neb"},
    {"id": "M16", "name": "독수리성운", "ra": 18.313, "dec": -13.79, "mag": 6.0, "t": "neb"},
    {"id": "M17", "name": "오메가성운", "ra": 18.346, "dec": -16.18, "mag": 6.0, "t": "neb"},
    {"id": "M20", "name": "삼렬성운", "ra": 18.045, "dec": -23.03, "mag": 6.3, "t": "neb"},
    {"id": "NGC7000", "name": "북아메리카성운", "ra": 20.983, "dec": 44.52, "mag": 4.0, "t": "neb"},
    {"id": "M27", "name": "아령성운", "ra": 19.994, "dec": 22.72, "mag": 7.4, "t": "pn"},
    {"id": "M57", "name": "고리성운", "ra": 18.885, "dec": 33.03, "mag": 8.8, "t": "pn"},
    {"id": "M97", "name": "올빼미성운", "ra": 11.247, "dec": 55.02, "mag": 9.9, "t": "pn"},
    {"id": "M13", "name": "헤르쿨레스 대성단", "ra": 16.695, "dec": 36.46, "mag": 5.8, "t": "gc"},
    {"id": "M92", "name": "", "ra": 17.285, "dec": 43.14, "mag": 6.4, "t": "gc"},
    {"id": "M5", "name": "", "ra": 15.310, "dec": 2.08, "mag": 5.6, "t": "gc"},
    {"id": "M3", "name": "", "ra": 13.703, "dec": 28.38, "mag": 6.2, "t": "gc"},
    {"id": "M15", "name": "", "ra": 21.500, "dec": 12.17, "mag": 6.2, "t": "gc"},
    {"id": "M45", "name": "플레이아데스", "ra": 3.790, "dec": 24.12, "mag": 1.6, "t": "oc"},
    {"id": "M11", "name": "야생오리성단", "ra": 18.851, "dec": -6.27, "mag": 6.3, "t": "oc"},
    {"id": "M44", "name": "벌집성단", "ra": 8.670, "dec": 19.67, "mag": 3.7, "t": "oc"},
    {"id": "M1", "name": "게성운", "ra": 5.575, "dec": 22.02, "mag": 8.4, "t": "snr"},
]

TYPE_KO = {"gx": "은하", "gc": "구상성단", "oc": "산개성단",
           "pn": "행성상성운", "neb": "성운", "snr": "초신성잔해"}


def match_type(q: str | None) -> list[str] | None:
    """자연어 종류 질의 → 종류코드 목록. None이면 전체."""
    s = (q or "").strip().lower()
    if not s or s in ("all", "전체", "아무거나", "any"):
        return None
    if "은하" in s or "galaxy" in s:
        return ["gx"]
    if "행성상" in s or "planetary" in s:
        return ["pn"]
    if "성운" in s or "nebula" in s:
        return ["neb", "pn"]
    if "구상" in s or "globular" in s:
        return ["gc"]
    if "산개" in s or "open" in s:
        return ["oc"]
    if "성단" in s or "cluster" in s:
        return ["gc", "oc"]
    return None
