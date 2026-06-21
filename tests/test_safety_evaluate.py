"""watchtower.safety.evaluate — fail-closed 안전 판정 불변식.

핵심: "기상 데이터 없음/stale = unsafe"(로드맵 원칙 #5), 주간 보호가 세션에 마스킹되지
않음(F11), 강수/강풍 EMERGENCY, 임계 단조성.
"""

import unittest

from asterion.watchtower import safety as S

GOOD = {"rain": False, "wind": 0.0, "humidity": 40.0, "cloud": 0.1}


def ev(**kw):
    base = dict(missing_required=[], weather=GOOD, sun_alt=-20.0,
                session_running=False, weather_age_s=1.0)
    base.update(kw)
    return S.evaluate(**base)


class TestFailClosed(unittest.TestCase):
    def test_no_weather_data_is_weather_hold(self):
        r = ev(weather=None, weather_age_s=None)
        self.assertEqual(r["state"], S.WEATHER_HOLD)
        self.assertTrue(r.get("weather_stale"))

    def test_stale_weather_is_weather_hold(self):
        r = ev(weather_age_s=999.0)   # >= unsafe(120)
        self.assertEqual(r["state"], S.WEATHER_HOLD)
        self.assertTrue(r.get("weather_stale"))

    def test_stale_gate_precedes_emergency(self):
        # 데이터가 stale이면 (결측→0인) 강수/강풍 값보다 stale 게이트가 먼저 닫는다.
        r = ev(weather={"rain": True, "wind": 99.0}, weather_age_s=None)
        self.assertEqual(r["state"], S.WEATHER_HOLD)

    def test_warn_age_flags_warning_but_operates(self):
        r = ev(weather_age_s=60.0)   # warn(30) <= age < unsafe(120)
        self.assertTrue(r.get("weather_warn"))
        self.assertNotEqual(r["state"], S.WEATHER_HOLD)

    def test_monotonic_more_stale_never_safer(self):
        # 지연이 길수록 안전도가 역전(더 안전)되면 안 된다.
        unsafe_states = {S.WEATHER_HOLD, S.EMERGENCY_CLOSE, S.FAULT}
        self.assertIn(ev(weather_age_s=None)["state"], unsafe_states)
        self.assertIn(ev(weather_age_s=130.0)["state"], unsafe_states)


class TestHazards(unittest.TestCase):
    def test_missing_required_is_fault(self):
        self.assertEqual(ev(missing_required=["마운트"])["state"], S.FAULT)

    def test_rain_emergency_close(self):
        self.assertEqual(ev(weather={"rain": True})["state"], S.EMERGENCY_CLOSE)

    def test_high_wind_emergency_close(self):
        self.assertEqual(ev(weather={"wind": 25.0})["state"], S.EMERGENCY_CLOSE)

    def test_high_humidity_weather_hold(self):
        self.assertEqual(ev(weather={"humidity": 95.0})["state"], S.WEATHER_HOLD)

    def test_high_cloud_weather_hold(self):
        self.assertEqual(ev(weather={"cloud": 0.9})["state"], S.WEATHER_HOLD)


class TestDaytimeAndSession(unittest.TestCase):
    def test_daytime_session_is_safe_closed_not_observing(self):
        # F11: 주간(태양>-0.5)이면 세션 실행 중이어도 OBSERVING으로 자기마스킹되지 않는다.
        r = ev(sun_alt=10.0, session_running=True)
        self.assertEqual(r["state"], S.SAFE_CLOSED)

    def test_night_session_is_observing(self):
        r = ev(sun_alt=-20.0, session_running=True)
        self.assertEqual(r["state"], S.OBSERVING)

    def test_twilight_session_is_observing(self):
        r = ev(sun_alt=-3.0, session_running=True)
        self.assertEqual(r["state"], S.OBSERVING)

    def test_daytime_no_session_is_safe_closed(self):
        self.assertEqual(ev(sun_alt=10.0)["state"], S.SAFE_CLOSED)

    def test_clear_night_is_open_allowed(self):
        self.assertEqual(ev(sun_alt=-20.0)["state"], S.OPEN_ALLOWED)


if __name__ == "__main__":
    unittest.main()
