"""ForecastWatch — 강수 예보 조기경보(선제 '경고'만, 물리행동 0).

핵심: 향후 lead_hours 내 예보 강수확률 최대치가 임계 이상이면 alert 한 건. 돔/마운트
물리행동은 전혀 없다(실제 닫기는 센서 감지가 한다). off / 임계미만 / 창밖이면 무발화.
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from asterion.watchtower.forecast_watch import ForecastWatch

from ._helpers import Cfg


class FakeAlert:
    def __init__(self):
        self.fired = []

    def fire(self, rule_id, level, title, detail="", state="", cooldown_s=0.0):
        rec = {"rule_id": rule_id, "level": level, "title": title,
               "detail": detail, "cooldown_s": cooldown_s}
        self.fired.append(rec)
        return rec


class FakeForecast:
    """points: [(앞으로 몇 시간, 강수확률 0..1), ...]"""
    def __init__(self, points):
        self.points = points

    def upcoming(self, hours):
        now = datetime.now(timezone.utc)
        return [SimpleNamespace(
            time_utc=(now + timedelta(hours=off)).isoformat(),
            precip_prob=p) for off, p in self.points]


def _cfg(**over):
    base = {"weather.forecast_alert.enabled": True,
            "weather.forecast_alert.precip_threshold": 0.5,
            "weather.forecast_alert.lead_hours": 2.0,
            "weather.forecast_alert.cooldown_seconds": 1800.0}
    base.update(over)
    return Cfg(**base)


class TestForecastWatch(unittest.TestCase):
    def _make(self, points, **over):
        self.alert = FakeAlert()
        return ForecastWatch(FakeForecast(points), self.alert, _cfg(**over))

    def test_high_precip_in_window_fires(self):
        wd = self._make([(1.0, 0.8)])              # 1시간 뒤 강수확률 80%
        rec = wd.check()
        self.assertIsNotNone(rec)
        self.assertEqual(len(self.alert.fired), 1)
        self.assertEqual(self.alert.fired[0]["rule_id"], "weather_forecast_rain")
        self.assertEqual(self.alert.fired[0]["level"], "warn")

    def test_low_precip_no_fire(self):
        wd = self._make([(1.0, 0.2)])              # 20% < 임계 50%
        self.assertIsNone(wd.check())
        self.assertEqual(len(self.alert.fired), 0)

    def test_threshold_boundary_fires(self):
        wd = self._make([(1.0, 0.5)])              # 정확히 임계(>= 발화)
        self.assertIsNotNone(wd.check())
        self.assertEqual(len(self.alert.fired), 1)

    def test_high_precip_outside_lead_window_no_fire(self):
        wd = self._make([(5.0, 0.9)])              # 5시간 뒤 — lead 2h 창 밖
        self.assertIsNone(wd.check())
        self.assertEqual(len(self.alert.fired), 0)

    def test_disabled_no_fire(self):
        wd = self._make([(1.0, 0.9)],
                        **{"weather.forecast_alert.enabled": False})
        self.assertIsNone(wd.check())
        self.assertEqual(len(self.alert.fired), 0)

    def test_peak_picks_max_within_window(self):
        # 창 안 여러 점 중 최대치를 고른다(창 밖 0.95는 무시).
        wd = self._make([(0.5, 0.3), (1.5, 0.7), (5.0, 0.95)])
        peak, _at = wd.peak_risk(2.0)
        self.assertAlmostEqual(peak, 0.7, places=3)

    def test_no_physical_action_only_alert(self):
        # 가드: ForecastWatch는 drivers/bus를 갖지 않는다 — 물리행동 경로가 없다.
        wd = self._make([(1.0, 0.9)])
        wd.check()
        self.assertFalse(hasattr(wd, "drivers"))
        self.assertFalse(hasattr(wd, "bus"))

    # ---- should_defer_exposure (예보→긴 노출 보류, 되돌릴 수 있는 선제) ----

    def test_defer_long_exposure_high_precip(self):
        # 긴 노출(300s) + 노출 동안 강수확률 높음 → 시작 보류(True).
        wd = self._make([(0.0, 0.8)])
        self.assertTrue(wd.should_defer_exposure(300.0))

    def test_no_defer_short_exposure(self):
        # 짧은 노출(30s < defer_min 60)은 강수확률 높아도 그냥 진행(False) — 위험 낮음.
        wd = self._make([(0.0, 0.9)])
        self.assertFalse(wd.should_defer_exposure(30.0))

    def test_no_defer_low_precip(self):
        # 긴 노출이라도 강수확률 낮으면 진행(False).
        wd = self._make([(0.0, 0.2)])
        self.assertFalse(wd.should_defer_exposure(300.0))

    def test_defer_disabled_never_defers(self):
        # defer_exposures=False면 긴 노출+고강수여도 안 보류(False).
        wd = self._make([(0.0, 0.9)],
                        **{"weather.forecast_alert.defer_exposures": False})
        self.assertFalse(wd.should_defer_exposure(300.0))


if __name__ == "__main__":
    unittest.main()
