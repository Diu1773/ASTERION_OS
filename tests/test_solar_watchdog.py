"""SolarWatchdog — 태양 폐루프 자동정지(진입 가드 최후 방어선).

핵심: 슬루/추적 중 OTA가 태양 제외각 안 + 주간이면 긴급 정지. 정지 안 함 조건:
멈춰 있음 / 태양에서 멈 / 야간(태양 지평 아래) / 책임자 override.
"""

import asyncio
import unittest

from asterion.watchtower.solar_watchdog import SolarWatchdog

from ._helpers import Cfg


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
    def log(self, *a, **k):
        pass


class FakeMount:
    def __init__(self):
        self.stopped = 0
        self.tracking_off = 0
        self.axis_zeroed = []

    def stop(self):
        self.stopped += 1

    def move_axis(self, ax, rate):
        self.axis_zeroed.append((ax, rate))

    def set_tracking(self, on):
        if not on:
            self.tracking_off += 1


def _snap(*, m_alt, m_az, sun_alt, sun_az, slewing=False, tracking=False, connected=True):
    return {"mount": {"connected": connected, "alt": m_alt, "az": m_az,
                      "slewing": slewing, "tracking": tracking},
            "sun": {"alt": sun_alt, "az": sun_az}}


async def _tick(wd, snap):
    await wd(snap)
    await asyncio.sleep(0.05)   # _spawn된 정지 태스크 완료 대기


class TestSolarWatchdog(unittest.TestCase):
    def _make(self, **cfg):
        self.bus = _Bus()
        self.mount = FakeMount()
        return SolarWatchdog({"mount": self.mount}, self.bus, _Ev(), Cfg(**cfg))

    def test_near_sun_moving_daytime_stops(self):
        wd = self._make()
        # OTA가 태양 위치(sep 0) + 슬루 중 + 태양 지평 위(주간)
        asyncio.run(_tick(wd, _snap(m_alt=30, m_az=180, sun_alt=30, sun_az=180, slewing=True)))
        self.assertEqual(self.bus.n("solar_emergency_stop"), 1)
        self.assertEqual(self.mount.stopped, 1)
        self.assertEqual(self.mount.tracking_off, 1)

    def test_tracking_drift_into_sun_stops(self):
        wd = self._make()
        asyncio.run(_tick(wd, _snap(m_alt=20, m_az=90, sun_alt=20, sun_az=95, tracking=True)))
        self.assertEqual(self.bus.n("solar_emergency_stop"), 1)

    def test_not_moving_no_stop(self):
        wd = self._make()
        asyncio.run(_tick(wd, _snap(m_alt=30, m_az=180, sun_alt=30, sun_az=180)))
        self.assertEqual(self.bus.n("solar_emergency_stop"), 0)

    def test_far_from_sun_no_stop(self):
        wd = self._make()
        # 천정 근처 vs 저고도 반대 방위 → 이격 큼
        asyncio.run(_tick(wd, _snap(m_alt=85, m_az=0, sun_alt=20, sun_az=180, slewing=True)))
        self.assertEqual(self.bus.n("solar_emergency_stop"), 0)

    def test_night_no_stop(self):
        # 태양이 지평 아래(야간) → 정상 슬루 방해 금지(OTA가 태양에 닿을 수 없음)
        wd = self._make()
        asyncio.run(_tick(wd, _snap(m_alt=30, m_az=180, sun_alt=-20, sun_az=180, slewing=True)))
        self.assertEqual(self.bus.n("solar_emergency_stop"), 0)

    def test_override_disables(self):
        wd = self._make(**{"safety.allow_solar_slew": True})
        asyncio.run(_tick(wd, _snap(m_alt=30, m_az=180, sun_alt=30, sun_az=180, slewing=True)))
        self.assertEqual(self.bus.n("solar_emergency_stop"), 0)

    def test_debounce_fires_once(self):
        wd = self._make()
        snap = _snap(m_alt=30, m_az=180, sun_alt=30, sun_az=180, slewing=True)
        asyncio.run(_tick(wd, snap))
        asyncio.run(_tick(wd, snap))   # 같은 위험 상태 지속 → 재발화 안 함
        self.assertEqual(self.bus.n("solar_emergency_stop"), 1)

    def test_daytime_moving_unknown_position_stops(self):
        # 주간 + 구동 중인데 마운트 좌표 결측(슬루 중 ASCOM이 alt/az를 떨굼) → 분리각 계산
        # 불가. fail-closed: 결측을 안전으로 보지 않고 긴급 정지(최후 방어선 무장해제 방지).
        wd = self._make()
        asyncio.run(_tick(wd, _snap(m_alt=None, m_az=None,
                                    sun_alt=30, sun_az=180, slewing=True)))
        self.assertEqual(self.bus.n("solar_emergency_stop"), 1)
        self.assertEqual(self.mount.stopped, 1)
        self.assertEqual(self.mount.tracking_off, 1)

    def test_daytime_idle_unknown_position_no_stop(self):
        # 좌표 불명이어도 구동(슬루/추적) 안 하면 태양으로 박을 위험 없음 → 정지 안 함.
        wd = self._make()
        asyncio.run(_tick(wd, _snap(m_alt=None, m_az=None, sun_alt=30, sun_az=180)))
        self.assertEqual(self.bus.n("solar_emergency_stop"), 0)

    def test_night_unknown_position_no_stop(self):
        # 야간(태양 지평 아래)이면 좌표 결측이어도 위험 없음 — 정상 슬루 오정지 금지.
        wd = self._make()
        asyncio.run(_tick(wd, _snap(m_alt=None, m_az=None,
                                    sun_alt=-20, sun_az=180, slewing=True)))
        self.assertEqual(self.bus.n("solar_emergency_stop"), 0)


if __name__ == "__main__":
    unittest.main()
