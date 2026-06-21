"""보존 정리 — 시뮬레이터 저장소를 일정 기간(기본 90일)으로 제한한다.

목적: sim 모드 데이터(별도 저장소 data/sim/)가 무한히 쌓이지 않게, 기준 시각보다
오래된 프레임 파일 + DB 행(세션·기상·액션·알림·텔레메트리·망원경상태·결정·포커스·보정·
품질)을 지운다. **실측 모드 데이터에는 절대 적용하지 않는다**(create_app이 sim일 때만 배선).

안전: 파일 삭제는 frames_dir 하위로 한정(경로 탈출 방지). 읽기/쓰기는 Db 트랜잭션을
쓰고, 한 번 실패해도 다음 스윕에서 다시 시도(추가형, 다른 흐름 무영향).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ontology import (
    ActionLog, Alert, CalibrationProduct, Decision, FocusRun, Frame,
    ObservationSession, QualityMetric, TelemetrySample, TelescopeState,
    WeatherRecord,
)

# (모델, 시각컬럼) — 이 컬럼이 cutoff(ISO)보다 작은 행을 정리한다. ISO8601 문자열은
# 사전식 비교가 시간순과 일치하므로 문자열 < 비교로 충분하다.
_TIME_TABLES: list[tuple[Any, str]] = [
    (Frame, "date_obs_utc"),
    (ObservationSession, "started_utc"),
    (WeatherRecord, "utc"),
    (ActionLog, "utc"),
    (Alert, "utc"),
    (TelescopeState, "utc"),
    (Decision, "utc"),
    (FocusRun, "utc"),
    (TelemetrySample, "utc"),
    (CalibrationProduct, "created_utc"),
]


class Retention:
    def __init__(self, db, frames_dir: Path, days: float = 90.0, events=None):
        self.db = db
        self.frames_dir = Path(frames_dir)
        self.days = float(days)
        self.events = events
        self.last_result: dict[str, Any] | None = None

    def _cutoff_iso(self, now: datetime | None = None) -> str:
        now = now or datetime.now(timezone.utc)
        return (now - timedelta(days=self.days)).isoformat(timespec="milliseconds")

    # ---------- 파일 정리(frames_dir 하위로 한정) ----------

    def _within_frames(self, raw: str) -> Path | None:
        if not raw:
            return None
        p = Path(raw)
        if not p.is_absolute():
            p = self.frames_dir / p
        try:
            p = p.resolve()
            root = self.frames_dir.resolve()
        except OSError:
            return None
        return p if (p == root or root in p.parents) else None

    def _delete_files(self, paths: list[str]) -> int:
        n = 0
        for raw in paths:
            p = self._within_frames(raw)
            if p is None:
                continue
            try:
                if p.exists():
                    p.unlink()
                    n += 1
            except OSError:
                pass
        return n

    def _prune_empty_day_dirs(self) -> None:
        if not self.frames_dir.exists():
            return
        for child in list(self.frames_dir.iterdir()):
            try:
                if child.is_dir() and not any(child.iterdir()):
                    child.rmdir()
            except OSError:
                pass

    # ---------- 메인 ----------

    def prune(self, now: datetime | None = None) -> dict[str, Any]:
        cutoff = self._cutoff_iso(now)
        counts: dict[str, int] = {}

        # 1) 오래된 프레임의 파일을 먼저 지운다(행 삭제 전에 경로를 읽어둔다).
        def _old_frame_paths(s):
            rows = (s.query(Frame.file_path)
                    .filter(Frame.file_path != "",
                            Frame.date_obs_utc < cutoff).all())
            return [r[0] for r in rows]
        counts["frame_files"] = self._delete_files(self.db.query(_old_frame_paths))

        # 2) 시각 컬럼 기준 행 정리 + 고아 품질지표(프레임 삭제로 떨어진 것).
        def _delete_rows(s):
            for model, attr in _TIME_TABLES:
                col = getattr(model, attr)
                counts[model.__tablename__] = (
                    s.query(model).filter(col < cutoff)
                    .delete(synchronize_session=False))
            sub = s.query(Frame.id)
            counts["quality_metric_orphan"] = (
                s.query(QualityMetric)
                .filter(QualityMetric.frame_id.notin_(sub))
                .delete(synchronize_session=False))
        self.db.update(_delete_rows)

        self._prune_empty_day_dirs()
        result = {"cutoff_utc": cutoff, "days": self.days,
                  "deleted": counts,
                  "total_rows": sum(v for k, v in counts.items()
                                    if k != "frame_files")}
        self.last_result = result
        if self.events is not None and (result["total_rows"] or counts["frame_files"]):
            self.events.log(
                "retention",
                f"시뮬 보존 정리 — {result['total_rows']}행 + "
                f"{counts['frame_files']}파일 삭제 ({self.days:.0f}일 경과분)")
        return result
