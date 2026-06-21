"""시뮬레이터 저장소 보존 정리 회귀 (3개월 제한).

고정 시각으로 결정론적 검증: cutoff(90일)보다 오래된 프레임 파일+행은 지우고, 최근
것은 남기며, frames_dir 밖 파일은 절대 건드리지 않고, 고아 품질지표는 정리한다.

실행: 프로젝트 루트에서  python -m unittest tests.test_retention
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from asterion.core.ontology import (
    ActionLog, Alert, Db, Frame, ObservationSession, QualityMetric,
    TelemetrySample, WeatherRecord,
)
from asterion.core.retention import Retention

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=120)).isoformat(timespec="milliseconds")   # > 90일 → 삭제
NEW = (NOW - timedelta(days=10)).isoformat(timespec="milliseconds")    # < 90일 → 유지


class TestRetention(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.frames = self.tmp / "frames"
        self.db = Db(self.tmp / "asterion.db")

        def _frame_file(day: str, name: str) -> Path:
            d = self.frames / day
            d.mkdir(parents=True, exist_ok=True)
            p = d / name
            p.write_bytes(b"FITSDATA")
            return p

        self.old_file = _frame_file("20260222", "LIGHT_X_000000_001.fits")
        self.new_file = _frame_file("20260612", "LIGHT_X_000000_001.fits")
        # frames_dir 밖 파일 — 보존 정리가 절대 손대면 안 됨(경로 탈출 방지)
        self.outside = self.tmp / "outside.fits"
        self.outside.write_bytes(b"KEEP")

        self.old_frame = self.db.add(Frame(
            file_path=str(self.old_file), image_type="LIGHT", date_obs_utc=OLD))
        self.new_frame = self.db.add(Frame(
            file_path=str(self.new_file), image_type="LIGHT", date_obs_utc=NEW))
        self.escape_frame = self.db.add(Frame(
            file_path=str(self.outside), image_type="LIGHT", date_obs_utc=OLD))

        self.db.add(QualityMetric(frame_id=self.old_frame.id, verdict="ok"))
        self.db.add(QualityMetric(frame_id=self.new_frame.id, verdict="ok"))

        for ts in (OLD, NEW):
            self.db.add(WeatherRecord(utc=ts, temp_c=1.0))
            self.db.add(ActionLog(utc=ts, action_type="x", actor="t"))
            self.db.add(Alert(utc=ts, rule_id="r", level="warn", title="t"))
            self.db.add(ObservationSession(kind="capture", started_utc=ts))
            self.db.add(TelemetrySample(utc=ts, channel="mount.alt", vmean=1.0))

    def _count(self, model) -> int:
        return self.db.query(lambda s: s.query(model).count())

    def test_prune_old_keep_new(self):
        res = Retention(self.db, self.frames, days=90).prune(now=NOW)

        # 시각 기준 테이블: 오래된 1건 삭제, 최근 1건 유지
        for model in (WeatherRecord, ActionLog, Alert, ObservationSession,
                      TelemetrySample):
            self.assertEqual(self._count(model), 1, model.__tablename__)

        # 프레임: 오래된 2건(old_file + escape) 삭제, 최근 1건 유지
        self.assertEqual(self._count(Frame), 1)
        self.assertEqual(res["deleted"]["frame"], 2)

        # 파일: frames_dir 안의 오래된 것만 삭제, 최근/밖은 유지
        self.assertFalse(self.old_file.exists())
        self.assertTrue(self.new_file.exists())
        self.assertTrue(self.outside.exists(), "frames_dir 밖 파일은 보존돼야 함")
        self.assertEqual(res["deleted"]["frame_files"], 1)

        # 고아 품질지표(삭제된 프레임 참조)만 제거, 살아있는 프레임 것은 유지
        self.assertEqual(self._count(QualityMetric), 1)
        self.assertEqual(res["deleted"]["quality_metric_orphan"], 1)

        # 빈 날짜 폴더 정리
        self.assertFalse((self.frames / "20260222").exists())
        self.assertTrue((self.frames / "20260612").exists())

    def test_idempotent_second_sweep_noop(self):
        Retention(self.db, self.frames, days=90).prune(now=NOW)
        res2 = Retention(self.db, self.frames, days=90).prune(now=NOW)
        self.assertEqual(res2["total_rows"], 0)
        self.assertEqual(res2["deleted"]["frame_files"], 0)

    def test_zero_age_prunes_all_when_days_negative(self):
        # days를 크게 잡으면(미래 cutoff 아님) 아무것도 안 지운다 — 경계 확인
        res = Retention(self.db, self.frames, days=3650).prune(now=NOW)
        self.assertEqual(res["total_rows"], 0)
        self.assertEqual(self._count(Frame), 3)


if __name__ == "__main__":
    unittest.main()
