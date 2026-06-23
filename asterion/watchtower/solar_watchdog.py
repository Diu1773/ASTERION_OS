"""SolarWatchdog — OTA가 태양 제외각 안에서 슬루/추적 중이면 즉시 정지(폐루프 최후 방어선).

진입 가드(슬루 시작 거부, _solar_precond / sun_sep_ok)를 뚫고 들어온 경우 —
연속 move_axis가 태양으로 드리프트, 주간 추적 드리프트, override 후 방치 등 — 를
샘플러가 매 틱(1Hz) 잡아 mount.stop + move_axis 0 + tracking off로 정지시킨다.

fail-closed: 주간(태양 지평 위)에 구동(슬루/추적) 중인데 마운트 좌표가 결측이면 — 슬루 중
ASCOM이 alt/az를 잠깐 떨구는 등 — 분리각을 계산할 수 없다. 결측을 안전으로 보지 않고 정지한다
(태양 위치는 ephemeris 순수 계산이라 결측이면 주간 판정 불가 → 그땐 오정지 방지로 감시 보류).

태양이 지평 위(주간)일 때만 동작하므로 야간 정상 슬루를 방해하지 않는다(야간엔 태양이
지평 아래라 OTA가 태양에 닿을 수 없다). allow_solar_slew(책임자 config)면 비활성.
DomeGuard와 동형 — 판정이 아니라 '행동' 레이어이고, 장비 명령은 ActionBus를 통과한다.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..core import ephemeris


class SolarWatchdog:
    def __init__(self, drivers: dict[str, Any], bus: Any, events: Any, cfg: Any):
        self.drivers = drivers
        self.bus = bus
        self.events = events
        self.cfg = cfg
        self._stopped = False                 # 에피소드당 1회 발화 디바운스
        self._task: asyncio.Task | None = None

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        t.add_done_callback(lambda x: x.cancelled() or x.exception())
        return t

    async def __call__(self, snap: dict) -> None:
        if bool(self.cfg.get("safety.allow_solar_slew", False)):
            self._stopped = False             # 책임자 override — 감시 비활성
            return
        mount = snap.get("mount") or {}
        sun = snap.get("sun") or {}
        if not mount.get("connected"):
            self._stopped = False
            return
        sun_alt, sun_az = sun.get("alt"), sun.get("az")
        # 태양 위치는 순수 계산(ephemeris)이라 거의 항상 존재. 없으면 주간/야간 판정 자체가
        # 불가하므로 감시를 보류한다 — ephemeris 결함으로 야간 정상 슬루를 오정지하지 않기 위함.
        if sun_alt is None or sun_az is None:
            return
        if sun_alt <= -0.5:                   # 태양 지평 아래(야간/박명) → 위험 없음·정상 슬루 보호
            self._stopped = False
            return
        # ── 주간 확정(sun_alt > -0.5) ──
        moving = bool(mount.get("slewing") or mount.get("tracking"))
        if not moving:                        # 구동 안 함 → 태양으로 박을 위험 없음
            self._stopped = False
            return
        # 주간 + 구동 중. 마운트 좌표를 알면 분리각으로 판정한다. 좌표가 결측이면(슬루 중
        # ASCOM이 위치를 잠깐 떨굼 등) 분리각을 계산할 수 없는데, fail-closed 원칙상 '결측을
        # 안전으로 보지 않는다' — 주간에 위치 불명으로 구동 중이면 최후 방어선을 발화한다.
        alt, az = mount.get("alt"), mount.get("az")
        if alt is None or az is None:
            sep = None
        else:
            sep = ephemeris.angular_separation_altaz(alt, az, sun_alt, sun_az)
        excl = float(self.cfg.get("safety.sun_avoidance_deg", 15.0))
        if sep is None or sep < excl:
            if not self._stopped:
                busy = self._task is not None and not self._task.done()
                if not busy:
                    self._stopped = True
                    self._task = self._spawn(self._emergency_stop(sep))
        else:
            self._stopped = False             # 멀어짐 → 재무장

    async def _emergency_stop(self, sep: float | None) -> None:
        where = f"태양 {sep:.0f}° 이내" if sep is not None else "주간 위치 불명 상태"
        self.events.log("watchtower",
                        f"⚠ OTA가 {where}에서 이동 중 — 긴급 정지", "error")
        mount = self.drivers["mount"]

        def _halt():
            # 슬루·연속조그·추적 모두 멈춘다 (best-effort — 하나 실패해도 나머지 시도).
            try:
                mount.stop()
            except Exception:
                pass
            for ax in (0, 1):
                try:
                    mount.move_axis(ax, 0.0)
                except Exception:
                    pass
            try:
                mount.set_tracking(False)
            except Exception:
                pass

        await self.bus.run("solar_emergency_stop", actor="watchtower",
                           params={"sun_sep_deg": round(sep, 1) if sep is not None else None},
                           func=lambda: asyncio.to_thread(_halt))
