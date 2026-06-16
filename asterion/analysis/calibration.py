"""Calibration Library — 보정 마스터 프레임 관리 (로드맵 §10.5).

bias/dark/flat 마스터를 등록·조회하고, 주어진 과학 프레임 조건에
가장 잘 맞는 보정 프레임을 골라준다(전처리 Forge가 쓸 기반). 초기에는
규칙 기반 매칭만; 'calibration 품질 추천'은 추후 확장 자리.

매칭 규칙(kind별):
  bias : binning 일치 → 최신
  dark : binning 일치 + |temp차| + |exposure차| 최소 → 최신
  flat : filter 일치 + binning 일치 → 최신
"""

from __future__ import annotations

from typing import Any

from ..core.ontology import CalibrationProduct, Db, row_to_dict

KINDS = ("bias", "dark", "flat")


class CalibrationLibrary:
    def __init__(self, db: Db):
        self.db = db

    def register(self, *, kind: str, file_path: str = "", filter_name: str = "",
                 temperature_c: float | None = None, exposure_s: float | None = None,
                 gain: float | None = None, binning: int = 1, n_frames: int = 0,
                 quality_score: float | None = None, notes: str = "") -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"kind는 {KINDS} 중 하나여야 함: {kind!r}")
        row = self.db.add(CalibrationProduct(
            kind=kind, file_path=file_path, filter_name=filter_name,
            temperature_c=temperature_c, exposure_s=exposure_s, gain=gain,
            binning=binning, n_frames=n_frames, quality_score=quality_score,
            notes=notes))
        return row_to_dict(row)

    def list_products(self, kind: str | None = None,
                      limit: int = 100) -> list[dict[str, Any]]:
        def _q(s):
            q = s.query(CalibrationProduct)
            if kind:
                q = q.filter(CalibrationProduct.kind == kind)
            rows = q.order_by(CalibrationProduct.id.desc()).limit(limit).all()
            return [row_to_dict(r) for r in rows]
        return self.db.query(_q)

    def find_match(self, *, kind: str, filter_name: str | None = None,
                   temperature_c: float | None = None,
                   exposure_s: float | None = None,
                   binning: int | None = None) -> dict[str, Any] | None:
        """조건에 가장 맞는 보정 프레임 1개(없으면 None). bias/flat은 정확 일치 후
        최신, dark는 temp·exposure 최근접."""
        if kind not in KINDS:
            raise ValueError(f"kind는 {KINDS} 중 하나여야 함: {kind!r}")
        cands = self.list_products(kind=kind, limit=1000)
        if binning is not None:
            cands = [c for c in cands if c["binning"] == binning]
        if kind == "flat" and filter_name:
            cands = [c for c in cands if c["filter_name"] == filter_name]
        if not cands:
            return None
        if kind == "dark":
            def _dist(c):
                dt = abs((c["temperature_c"] or 0.0) - (temperature_c or 0.0)) \
                    if temperature_c is not None else 0.0
                de = abs((c["exposure_s"] or 0.0) - (exposure_s or 0.0)) \
                    if exposure_s is not None else 0.0
                # exposure 차이를 더 크게 가중(보정 정확도에 직접 영향)
                return (de * 2.0 + dt, -c["id"])
            cands.sort(key=_dist)
            return cands[0]
        # bias/flat: list_products가 이미 id desc(최신순) → 첫 항목
        return cands[0]
