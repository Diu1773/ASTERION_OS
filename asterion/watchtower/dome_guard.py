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
        self._emergency: str | None = None     # 수동 셔터 경보 디바운스(스팸 방지)
        self._close_task: asyncio.Task | None = None   # 진행 중 비상 닫기(중복 명령 방지)
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
        shutter = dome.get("shutter")
        # 닫힘이 *확증*된 상태만 안전으로 본다('closed'=닫힘, 'closing'=닫는 중).
        # 'open'/'opening'(개방 진행)/'unknown'/'error'는 모두 닫힘 미확증 → fail-closed로 닫는다.
        # ('open'만 매칭하면 슬릿 여는 도중 비상이 와도 'opening' 동안 닫지 못한다.)
        not_closed = shutter not in ("closed", "closing")

        # ① 비상 자동닫힘 — EMERGENCY_CLOSE이고 닫힘이 확증되지 않았으면 닫는다.
        if state == safety.EMERGENCY_CLOSE and not_closed:
            if dome.get("can_command_shutter"):
                # 진행 중 닫기 명령이 없을 때만 (재)발행한다. 닫기가 실패(COM 타임아웃/예외)해
                # 셔터가 여전히 안 닫혀 있으면 다음 틱에 재시도 — '성공해 closed/closing이 될
                # 때까지' 수렴한다(단발 fire-and-forget로 미수렴되던 결함 수정). in-flight 1개만.
                prev = self._close_task
                busy = prev is not None and not prev.done()
                if not busy:
                    if (prev is not None and not prev.cancelled()
                            and prev.exception() is not None):
                        self.events.log("watchtower", "⚠ 비상 셔터 닫기 실패 — 재시도 "
                                        f"({prev.exception()})", "error")
                    self._close_task = self._spawn(self.bus.run(
                        "dome_emergency_close", actor="watchtower",
                        params={"reason": saf.get("reasons"), "shutter": shutter},
                        func=lambda: asyncio.to_thread(
                            self.drivers["dome"].close_shutter)))
            elif self._emergency != "manual":
                self._emergency = "manual"
                self.events.log("watchtower", "⚠ EMERGENCY_CLOSE인데 셔터 수동 — "
                                f"운영자 즉시 닫기 필요! ({saf.get('reasons')})", "error")
            return
        if state != safety.EMERGENCY_CLOSE:
            self._emergency = None                          # 회복 → 수동 경보 디바운스 해제

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
