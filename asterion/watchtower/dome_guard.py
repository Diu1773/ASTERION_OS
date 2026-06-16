"""DomeGuard — 안전 '판정'(safety)과 분리된 돔 '행동' 레이어.

StatusSampler가 매 스냅샷마다 호출한다. 두 가지를 한다:
  ① 비상 자동닫힘 — EMERGENCY_CLOSE인데 셔터가 열려 있으면, 닫을 수 있으면 닫고
     (전동 셔터), 수동 셔터면 운영자에게 경보(자동닫힘 불가를 정직히 알림).
  ② 슬레이빙 — 돔이 slaved면 dome_geometry로 목표 방위를 계산해 허용오차를 넘으면
     추종(slew). 블로킹 슬루(시리얼 돔)는 태스크로 띄워 샘플러 루프를 막지 않는다.

장비 키에 의존하지 않는다 — 돔이 REGISTRY에 있어 스냅샷에 'dome'이 뜨면 자동 동작.
"""

from __future__ import annotations

import asyncio
from typing import Any

from . import safety
from ..core import dome_geometry


class DomeGuard:
    def __init__(self, drivers: dict[str, Any], bus: Any, events: Any, *,
                 dome_cfg: dict, az_tolerance_deg: float = 4.0):
        self.drivers = drivers
        self.bus = bus
        self.events = events
        self.dome_cfg = dict(dome_cfg)
        self.tol = float(az_tolerance_deg)
        self._emergency: str | None = None     # 비상 디바운스
        self._slew_task: asyncio.Task | None = None

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        t.add_done_callback(lambda x: x.cancelled() or x.exception())
        return t

    async def __call__(self, snap: dict) -> None:
        dome = snap.get("dome") or {}
        if not dome.get("connected"):
            return
        saf = snap.get("safety") or {}
        state = saf.get("state")

        # ① 비상 자동닫힘
        if state == safety.EMERGENCY_CLOSE and dome.get("shutter") == "open":
            if dome.get("can_command_shutter"):
                if self._emergency != "closing":            # 에피소드당 1회
                    self._emergency = "closing"
                    self._spawn(self.bus.run(
                        "dome_emergency_close", actor="watchtower",
                        params={"reason": saf.get("reasons")},
                        func=lambda: asyncio.to_thread(
                            self.drivers["dome"].close_shutter)))
            elif self._emergency != "manual":
                self._emergency = "manual"
                self.events.log("watchtower", "⚠ EMERGENCY_CLOSE인데 셔터 수동 — "
                                f"운영자 즉시 닫기 필요! ({saf.get('reasons')})", "error")
            return
        if state != safety.EMERGENCY_CLOSE:
            self._emergency = None                          # 회복 → 디바운스 해제

        # ② 슬레이빙
        if (dome.get("slaved") and dome.get("can_slew_azimuth")
                and not dome.get("moving")):
            mount = snap.get("mount") or {}
            cur = dome.get("azimuth")
            if (mount.get("connected") and mount.get("alt") is not None
                    and cur is not None):
                target, _ = dome_geometry.target_dome_azimuth(
                    mount["alt"], mount.get("az") or 0.0, **self.dome_cfg)
                busy = (self._slew_task is not None
                        and not self._slew_task.done())
                if (not busy and abs(dome_geometry.azimuth_error(cur, target))
                        > self.tol):
                    self._slew_task = self._spawn(self.bus.run(
                        "dome_slave_slew", actor="watchtower",
                        params={"target_az": round(target, 2)},
                        func=lambda t=target: asyncio.to_thread(
                            self.drivers["dome"].slew_to_azimuth, t)))
