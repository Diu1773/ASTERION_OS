"""ObservationOrchestrator.start_plan — 시작 게이트 불변식.

핵심: 카메라 단일점유 대칭(F8), running()~_task 생성 사이 이중시작 경합 차단(F10),
태양 근접 대상 거부(solar). 모두 ActionBus 사전조건 실패 → ActionError.
"""

import tempfile
import unittest
from pathlib import Path

from asterion.core.actions import ActionError
from asterion.core.ephemeris import now_utc, sun_radec
from asterion.operation import meridian as M
from asterion.operation.orchestrator import ObservationOrchestrator

from ._helpers import Cfg, FakeDevice, new_bus, run, tmp_db


class _Meridian:
    def __init__(self, ra=1.0, dec=2.0):
        self.ra, self.dec = ra, dec

    def get_plan(self, pid):
        return {"id": pid, "approval_status": M.APPROVED,
                "target": {"name": "T", "ra_hours": self.ra, "dec_degs": self.dec},
                "params": {}}

    def set_status(self, *a, **k):
        pass


class _Events:
    def log(self, *a, **k):
        pass

    def status(self, *a, **k):
        pass

    def action(self, *a, **k):
        pass


def _make(meridian=None, occupancy_fn=None, cfg=None):
    db = tmp_db()
    return ObservationOrchestrator(
        cfg or Cfg(), {"camera": FakeDevice(), "mount": FakeDevice()},
        new_bus(db, _Events()), db, _Events(), meridian or _Meridian(),
        Path(tempfile.mkdtemp()),
        safety_fn=lambda: {"state": "OPEN_ALLOWED"}, occupancy_fn=occupancy_fn)


class TestStartPlanGates(unittest.TestCase):
    def test_occupancy_rejects(self):
        # F8 — 수동 캡처/오토플랫이 카메라 점유 중이면 시작 거부(단일점유 대칭).
        orch = _make(occupancy_fn=lambda: "수동 캡처 실행 중")
        with self.assertRaises(ActionError) as cm:
            run(orch.start_plan(1))
        self.assertIn("캡처", str(cm.exception))

    def test_launching_guard_rejects_reentrant(self):
        # F10 — _launching 임계영역: 진입 중 두 번째 호출 거부.
        orch = _make()
        orch._launching = True
        with self.assertRaises(ActionError) as cm:
            run(orch.start_plan(1))
        self.assertIn("이미", str(cm.exception))

    def test_sun_target_rejected(self):
        # solar — 대상이 현재 태양 좌표면 거부.
        sra, sdec = sun_radec(now_utc())
        orch = _make(meridian=_Meridian(ra=sra, dec=sdec))
        with self.assertRaises(ActionError) as cm:
            run(orch.start_plan(1))
        self.assertIn("태양", str(cm.exception))

    def test_sun_override_not_blocked_by_sun_reason(self):
        # allow_solar_slew(책임자 config)면 태양 사유로는 막히지 않는다. 다른 사전조건(점유)으로
        # 거부시켜 launch 없이 깔끔히 — 거부 사유에 '태양'이 없음을 확인.
        sra, sdec = sun_radec(now_utc())
        orch = _make(meridian=_Meridian(ra=sra, dec=sdec),
                     occupancy_fn=lambda: "수동 캡처 실행 중",
                     cfg=Cfg(**{"safety.allow_solar_slew": True}))
        with self.assertRaises(ActionError) as cm:
            run(orch.start_plan(1))
        self.assertNotIn("태양", str(cm.exception))
        self.assertIn("캡처", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
