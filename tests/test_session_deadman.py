"""SessionWatchdog — 원격 운영자 데드맨 (REMOTE_ACCESS_PLAN Phase C).

핵심: 원격 수동 운영 중 하트비트가 끊기면(stale) + 보호할 위험이 있으면 세이프-스테이트.
발화 안 함: 비활성 / 미무장(하트비트 없음) / 신선 / 위험 없음 / NightRunner 무인 운영.
"""

import asyncio
import unittest

from asterion.watchtower.session_watchdog import SessionWatchdog

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
        self.tracking_off = 0
        self.stopped = 0
        self.parked = 0

    def set_tracking(self, on):
        if not on:
            self.tracking_off += 1

    def stop(self):
        self.stopped += 1

    def park(self):
        self.parked += 1


class NoParkMount:
    """PWI4류 — park/find_home/set_park 미구현(NotImplementedError), goto_altaz만 있음."""

    def __init__(self):
        self.tracking_off = 0
        self.stopped = 0
        self.goto = []

    def set_tracking(self, on):
        if not on:
            self.tracking_off += 1

    def stop(self):
        self.stopped += 1

    def park(self):
        raise NotImplementedError("이 마운트는 파킹을 지원하지 않습니다")

    def goto_altaz(self, alt, az):
        self.goto.append((alt, az))


class FailParkMount:
    """park()가 *전이적* 실패(타임아웃/통신)로 일반 예외를 던지는 마운트(미지원=NotImplementedError 아님)."""

    def __init__(self):
        self.tracking_off = 0
        self.stopped = 0

    def set_tracking(self, on):
        if not on:
            self.tracking_off += 1

    def stop(self):
        self.stopped += 1

    def park(self):
        raise RuntimeError("PWI4 /mount/park HTTP timeout")


class FakeDome:
    def __init__(self):
        self.closed = 0

    def close_shutter(self):
        self.closed += 1


def _snap(*, tracking=False, slewing=False, shutter="closed", can_close=True,
          capture=False, autoflat=False, orch=False, night=False):
    return {
        "mount": {"connected": True, "tracking": tracking, "slewing": slewing},
        "dome": {"connected": True, "shutter": shutter,
                 "can_command_shutter": can_close},
        "capture": {"active": capture},
        "autoflat": {"running": autoflat},
        "orchestrator": {"running": orch},
        "night_runner": {"active": night},
    }


async def _tick(wd, snap):
    await wd(snap)
    await asyncio.sleep(0.05)   # _spawn된 세이프-스테이트 태스크 완료 대기


