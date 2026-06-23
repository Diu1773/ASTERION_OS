"""ConnectionWatchdog — 샘플러 루프 생존 감시(rank8).

폐루프 안전(돔/태양/데드맨)이 1Hz 샘플러 루프에 얹혀 있어 루프가 죽으면 함께 죽는 SPOF를,
*독립 루프*인 ConnectionWatchdog가 스냅샷 ts_mono 나이로 감지·경보한다.
"""

import time
import unittest

from asterion.watchtower.recovery import ConnectionWatchdog

from ._helpers import Cfg


class _Ev:
    def __init__(self):
        self.logs = []

    def log(self, *a, **k):
        self.logs.append(a)


def _wd(alerts, **cfg):
    return ConnectionWatchdog(
        Cfg(**cfg), conn=object(), sampler=object(), events=_Ev(),
        alert_fn=lambda t, d: alerts.append((t, d)))


class TestSamplerStall(unittest.TestCase):
    def test_stall_fires_alert_once_then_rearms(self):
        alerts = []
        wd = _wd(alerts, **{"drivers.sampler_stall_seconds": 15.0})
        wd._check_sampler_stall({"ts_mono": time.monotonic() - 20.0})   # 20s 갱신 없음 → stall
        self.assertEqual(len(alerts), 1)
        self.assertTrue(wd._stall_alarmed)
        wd._check_sampler_stall({"ts_mono": time.monotonic() - 20.0})   # 지속 → 디바운스
        self.assertEqual(len(alerts), 1)
        wd._check_sampler_stall({"ts_mono": time.monotonic()})          # 신선 회복 → 재무장
        self.assertFalse(wd._stall_alarmed)

    def test_fresh_snapshot_no_alert(self):
        alerts = []
        wd = _wd(alerts)
        wd._check_sampler_stall({"ts_mono": time.monotonic()})
        self.assertEqual(alerts, [])

    def test_no_ts_mono_no_alert(self):
        # 첫 스냅샷 전(ts_mono 없음) → 오경보 금지.
        alerts = []
        wd = _wd(alerts)
        wd._check_sampler_stall({})
        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
