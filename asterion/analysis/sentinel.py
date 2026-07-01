"""Sentinel — 프레임 품질 평가 (로드맵 §10.4).

`evaluate(frame_id)` → {verdict, reason, metrics, recommended_action}.
지금은 *이미 적재된* 기본 지표(중앙값 ADU·과포화 비율)로 규칙 판정만 한다.
FWHM·star count·ML 분류 같은 무거운 분석은 metrics에 None placeholder로 자리를
잡아두고 추후 플러그인이 채운다 (인터페이스 안정). 임계는 config [sentinel].
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..core.ontology import Db, Frame, QualityMetric, row_to_dict

ACCEPTED = "accepted"
WARNING = "warning"
REJECTED = "rejected"


class Sentinel:
    def __init__(self, cfg: Config, db: Db):
        self.db = db
        g = cfg.get
        sat_adu = float(g("camera.saturation_adu", 65535))
        self.sat_reject = float(g("sentinel.saturation_reject_frac", 0.02))
        self.sat_warn = float(g("sentinel.saturation_warn_frac", 0.005))
        self.median_low = float(g("sentinel.median_low_adu", 1000.0))
        self.median_high = float(g("sentinel.median_high_frac", 0.9)) * sat_adu

    # ---------- 지표 조회 ----------

    def _qm_for(self, frame_id: int) -> dict[str, Any] | None:
        def _q(s):
            row = (s.query(QualityMetric)
                   .filter(QualityMetric.frame_id == frame_id)
                   .order_by(QualityMetric.id.desc()).first())
            return row_to_dict(row) if row else None
        return self.db.query(_q)

    # ---------- 판정 ----------

    def _judge(self, median: float | None,
               sat_frac: float | None) -> tuple[str, str, str]:
        if median is None and sat_frac is None:
            return WARNING, "품질 지표 없음 — 평가 불가", "통계 재계산 필요"
        sat = sat_frac or 0.0
        if sat >= self.sat_reject:
            return (REJECTED,
                    f"과포화 픽셀 {sat * 100:.1f}% (≥ {self.sat_reject * 100:.0f}%)",
                    "재촬영: 노출/게인 낮추기")
        if median is not None and median >= self.median_high:
            return (WARNING,
                    f"과노출 우려 (중앙값 {median:.0f} ≥ {self.median_high:.0f})",
                    "노출 단축 고려")
        if median is not None and median <= self.median_low:
            return (WARNING,
                    f"노출 부족 (중앙값 {median:.0f} ≤ {self.median_low:.0f})",
                    "노출 증가 고려")
        if sat >= self.sat_warn:
            return (WARNING, f"과포화 경고 {sat * 100:.1f}%", "노출 단축 검토")
        return ACCEPTED, "기본 지표 정상 범위", ""

    def judge_stored(self, median: float | None,
                     sat_frac: float | None) -> tuple[str, str]:
        """저장된 지표(median_adu·saturation_frac)만으로 판정 — FITS/재계산 없이 (verdict, reason).
        판정 규칙(_judge)은 이 둘만 쓰므로, night_report 등이 대량 프레임을 상한 없이 배치
        집계할 때 프레임당 evaluate() 대신 이걸 쓴다(FITS 0·프레임당 쿼리 0)."""
        verdict, reason, _ = self._judge(median, sat_frac)
        return verdict, reason

    def evaluate(self, frame_id: int) -> dict[str, Any] | None:
        """프레임 1장 품질 평가. 프레임이 없으면 None."""
        frame = self.db.get(Frame, frame_id)
        if frame is None:
            return None
        qm = self._qm_for(frame_id) or {}
        median = qm.get("median_adu")
        if median is None:
            median = frame.get("median_adu")
        std = qm.get("std_adu")
        if std is None:
            std = frame.get("std_adu")
        metrics = {
            "median_adu": median, "std_adu": std,
            "min_adu": qm.get("min_adu"), "max_adu": qm.get("max_adu"),
            "saturation_frac": qm.get("saturation_frac"),
            # 캡처 시 보정본에서 잰 값을 우선 사용(S4 영속). 보정 여부·하늘밝기도 노출.
            "fwhm": qm.get("fwhm"), "star_count": qm.get("star_count"),
            "background_adu": qm.get("background_adu"), "calibrated": qm.get("calibrated"),
        }
        # LIGHT인데 영속값이 없으면(레거시/백필) 별 검출로 채움 — 있으면 재계산 회피(점광원).
        if ((frame.get("image_type") or "").upper() == "LIGHT"
                and metrics["star_count"] is None):
            from .framedata import FrameData
            det = FrameData(self.db).detect_stars(frame_id)
            metrics["fwhm"] = det.get("fwhm")
            metrics["star_count"] = det.get("star_count")
            if metrics["background_adu"] is None:
                metrics["background_adu"] = det.get("bg")
        verdict, reason, action = self._judge(median, qm.get("saturation_frac"))
        return {
            "frame_id": frame_id, "verdict": verdict, "reason": reason,
            "recommended_action": action, "metrics": metrics,
            "image_type": frame.get("image_type"),
            "filter": frame.get("filter_name"),
            "file_path": frame.get("file_path"),
        }

    def evaluate_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for f in self.db.recent(Frame, limit):
            v = self.evaluate(f["id"])
            if v is not None:
                out.append(v)
        return out