class TestSessionDeadman(unittest.TestCase):
    def _make(self, **cfg):
        self.bus = _Bus()
        self.mount = FakeMount()
        self.dome = FakeDome()
        self.alerts = []
        wd = SessionWatchdog(
            {"mount": self.mount, "dome": self.dome}, self.bus, _Ev(),
            Cfg(**cfg),
            alert_fn=lambda t, d, rule_id=None: self.alerts.append((t, d, rule_id)))
        return wd

    def _make_with_mount(self, mount, **cfg):
        self.bus = _Bus()
        self.mount = mount
        self.dome = FakeDome()
        self.alerts = []
        return SessionWatchdog(
            {"mount": mount, "dome": self.dome}, self.bus, _Ev(), Cfg(**cfg),
            alert_fn=lambda t, d, rule_id=None: self.alerts.append((t, d, rule_id)))

    def _on0(self):  # enabled + 즉시 stale(timeout 0)
        return {"safety.session_deadman.enabled": True,
                "safety.session_deadman.timeout_seconds": 0.0}

    def test_disabled_never_fires(self):
        wd = self._make()                       # enabled 기본 False
        wd.heartbeat()
        asyncio.run(_tick(wd, _snap(tracking=True)))
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 0)

    def test_unarmed_no_heartbeat_no_fire(self):
        wd = self._make(**self._on0())          # 하트비트 호출 안 함 → 미무장
        asyncio.run(_tick(wd, _snap(tracking=True)))
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 0)

    def test_fresh_heartbeat_no_fire(self):
        wd = self._make(**{"safety.session_deadman.enabled": True,
                           "safety.session_deadman.timeout_seconds": 120.0})
        wd.heartbeat()
        asyncio.run(_tick(wd, _snap(tracking=True)))
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 0)

    def test_stale_risky_fires_safe_state(self):
        wd = self._make(**self._on0())
        wd.heartbeat()
        asyncio.run(_tick(wd, _snap(tracking=True, shutter="open")))
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 1)
        self.assertEqual(self.mount.tracking_off, 1)
        self.assertEqual(self.mount.stopped, 1)
        self.assertEqual(self.mount.parked, 1)
        self.assertEqual(self.dome.closed, 1)
        self.assertEqual(len(self.alerts), 1)
        # 돔이 정상 자동 폐쇄됨 → 일반 데드맨 rule_id(격상 아님)
        self.assertEqual(self.alerts[0][2], "session_deadman")

    def test_stale_but_idle_no_fire(self):
        wd = self._make(**self._on0())
        wd.heartbeat()
        asyncio.run(_tick(wd, _snap()))         # 추적X·슬루X·돔닫힘·세션X → 위험 없음
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 0)

    def test_nightrunner_exempt(self):
        wd = self._make(**self._on0())
        wd.heartbeat()
        asyncio.run(_tick(wd, _snap(tracking=True, night=True)))   # 무인 운영
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 0)

    def test_manual_shutter_not_closed_but_stops(self):
        wd = self._make(**self._on0())
        wd.heartbeat()
        asyncio.run(_tick(wd, _snap(tracking=True, shutter="open", can_close=False)))
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 1)
        self.assertEqual(self.dome.closed, 0)   # 수동 셔터 — 닫지 못함(경보만)
        self.assertEqual(self.mount.parked, 1)
        # 닫을 수 없는 열린 슬릿 → 격상된 별도 rule_id(즉시 현장 조치 경보)
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(self.alerts[0][2], "session_deadman_shutter_stuck")

    def test_no_park_mount_with_stow_goes_to_stow(self):
        # rank4 — park 미지원 마운트 + safety.stow_altaz 설정 → 안전 stow로 goto 폴백(정지+이동).
        m = NoParkMount()
        cfg = self._on0()
        cfg["safety.stow_altaz"] = [10.0, 0.0]
        wd = self._make_with_mount(m, **cfg)
        wd.heartbeat()
        asyncio.run(_tick(wd, _snap(tracking=True, shutter="open")))
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 1)
        self.assertEqual(m.tracking_off, 1)
        self.assertEqual(m.goto, [(10.0, 0.0)])             # 안전 stow로 이동(phantom-park 아님)
        self.assertEqual(self.alerts[0][2], "session_deadman")   # stow됨 → 안전 상태

    def test_no_park_mount_no_stow_escalates(self):
        # rank4 — park 미지원 + stow 미설정 → 정지만, '파킹 완료' 거짓단언 대신 격상.
        m = NoParkMount()
        wd = self._make_with_mount(m, **self._on0())        # stow 미설정
        wd.heartbeat()
        asyncio.run(_tick(wd, _snap(tracking=True, shutter="open")))
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 1)
        self.assertEqual(m.tracking_off, 1)
        self.assertEqual(m.goto, [])                        # 폴백 goto 없음(정지만)
        self.assertEqual(self.alerts[0][2], "session_deadman_no_park")

    def test_transient_park_failure_not_no_park(self):
        # rank5 — park()의 전이적 실패는 '미지원(no_park)'이 아니라 park_failed로 구분 경보.
        m = FailParkMount()
        wd = self._make_with_mount(m, **self._on0())
        wd.heartbeat()
        asyncio.run(_tick(wd, _snap(tracking=True, shutter="open")))
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 1)
        self.assertEqual(m.tracking_off, 1)            # 정지는 수행
        self.assertEqual(self.alerts[0][2], "session_deadman_park_failed")

    def test_debounce_then_rearm(self):
        wd = self._make(**self._on0())
        wd.heartbeat()
        snap = _snap(tracking=True)
        asyncio.run(_tick(wd, snap))
        asyncio.run(_tick(wd, snap))            # 같은 stale 지속 → 재발화 안 함
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 1)
        wd.heartbeat()                          # 하트비트 재개 → 재무장
        asyncio.run(_tick(wd, snap))
        self.assertEqual(self.bus.n("session_deadman_safe_state"), 2)


if __name__ == "__main__":
    unittest.main()
