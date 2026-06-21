"""ToolKit (AI 에이전트) — 실행계 안전/태양 게이트.

핵심: AI는 ActionBus를 통과하고 안전게이트를 상속한다. 태양 본체·근방 슬루 거부,
악천후/stale엔 돔 개방 거부(닫기는 안전방향이라 허용). AI엔 force가 없어 책임자
config(allow_solar_slew)만 우회 가능.
"""

import unittest

from asterion.agent.toolkit import ToolKit
from asterion.core.ephemeris import now_utc, sun_radec

from ._helpers import Cfg, new_bus, run, tmp_db


class _DomeStatus:
    can_command_shutter = True


class FakeDome:
    def __init__(self):
        self.opened = False
        self.closed = False

    def status(self):
        return _DomeStatus()

    def open_shutter(self):
        self.opened = True

    def close_shutter(self):
        self.closed = True


class FakeMount:
    def goto_radec(self, ra, dec):
        self.last = (ra, dec)


def _toolkit(snapshot, cfg=None, dome=None, mount=None):
    db = tmp_db()
    return ToolKit(cfg=cfg or Cfg(), snapshot_fn=lambda: snapshot, meridian=None,
                   orchestrator=None, bus=new_bus(db), db=db,
                   drivers={"mount": mount or FakeMount(), "dome": dome or FakeDome()})


class TestAgentSafetyGate(unittest.TestCase):
    def test_goto_sun_refused(self):
        tk = _toolkit({"safety": {"state": "OPEN_ALLOWED"}})
        r = run(tk._t_goto_planet({"name": "sun"}))
        self.assertFalse(r["ok"])
        self.assertIn("태양", r["reason"])

    def test_dome_open_blocked_when_unsafe(self):
        dome = FakeDome()
        tk = _toolkit({"safety": {"state": "WEATHER_HOLD"}}, dome=dome)
        r = run(tk._t_dome_shutter({"open": True}))
        self.assertFalse(r["ok"])
        self.assertFalse(dome.opened)

    def test_dome_close_allowed_when_unsafe(self):
        # 닫기는 안전 방향 — 무조건 허용(fail-closed).
        dome = FakeDome()
        tk = _toolkit({"safety": {"state": "WEATHER_HOLD"}}, dome=dome)
        r = run(tk._t_dome_shutter({"open": False}))
        self.assertTrue(r["ok"])
        self.assertTrue(dome.closed)

    def test_dome_open_allowed_when_safe(self):
        dome = FakeDome()
        tk = _toolkit({"safety": {"state": "OPEN_ALLOWED"}}, dome=dome)
        r = run(tk._t_dome_shutter({"open": True}))
        self.assertTrue(r["ok"])
        self.assertTrue(dome.opened)


class TestAgentSolarGate(unittest.TestCase):
    def setUp(self):
        self.sra, self.sdec = sun_radec(now_utc())

    def test_sun_sep_preconds_blocks_sun(self):
        tk = _toolkit({})
        pc = tk._sun_sep_preconds(self.sra, self.sdec)
        self.assertEqual(pc[0][0], "sun_sep_ok")
        self.assertFalse(pc[0][1])

    def test_sun_sep_preconds_allows_override(self):
        tk = _toolkit({}, cfg=Cfg(**{"safety.allow_solar_slew": True}))
        self.assertTrue(tk._sun_sep_preconds(self.sra, self.sdec)[0][1])

    def test_sun_sep_preconds_far_target_ok(self):
        tk = _toolkit({})
        far_ra = (self.sra + 12.0) % 24.0
        self.assertTrue(tk._sun_sep_preconds(far_ra, -self.sdec)[0][1])


if __name__ == "__main__":
    unittest.main()
