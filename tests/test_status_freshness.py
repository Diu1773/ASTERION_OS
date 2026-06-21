"""StatusSampler.current_safety — 스냅샷 신선도 게이트(메타 fail-closed).

샘플러 루프가 멈춰 스냅샷이 굳으면 마지막 SAFE가 영구 신뢰되는 메타 fail-open이
생긴다. ts_mono가 max_age를 넘으면 FAULT로 떨어뜨려 소비자가 멈추게 한다.
"""

import time
import unittest

from asterion.watchtower import safety as S
from asterion.watchtower.status import StatusSampler


def _sampler(snapshot, max_age=30.0):
    s = StatusSampler.__new__(StatusSampler)   # __init__ 우회 — 순수 메서드만 테스트
    s.snapshot = snapshot
    s._snapshot_max_age_s = max_age
    return s


class TestCurrentSafety(unittest.TestCase):
    def test_no_timestamp_returns_safety_as_is(self):
        # 첫 스냅샷 전 — state 없음(=소비자가 unsafe로 처리). 가짜 SAFE를 만들지 않는다.
        self.assertEqual(_sampler({}).current_safety(), {})

    def test_fresh_snapshot_passthrough(self):
        snap = {"ts_mono": time.monotonic(), "safety": {"state": S.OPEN_ALLOWED}}
        self.assertEqual(_sampler(snap).current_safety()["state"], S.OPEN_ALLOWED)

    def test_stale_snapshot_becomes_fault(self):
        snap = {"ts_mono": time.monotonic() - 100.0, "safety": {"state": S.OPEN_ALLOWED}}
        out = _sampler(snap).current_safety()
        self.assertEqual(out["state"], S.FAULT)
        self.assertTrue(out.get("stale_snapshot"))

    def test_just_under_max_age_still_passthrough(self):
        snap = {"ts_mono": time.monotonic() - 5.0, "safety": {"state": S.OBSERVING}}
        self.assertEqual(_sampler(snap, max_age=30.0).current_safety()["state"], S.OBSERVING)


if __name__ == "__main__":
    unittest.main()
