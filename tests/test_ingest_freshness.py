"""ingest.current_weather — fail-closed 신선도 + out-of-order backfill 정합성.

엣지 store-and-forward 에이전트가 재접속 후 옛 버퍼를 backfill하면 옛 record가 더 큰
id로 들어온다. '최신'을 id가 아니라 utc(관측시각)로 골라야 옛 record를 최신으로 오판하지
않는다. 평소엔 fresh를 정확히 반환하고, 모두 오래되면 None(→ fail-closed stale).
"""

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from asterion.core.ontology import Db, WeatherRecord
from asterion.watchtower.ingest import current_weather


def _iso(dt):
    return dt.isoformat()


class TestCurrentWeatherFreshness(unittest.TestCase):
    def setUp(self):
        self.db = Db(Path(tempfile.mkdtemp()) / "t.db")

    def test_returns_fresh_record(self):
        now = datetime.now(timezone.utc)
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(now), temp_c=12.3))
        rec = current_weather(self.db, max_age_s=120)
        self.assertIsNotNone(rec)
        self.assertAlmostEqual(rec["temp_c"], 12.3, places=3)
        self.assertLess(rec["age_s"], 5)

    def test_out_of_order_backfill_not_misjudged(self):
        now = datetime.now(timezone.utc)
        # 1) 신선 record 먼저(작은 id)
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(now), temp_c=20.0))
        # 2) 옛 record가 나중에 backfill (더 큰 id, 1시간 전 utc)
        self.db.add(WeatherRecord(source_id="pc1",
                                  utc=_iso(now - timedelta(hours=1)), temp_c=5.0))
        rec = current_weather(self.db, max_age_s=120)
        # id순이면 옛(1h前=stale)→None 오판. utc순이면 신선(20.0) 반환.
        self.assertIsNotNone(rec)
        self.assertAlmostEqual(rec["temp_c"], 20.0, places=3)

    def test_all_stale_returns_none(self):
        old = datetime.now(timezone.utc) - timedelta(hours=2)
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(old), temp_c=5.0))
        self.assertIsNone(current_weather(self.db, max_age_s=120))

    def test_no_remote_records_returns_none(self):
        self.assertIsNone(current_weather(self.db, max_age_s=120))

    def test_future_timestamp_rejected(self):
        # 원격 시계가 앞선 미래 record(age<0)는 신선으로 인정하지 않음(fail-closed).
        future = datetime.now(timezone.utc) + timedelta(minutes=10)
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(future), temp_c=9.0))
        self.assertIsNone(current_weather(self.db, max_age_s=120))


class TestWorstCaseAggregate(unittest.TestCase):
    """rank2 — 신선한 다중 소스의 worst-case 합성(fail-closed). 한 소스라도 위험이면 위험."""

    def setUp(self):
        self.db = Db(Path(tempfile.mkdtemp()) / "t.db")
        self.now = datetime.now(timezone.utc)

    def test_rain_any_source(self):
        # pc1=맑음, pc2=강수 동시 신선 → 합성 rain=True(최신 utc가 pc1이어도 pc2 강수 묵살 X).
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(self.now), rain=False))
        self.db.add(WeatherRecord(
            source_id="pc2", utc=_iso(self.now - timedelta(seconds=10)), rain=True))
        rec = current_weather(self.db, max_age_s=120)
        self.assertIsNotNone(rec)
        self.assertTrue(rec["rain"])
        self.assertEqual(rec["sources"], ["pc1", "pc2"])

    def test_wind_humidity_cloud_max(self):
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(self.now),
                                  wind_ms=5.0, humidity=50.0, cloud_score=0.1))
        self.db.add(WeatherRecord(source_id="pc2", utc=_iso(self.now),
                                  wind_ms=25.0, humidity=80.0, cloud_score=0.6))
        rec = current_weather(self.db, max_age_s=120)
        self.assertAlmostEqual(rec["wind_ms"], 25.0)
        self.assertAlmostEqual(rec["humidity"], 80.0)
        self.assertAlmostEqual(rec["cloud_score"], 0.6)

    def test_stale_dangerous_source_excluded(self):
        # pc2가 2시간 전 강수(stale=신뢰 불가)면 worst-case에서 제외 — 죽은 원격 에이전트가
        # 영영 관측을 막지 않는다. 신선한 pc1=맑음만 반영(rain=False), sources=[pc1].
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(self.now), rain=False))
        self.db.add(WeatherRecord(
            source_id="pc2", utc=_iso(self.now - timedelta(hours=2)), rain=True))
        rec = current_weather(self.db, max_age_s=120)
        self.assertIsNotNone(rec)
        self.assertFalse(rec["rain"])
        self.assertEqual(rec["sources"], ["pc1"])

    def test_age_is_freshest(self):
        self.db.add(WeatherRecord(
            source_id="pc1", utc=_iso(self.now - timedelta(seconds=90)), rain=False))
        self.db.add(WeatherRecord(source_id="pc2", utc=_iso(self.now), rain=False))
        rec = current_weather(self.db, max_age_s=120)
        self.assertLess(rec["age_s"], 10)   # 가장 신선한(pc2) 기준

    def test_multisource_backfill_not_false_stale(self):
        # rank19 — pc1 신선 record 후, 옛 record들을 대량 backfill(더 큰 id). id 윈도가 아니라
        # 소스별 max(utc)로 고르므로 신선 record가 윈도 밖으로 밀리지 않는다(false-stale X).
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(self.now), rain=False))
        for i in range(200):   # 옛 record 대량 backfill(큰 id, 과거 utc)
            self.db.add(WeatherRecord(
                source_id="pc1",
                utc=_iso(self.now - timedelta(hours=2, minutes=i)), rain=False))
        rec = current_weather(self.db, max_age_s=120)
        self.assertIsNotNone(rec)        # 신선한 now record가 살아있어야
        self.assertLess(rec["age_s"], 10)


if __name__ == "__main__":
    unittest.main()
