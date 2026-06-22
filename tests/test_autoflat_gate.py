"""AutoFlatRunner 안전 게이트 — fail-OPEN 회귀 가드.

rank1 발견: autoflat이 안전 스냅샷을 전혀 소비하지 않아 WEATHER_HOLD/EMERGENCY_CLOSE/
FAULT/주간(SAFE_CLOSED) 중에도 시작·가대 슬루·노출이 가능했다(위험을 능동 생성하는 유일
fail-OPEN). orchestrator/night_runner와 동형으로 safety_fn 주입 + start safety_ok 사전조건
+ 진행 중 _safety_gate(슬루/노출 전)를 추가했고, 그 불변식을 고정한다.
"""

import tempfile
import unittest
from pathlib import Path

from asterion.core.actions import ActionError
from asterion.skyflat.autoflat import AutoFlatParams, AutoFlatRunner
from asterion.watchtower import safety as S

from ._helpers import Cfg, FakeDevice, new_bus, run, tmp_db


class _Events:
    def log(self, *a, **k):
        pass

    def status(self, *a, **k):
        pass

    def action(self, *a, **k):
        pass

    def frame(self, *a, **k):
        pass


class _Tw:
    enabled = True   # 박명창 우회 — 안전 precondition만 분리 검증


def _make(safety_state=None, safety_fn="default", drivers=None, cfg=None):
    db = tmp_db()
    if safety_fn == "default":
        safety_fn = None if safety_state is None else (lambda: {"state": safety_state})
    drivers = drivers or {"camera": FakeDevice(), "mount": FakeDevice()}
    return AutoFlatRunner(
        cfg or Cfg(), drivers, new_bus(db, _Events()), db, _Events(),
        _Tw(), lambda: -3.0, Path(tempfile.mkdtemp()),
        safety_fn=safety_fn)


class TestAutoFlatStartGate(unittest.TestCase):
    def test_weather_hold_blocks_start(self):
        r = _make(safety_state=S.WEATHER_HOLD)
        with self.assertRaises(ActionError) as cm:
            run(r.start(AutoFlatParams()))
        self.assertIn("안전", str(cm.exception))
        self.assertFalse(r.running())

    def test_emergency_close_blocks_start(self):
        r = _make(safety_state=S.EMERGENCY_CLOSE)
        with self.assertRaises(ActionError) as cm:
            run(r.start(AutoFlatParams()))
        self.assertIn("안전", str(cm.exception))

    def test_daytime_safe_closed_blocks_start(self):
        r = _make(safety_state=S.SAFE_CLOSED)
        with self.assertRaises(ActionError) as cm:
            run(r.start(AutoFlatParams()))
        self.assertIn("안전", str(cm.exception))

    def test_fault_blocks_start(self):
        r = _make(safety_state=S.FAULT)
        with self.assertRaises(ActionError) as cm:
            run(r.start(AutoFlatParams()))
        self.assertIn("안전", str(cm.exception))

    def test_safe_state_not_blocked_by_safety(self):
        # OPEN_ALLOWED면 안전 사유로는 막히지 않는다. 다른 precondition(카메라 미연결)으로
        # 거부시켜 launch 없이 — 거부 사유에 '안전'이 없음을 확인(safe precond 통과 증명).
        r = _make(safety_state=S.OPEN_ALLOWED,
                  drivers={"camera": FakeDevice(connected=False),
                           "mount": FakeDevice()})
        with self.assertRaises(ActionError) as cm:
            run(r.start(AutoFlatParams()))
        self.assertNotIn("안전", str(cm.exception))
        self.assertIn("카메라", str(cm.exception))

    def test_no_safety_fn_no_safety_block(self):
        # safety_fn=None(드라이버 직접 테스트)이면 안전 게이트 비활성 — 카메라 미연결로 거부되며
        # 사유에 '안전' 없음(None이 안전 precondition을 추가하지 않음).
        r = _make(safety_fn=None,
                  drivers={"camera": FakeDevice(connected=False),
                           "mount": FakeDevice()})
        with self.assertRaises(ActionError) as cm:
            run(r.start(AutoFlatParams()))
        self.assertNotIn("안전", str(cm.exception))


class TestAutoFlatSafetyGate(unittest.TestCase):
    def test_gate_passes_when_safe(self):
        r = _make(safety_state=S.OPEN_ALLOWED)
        run(r._safety_gate("x"))   # 예외 없이 통과

    def test_gate_noop_without_safety_fn(self):
        r = _make(safety_fn=None)
        run(r._safety_gate("x"))   # safety_fn None → 즉시 반환

    def test_gate_aborts_when_unsafe_persists(self):
        # 회복 안 되는 unsafe → _max_pause_s 경과 후 ActionError(fail-closed 중단).
        r = _make(safety_state=S.EMERGENCY_CLOSE,
                  cfg=Cfg(**{"safety.observe_max_pause_seconds": 0.0}))
        with self.assertRaises(ActionError) as cm:
            run(r._safety_gate("노출"))
        self.assertIn("미회복", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
