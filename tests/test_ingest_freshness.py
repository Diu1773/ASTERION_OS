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


if __name__ == "__main__":
    unittest.main()
