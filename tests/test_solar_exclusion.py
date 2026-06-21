"""ephemeris 태양 회피 — 각이격 계산 + solar_exclusion_check.

핵심: 태양 좌표뿐 아니라 태양 중심 exclusion_deg 반경 안이면 차단(근처도 막힘),
야간 정상 대상은 이격이 커 통과(오탐 0), 좌표 부족은 fail-closed.
"""

import unittest

from asterion.core import ephemeris as E
from asterion.core.ephemeris import now_utc, sun_radec


class TestAngularSeparation(unittest.TestCase):
    def test_radec_same_point_zero(self):
        self.assertAlmostEqual(E.angular_separation_radec(5.0, 10.0, 5.0, 10.0), 0.0, places=4)

    def test_radec_opposite_ra_180(self):
        self.assertAlmostEqual(E.angular_separation_radec(0.0, 0.0, 12.0, 0.0), 180.0, places=4)

    def test_altaz_same_point_zero(self):
        self.assertAlmostEqual(E.angular_separation_altaz(80.0, 0.0, 80.0, 0.0), 0.0, places=4)

    def test_altaz_known_90(self):
        # 지평(alt0,az0)과 천정(alt90) 사이 = 90°
        self.assertAlmostEqual(E.angular_separation_altaz(0.0, 0.0, 90.0, 0.0), 90.0, places=4)


class TestSolarExclusion(unittest.TestCase):
    def setUp(self):
        self.dt = now_utc()
        self.sra, self.sdec = sun_radec(self.dt)

    def _check(self, dec_offset, excl=15.0):
        return E.solar_exclusion_check(exclusion_deg=excl, ra_hours=self.sra,
                                       dec_deg=self.sdec + dec_offset, dt_utc=self.dt)

    def test_at_sun_blocked(self):
        ok, sep, _ = self._check(0.0)
        self.assertFalse(ok)
        self.assertLess(sep, 1.0)

    def test_near_sun_10deg_blocked(self):
        ok, sep, _ = self._check(10.0)
        self.assertFalse(ok)
        self.assertAlmostEqual(sep, 10.0, places=1)

    def test_just_inside_14deg_blocked(self):
        self.assertFalse(self._check(14.0)[0])

    def test_just_outside_16deg_ok(self):
        self.assertTrue(self._check(16.0)[0])

    def test_far_target_ok(self):
        ok, sep, _ = E.solar_exclusion_check(
            exclusion_deg=15.0, ra_hours=(self.sra + 12.0) % 24.0,
            dec_deg=-self.sdec, dt_utc=self.dt)
        self.assertTrue(ok)
        self.assertGreater(sep, 90.0)

    def test_missing_coords_fail_closed(self):
        ok, _, _ = E.solar_exclusion_check(exclusion_deg=15.0)
        self.assertFalse(ok)

    def test_altaz_path_uses_lat_lon(self):
        # alt/az 경로는 lat/lon이 있어야 계산되고, 없으면 fail-closed.
        ok_missing, _, _ = E.solar_exclusion_check(exclusion_deg=15.0, alt_deg=80.0, az_deg=0.0)
        self.assertFalse(ok_missing)
        ok, _, _ = E.solar_exclusion_check(exclusion_deg=15.0, alt_deg=80.0, az_deg=0.0,
                                           lat_deg=36.6, lon_deg=127.5, dt_utc=self.dt)
        self.assertIsInstance(ok, bool)


if __name__ == "__main__":
    unittest.main()
