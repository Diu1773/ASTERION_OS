"""DomeGuard — 비상 자동닫힘 액추에이터(fail-closed actuation).

핵심: 셔터가 'open'뿐 아니라 'opening'/'unknown'/'error'(닫힘 미확증)여도 닫고(F2),
닫기가 실패하면 다음 틱에 재시도해 성공까지 수렴한다(F3). 'closed'면 무동작.
"""

import asyncio
import unittest

from asterion.watchtower import safety as S
from asterion.watchtower.dome_guard import DomeGuard

from ._helpers import Cfg

DOME_CFG = {"dome_radius_m": 2.0, "mount_offset_e_m": 0.0, "mount_offset_n_m": 0.0,
            "mount_offset_up_m": 0.0, "gem_dec_offset_m": 0.0}


def _slit_snap(*, dome_az, sun_alt=30.0, sun_az=185.0, shutter="open",
               can_cmd=True, estimated=False):
    # 비-EMERGENCY(SAFE_CLOSED=주간) — ③ 슬릿 가드 경로를 탄다.
    return {"dome": {"connected": True, "shutter": shutter, "can_command_shutter": can_cmd,
                     "azimuth": dome_az, "azimuth_estimated": estimated},
            "sun": {"alt": sun_alt, "az": sun_az},
            "safety": {"state": S.SAFE_CLOSED, "reasons": ["daytime"]}}


def _has_slit_log(ev):
    return bool([a for a in ev.logs if any("슬릿" in str(x) for x in a)])


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

    def test_motorized_close_stuck_escalates_once(self):
        # rank2 — 전동셔터가 예외 없이도 영영 안 닫히면(모터 데드/ShutterStatus 고착) 수렴
        # 데드라인 초과 시 CRITICAL 1회 격상. 짧은 timeout으로 검증.
        class StuckDome:
            def close_shutter(self_inner):
                pass   # 예외 없이 반환하되 셔터는 계속 'open'(물리적으로 안 닫힘)

        bus = _Bus()
        ev = _Ev()
        g = DomeGuard({"dome": StuckDome()}, bus, ev, dome_cfg=DOME_CFG,
                      shutter_close_timeout_s=0.0)
        asyncio.run(self._tick(g, _emergency_snap("open")))   # tick1: _close_started 기록
        asyncio.run(self._tick(g, _emergency_snap("open")))   # tick2: elapsed>0 → 격상
        asyncio.run(self._tick(g, _emergency_snap("open")))   # tick3: 디바운스 — 재격상 안 함
        stuck = [a for a in ev.logs if any("수렴 실패" in str(x) for x in a)]
        self.assertEqual(len(stuck), 1)
        # 격상해도 닫기 재시도는 계속 수렴 시도(영구 포기 금지)
        self.assertGreaterEqual(bus.n("dome_emergency_close"), 2)

    def test_close_confirmed_resets_stuck_tracking(self):
        # 닫힘 확증(closing)되면 수렴 추적·경보 디바운스 리셋 → 다음 비상 때 재무장.
        class Dome:
            def close_shutter(self_inner):
                pass

        bus = _Bus()
        ev = _Ev()
        g = DomeGuard({"dome": Dome()}, bus, ev, dome_cfg=DOME_CFG,
                      shutter_close_timeout_s=0.0)
        asyncio.run(self._tick(g, _emergency_snap("open")))      # 타이머 시작
        asyncio.run(self._tick(g, _emergency_snap("closing")))   # 닫힘 확증 → 리셋
        self.assertIsNone(g._close_started)
        self.assertFalse(g._stuck_alarmed)
        self.assertEqual([a for a in ev.logs if any("수렴 실패" in str(x) for x in a)], [])

    # ---------- ③ 슬릿→태양 유입 방어 (rank3) ----------

    def test_slit_faces_sun_motorized_closes_and_alerts(self):
        # 주간 슬릿 개방이 태양 방위 제외각 안(sep 5°<15°) → 전동 닫기 + 경보.
        closed = {"n": 0}

        class Dome:
            def close_shutter(self_inner):
                closed["n"] += 1

        bus = _Bus()
        ev = _Ev()
        g = DomeGuard({"dome": Dome()}, bus, ev, dome_cfg=DOME_CFG)
        asyncio.run(self._tick(g, _slit_snap(dome_az=180, sun_az=185)))
        self.assertTrue(_has_slit_log(ev))
        self.assertEqual(bus.n("dome_slit_solar_close"), 1)
        self.assertEqual(closed["n"], 1)

    def test_slit_far_from_sun_no_action(self):
        bus = _Bus()
        ev = _Ev()
        g = DomeGuard({"dome": object()}, bus, ev, dome_cfg=DOME_CFG)
        asyncio.run(self._tick(g, _slit_snap(dome_az=180, sun_az=10)))   # sep 170°
        self.assertEqual(bus.n("dome_slit_solar_close"), 0)
        self.assertFalse(_has_slit_log(ev))

    def test_slit_sun_night_no_action(self):
        bus = _Bus()
        ev = _Ev()
        g = DomeGuard({"dome": object()}, bus, ev, dome_cfg=DOME_CFG)
        asyncio.run(self._tick(g, _slit_snap(dome_az=180, sun_az=185, sun_alt=-20)))
        self.assertEqual(bus.n("dome_slit_solar_close"), 0)

    def test_slit_sun_shutter_closed_no_action(self):
        # 정상 주간 = 셔터 닫힘 → not_closed False → 무동작(오발 없음).
        bus = _Bus()
        ev = _Ev()
        g = DomeGuard({"dome": object()}, bus, ev, dome_cfg=DOME_CFG)
        asyncio.run(self._tick(g, _slit_snap(dome_az=180, sun_az=185, shutter="closed")))
        self.assertEqual(bus.n("dome_slit_solar_close"), 0)
        self.assertFalse(_has_slit_log(ev))

    def test_slit_sun_manual_alerts_no_close(self):
        # 수동(can_command False) — 닫기 명령 없이 운영자 경보만.
        bus = _Bus()
        ev = _Ev()
        g = DomeGuard({"dome": object()}, bus, ev, dome_cfg=DOME_CFG)
        asyncio.run(self._tick(g, _slit_snap(dome_az=180, sun_az=185, can_cmd=False)))
        self.assertEqual(bus.n("dome_slit_solar_close"), 0)
        self.assertTrue(_has_slit_log(ev))

    def test_slit_sun_override_disabled(self):
        # allow_solar_slew(책임자) → 슬릿 가드 비활성.
        bus = _Bus()
        ev = _Ev()
        g = DomeGuard({"dome": object()}, bus, ev, dome_cfg=DOME_CFG,
                      cfg=Cfg(**{"safety.allow_solar_slew": True}))
        asyncio.run(self._tick(g, _slit_snap(dome_az=180, sun_az=185)))
        self.assertEqual(bus.n("dome_slit_solar_close"), 0)
        self.assertFalse(_has_slit_log(ev))

    @staticmethod
    async def _tick(guard, snap):
        await guard(snap)
        await asyncio.sleep(0.05)   # _spawn된 닫기 태스크가 끝나도록


if __name__ == "__main__":
    unittest.main()
