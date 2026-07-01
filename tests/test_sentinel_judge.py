"""Sentinel.judge_stored — 저장 지표(median+saturation_frac)만으로 판정(FITS 없음).

night_report가 200장 상한 없이 window 전체를 배치 집계할 때 쓰는 경로. 임계는 기본값
(sat_reject 0.02·sat_warn 0.005·median_low 1000·median_high 0.9×65535).
"""

import unittest

from asterion.analysis.sentinel import ACCEPTED, REJECTED, WARNING, Sentinel

from ._helpers import Cfg, tmp_db


class TestSentinelJudge(unittest.TestCase):
    def _s(self, **cfg):
        return Sentinel(Cfg(**cfg), tmp_db())

    def test_high_saturation_rejected(self):
        v, _ = self._s().judge_stored(20000, 0.03)      # 3% 과포화 ≥ 2%
        self.assertEqual(v, REJECTED)

    def test_normal_accepted(self):
        v, _ = self._s().judge_stored(20000, 0.0)
        self.assertEqual(v, ACCEPTED)

    def test_low_median_warning(self):
        v, r = self._s().judge_stored(500, 0.0)         # 노출 부족
        self.assertEqual(v, WARNING)
        self.assertIn("부족", r)

    def test_high_median_warning(self):
        v, _ = self._s().judge_stored(60000, 0.0)       # 과노출(≥ 0.9×sat)
        self.assertEqual(v, WARNING)

    def test_saturation_warn_band(self):
        v, _ = self._s().judge_stored(20000, 0.008)     # 0.5% ≤ 0.8% < 2%
        self.assertEqual(v, WARNING)

    def test_no_metrics_warning(self):
        v, r = self._s().judge_stored(None, None)
        self.assertEqual(v, WARNING)
        self.assertIn("없음", r)

    def test_custom_threshold(self):
        # 임계 config 반영 — reject 1%로 낮추면 1.5%가 rejected.
        s = self._s(**{"sentinel.saturation_reject_frac": 0.01})
        v, _ = s.judge_stored(20000, 0.015)
        self.assertEqual(v, REJECTED)


if __name__ == "__main__":
    unittest.main()
