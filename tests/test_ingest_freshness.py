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


class TestCanonicalUtc(unittest.TestCase):
    """rank3/8 — timestamp를 정규 UTC로 저장해 사전식 utc 비교/MAX·dedup이 시간순과 일치."""

    def test_offsets_normalize_to_same_utc(self):
        from asterion.watchtower.ingest import _canonical_utc
        a = _canonical_utc("2026-06-24T10:00:00+09:00")   # = 01:00Z
        b = _canonical_utc("2026-06-24T01:00:00+00:00")
        self.assertEqual(a, b)                            # 같은 순간 → 같은 문자열(dedup 합쳐짐)

    def test_naive_treated_as_utc(self):
        from asterion.watchtower.ingest import _canonical_utc
        self.assertEqual(_canonical_utc("2026-06-24T01:00:00"),
                         _canonical_utc("2026-06-24T01:00:00+00:00"))

    def test_unparseable_kept_raw(self):
        from asterion.watchtower.ingest import _canonical_utc
        self.assertEqual(_canonical_utc("garbage"), "garbage")

    def test_to_record_stores_canonical(self):
        from asterion.watchtower.ingest import _to_record
        r = _to_record({"source_id": "pc1", "timestamp": "2026-06-24T10:00:00+09:00"})
        self.assertTrue(r["utc"].endswith("+00:00"))      # UTC로 변환 저장


class TestSourceDropout(unittest.TestCase):
    """rank6 — 최근 보고하던 소스가 침묵(dropout)하면 위험 마스킹 방지 위해 기본 fail-closed."""

    def setUp(self):
        self.db = Db(Path(tempfile.mkdtemp()) / "t.db")
        self.now = datetime.now(timezone.utc)

    def test_dropout_holds_fail_closed(self):
        # pc1=신선·맑음, pc2=300s 전(최근 보고 후 침묵, max_age 120<300<=window 600) → dropout.
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(self.now), rain=False))
        self.db.add(WeatherRecord(
            source_id="pc2", utc=_iso(self.now - timedelta(seconds=300)), rain=False))
        self.assertIsNone(current_weather(self.db, max_age_s=120))   # 기본 holds → None(HOLD)

    def test_dropout_disabled_returns_composite_with_stale_list(self):
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(self.now), rain=False))
        self.db.add(WeatherRecord(
            source_id="pc2", utc=_iso(self.now - timedelta(seconds=300)), rain=False))
        rec = current_weather(self.db, max_age_s=120, dropout_holds=False)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["sources"], ["pc1"])
        self.assertEqual(rec["stale_sources"], ["pc2"])

    def test_decommissioned_source_ignored(self):
        # pc2가 window(600s) 너머로 오래 침묵 → '폐기'로 보아 무시(영구 HOLD 방지).
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(self.now), rain=False))
        self.db.add(WeatherRecord(
            source_id="pc2", utc=_iso(self.now - timedelta(hours=2)), rain=True))
        rec = current_weather(self.db, max_age_s=120)
        self.assertIsNotNone(rec)              # dropout 아님 → 합성 반환
        self.assertEqual(rec["sources"], ["pc1"])
        self.assertFalse(rec["rain"])          # 폐기 pc2의 강수는 무시(신선 pc1만)

    def test_all_fresh_no_dropout(self):
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(self.now), rain=False))
        self.db.add(WeatherRecord(source_id="pc2", utc=_iso(self.now), rain=False))
        rec = current_weather(self.db, max_age_s=120)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["stale_sources"], [])

    def test_single_source_unaffected(self):
        # 단일 소스는 dropout 개념 없음 — 신선하면 그대로(기존 거동 보존).
        self.db.add(WeatherRecord(source_id="pc1", utc=_iso(self.now), rain=False))
        self.assertIsNotNone(current_weather(self.db, max_age_s=120))


if __name__ == "__main__":
    unittest.main()
