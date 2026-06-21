"""DomeGuard — 비상 자동닫힘 액추에이터(fail-closed actuation).

핵심: 셔터가 'open'뿐 아니라 'opening'/'unknown'/'error'(닫힘 미확증)여도 닫고(F2),
닫기가 실패하면 다음 틱에 재시도해 성공까지 수렴한다(F3). 'closed'면 무동작.
"""

import asyncio
import unittest

from asterion.watchtower import safety as S
from asterion.watchtower.dome_guard import DomeGuard

DOME_CFG = {"dome_radius_m": 2.0, "mount_offset_e_m": 0.0, "mount_offset_n_m": 0.0,
            "mount_offset_up_m": 0.0, "gem_dec_offset_m": 0.0}


class _Bus:
    def __init__(self):
        self.calls = []

    async def run(self, action, actor=None, params=None, func=None, preconditions=None):
        self.calls.append(action)
        if func is not None:
            r = func()
            if asyncio.iscoroutine(r):
                await r

    def n(self, action):
        return self.calls.count(action)


class _Ev:
    def __init__(self):
        self.logs = []

    def log(self, *a, **k):
        self.logs.append(a)


def _emergency_snap(shutter, can_cmd=True):
    return {"dome": {"connected": True, "shutter": shutter, "can_command_shutter": can_cmd},
            "safety": {"state": S.EMERGENCY_CLOSE, "reasons": ["rain"]}}


class TestDomeGuard(unittest.TestCase):
    def test_opening_triggers_close(self):
        # F2 — 'opening' 중이어도 닫기 명령이 나간다.
        closed = {"n": 0}

        class Dome:
            def close_shutter(self_inner):
                closed["n"] += 1

        bus = _Bus()
        g = DomeGuard({"dome": Dome()}, bus, _Ev(), dome_cfg=DOME_CFG)
        asyncio.run(self._tick(g, _emergency_snap("opening")))
        self.assertEqual(bus.n("dome_emergency_close"), 1)
        self.assertEqual(closed["n"], 1)

    def test_retry_after_close_failure(self):
        # F3 — 1차 닫기 실패 후 다음 틱에 재시도(영구 고착 금지).
        attempts = {"n": 0}

        class FailDome:
            def close_shutter(self_inner):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise RuntimeError("COM timeout")

        bus = _Bus()
        g = DomeGuard({"dome": FailDome()}, bus, _Ev(), dome_cfg=DOME_CFG)
        asyncio.run(self._tick(g, _emergency_snap("open")))
        asyncio.run(self._tick(g, _emergency_snap("open")))
        self.assertEqual(attempts["n"], 2)
        self.assertEqual(bus.n("dome_emergency_close"), 2)

    def test_closed_shutter_no_command(self):
        class Dome:
            def close_shutter(self_inner):
                raise AssertionError("닫힌 셔터에 close가 나가면 안 됨")

        bus = _Bus()
        g = DomeGuard({"dome": Dome()}, bus, _Ev(), dome_cfg=DOME_CFG)
        asyncio.run(self._tick(g, _emergency_snap("closed")))
        self.assertEqual(bus.n("dome_emergency_close"), 0)

    def test_manual_shutter_alerts_no_close_task(self):
        # 수동 셔터(can_command False) — 닫기 명령 대신 운영자 경보만.
        bus = _Bus()
        ev = _Ev()
        g = DomeGuard({"dome": object()}, bus, ev, dome_cfg=DOME_CFG)
        asyncio.run(self._tick(g, _emergency_snap("open", can_cmd=False)))
        self.assertEqual(bus.n("dome_emergency_close"), 0)
        self.assertTrue(ev.logs)   # 경보 로그가 남음

    @staticmethod
    async def _tick(guard, snap):
        await guard(snap)
        await asyncio.sleep(0.05)   # _spawn된 닫기 태스크가 끝나도록


if __name__ == "__main__":
    unittest.main()
