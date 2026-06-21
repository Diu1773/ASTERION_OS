"""/api/telemetry/persisted — 영속 다운샘플 텔레메트리 과거 시계열(내장 대시보드 출구).

핵심: 채널 목록 발견, 시간범위(hours) 필터, 관측시각 오름차순(out-of-order 안전).
auth/app.py(작업 중)와 격리하려 build_analysis_router만 최소 앱으로 마운트해 검증.
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from asterion.analysis.api import build_analysis_router
from asterion.analysis.framedata import FrameData
from asterion.core.ontology import Db, TelemetrySample


def _iso(dt):
    return dt.isoformat()


class TestTelemetryPersisted(unittest.TestCase):
    def setUp(self):
        self.db = Db(Path(tempfile.mkdtemp()) / "t.db")
        now = datetime.now(timezone.utc)
        # 일부러 시각 역순으로 삽입(최신 먼저) → id순 ≠ 시각순. utc 정렬이어야 올바름.
        self.db.add(TelemetrySample(channel="mount.alt", utc=_iso(now - timedelta(minutes=5)),
                                    vmin=40.0, vmean=45.0, vmax=50.0, n=60))
        self.db.add(TelemetrySample(channel="mount.alt", utc=_iso(now - timedelta(hours=10)),
                                    vmin=10.0, vmean=12.0, vmax=14.0, n=60))
        self.db.add(TelemetrySample(channel="mount.alt", utc=_iso(now - timedelta(days=10)),
                                    vmin=1.0, vmean=2.0, vmax=3.0, n=60))   # 24h 밖
        self.db.add(TelemetrySample(channel="weather.temp", utc=_iso(now - timedelta(minutes=3)),
                                    vmin=11.0, vmean=12.0, vmax=13.0, n=60))
        app = FastAPI()
        app.include_router(build_analysis_router(None, FrameData(self.db)))
        self.c = TestClient(app)

    def test_lists_channels_when_no_channel(self):
        r = self.c.get("/api/telemetry/persisted")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(set(r.json()["channels"]), {"mount.alt", "weather.temp"})

    def test_points_chronological_within_range(self):
        r = self.c.get("/api/telemetry/persisted", params={"channel": "mount.alt", "hours": 24})
        pts = r.json()["points"]
        self.assertEqual(len(pts), 2)                    # 24h 내 2개(10일前 제외)
        self.assertLess(pts[0]["t"], pts[1]["t"])        # 관측시각 오름차순
        self.assertAlmostEqual(pts[-1]["mean"], 45.0)    # 최신=5분前=45
        for k in ("min", "mean", "max", "n"):
            self.assertIn(k, pts[0])

    def test_range_excludes_old(self):
        r = self.c.get("/api/telemetry/persisted", params={"channel": "mount.alt", "hours": 1})
        pts = r.json()["points"]
        self.assertEqual(len(pts), 1)                    # 최근 5분前 1개만
        self.assertAlmostEqual(pts[0]["mean"], 45.0)

    def test_unknown_channel_empty(self):
        r = self.c.get("/api/telemetry/persisted", params={"channel": "nope", "hours": 24})
        self.assertEqual(r.json()["points"], [])

    def test_max_points_downsamples_preserving_band(self):
        # 60개 분버킷(1시간) → max_points=10이면 ≤10 버킷으로 다운샘플, min/max 밴드는 전역 보존.
        now = datetime.now(timezone.utc)
        for i in range(60):
            v = float(i)   # mean 0..59, 버킷별 min=max=v
            self.db.add(TelemetrySample(channel="dense",
                                        utc=_iso(now - timedelta(minutes=60 - i)),
                                        vmin=v, vmean=v, vmax=v, n=60))
        r = self.c.get("/api/telemetry/persisted",
                       params={"channel": "dense", "hours": 2, "max_points": 10})
        j = r.json()
        self.assertTrue(j["downsampled"])
        self.assertEqual(j["raw_points"], 60)
        self.assertLessEqual(len(j["points"]), 10)
        # 다운샘플돼도 전역 min(0)·max(59)는 어느 버킷에 보존돼야(밴드 안 잃음)
        self.assertAlmostEqual(min(p["min"] for p in j["points"]), 0.0)
        self.assertAlmostEqual(max(p["max"] for p in j["points"]), 59.0)
        # 시간 오름차순 유지
        ts = [p["t"] for p in j["points"]]
        self.assertEqual(ts, sorted(ts))


if __name__ == "__main__":
    unittest.main()
