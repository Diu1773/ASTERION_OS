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
import time
from typing import Any

from . import safety
from ..core import dome_geometry


class DomeGuard:
    def __init__(self, drivers: dict[str, Any], bus: Any, events: Any, *,
                 dome_cfg: dict, az_tolerance_deg: float = 4.0,
                 shutter_close_timeout_s: float = 90.0, cfg: Any = None):
        self.drivers = drivers
        self.bus = bus
        self.events = events
        self.dome_cfg = dict(dome_cfg)
        self.tol = float(az_tolerance_deg)
        self._close_timeout = float(shutter_close_timeout_s)   # 닫기 수렴 데드라인(초)
        self.cfg = cfg                          # 런타임 cfg(sun_avoidance_deg·allow_solar_slew 라이브 조회)
        self._emergency: str | None = None     # 수동 셔터 경보 디바운스(스팸 방지)
        self._close_task: asyncio.Task | None = None   # 진행 중 비상 닫기(중복 명령 방지)
        self._slew_task: asyncio.Task | None = None
        self._close_started: float | None = None   # EMERGENCY 닫기 시작 monotonic(수렴 데드라인)
        self._stuck_alarmed = False                # 닫기 수렴 실패 경보 디바운스(1회)
        self._slit_alarmed = False                 # 슬릿→태양 유입 경보 디바운스(1회)
        self._slave_alarmed = False                # slaved인데 회전 불가 경보 디바운스(1회)

    def _cfg(self, key: str, default):
        try:
            return self.cfg.get(key, default) if self.cfg is not None else default
        except Exception:
            return default

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        t.add_done_callback(lambda x: x.cancelled() or x.exception())
        return t

    async def __call__(self, snap: dict) -> None:
        dome = snap.get("dome") or {}
        if not dome.get("connected"):
            self._slit_alarmed = False
            self._slave_alarmed = False
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
                if self._close_started is None:
                    self._close_started = time.monotonic()   # 닫기 수렴 데드라인 시작
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
                # 수렴 검증 — 예외 유무가 아니라 *결과 상태*로 판정한다. 모터 데드/ShutterStatus
                # 고착처럼 CloseShutter()가 예외 없이 반환하되 물리적으로 안 닫히면 위 재시도 로그
                # 조차 무음이다. 닫기 시작 후 _close_timeout이 지나도 not_closed면 닫는 능력이
                # 사실상 0으로 저하된 것 — CRITICAL로 1회 격상해 운영자 수동 개입을 부른다.
                elapsed = time.monotonic() - self._close_started
                if elapsed > self._close_timeout and not self._stuck_alarmed:
                    self._stuck_alarmed = True
                    self.events.log(
                        "watchtower",
                        f"⚠ 전동 셔터 닫기 {elapsed:.0f}s 수렴 실패(셔터={shutter}) — 모터/링크 "
                        f"고장 의심, 즉시 수동 닫기 필요! ({saf.get('reasons')})", "error")
            elif self._emergency != "manual":
                self._emergency = "manual"
                self.events.log("watchtower", "⚠ EMERGENCY_CLOSE인데 셔터 수동 — "
                                f"운영자 즉시 닫기 필요! ({saf.get('reasons')})", "error")
            return
        # 여기 도달 = 닫힘 확증(not_closed False) 또는 EMERGENCY 해제 → 닫기 수렴 추적·경보 리셋.
        self._close_started = None
        self._stuck_alarmed = False
        if state != safety.EMERGENCY_CLOSE:
            self._emergency = None                          # 회복 → 수동 경보 디바운스 해제

        # ③ 슬릿→태양 유입 방어 (날씨 EMERGENCY와 독립 — 주간 슬릿 개방이 태양 향함)
        await self._slit_solar_guard(snap, dome, shutter, not_closed)

        # ② 슬레이빙 + 저하 정직성 — slaved인데 회전 불가(모터/링크 고장 또는 미지원)면 슬릿이
        # 마운트를 못 따라가는데 종전 조건(slaved and can_slew_azimuth)은 이를 조용히 스킵했다
        # (can_command_shutter=False 수동 경보와 비대칭). 관측 중(추적/슬루)이면 운영자에게 정직히
        # 경보한다. 회전 가능하면 종전대로 추종 슬루.
        if dome.get("slaved"):
            mount = snap.get("mount") or {}
            cur = dome.get("azimuth")
            mount_active = bool(mount.get("connected")
                                and (mount.get("tracking") or mount.get("slewing")))
            if dome.get("can_slew_azimuth"):
                self._slave_alarmed = False                  # 회전 가능 → 재무장
                if (not dome.get("moving") and mount.get("connected")
                        and mount.get("alt") is not None and cur is not None):
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
            elif mount_active and not self._slave_alarmed:
                # slaved 설정인데 회전 불가 + 관측 중 → 슬릿 추종 불가를 정직히 경보(1회).
                self._slave_alarmed = True
                off = ""
                if cur is not None and mount.get("alt") is not None:
                    target, _ = dome_geometry.target_dome_azimuth(
                        mount["alt"], mount.get("az") or 0.0, **self.dome_cfg)
                    off = (f" (목표 {target:.0f}° vs 현재 {cur:.0f}°, "
                           f"오차 {abs(dome_geometry.azimuth_error(cur, target)):.0f}°)")
                self.events.log(
                    "watchtower",
                    f"⚠ 돔이 slaved인데 회전 불가(can_slew_azimuth=False){off} — 슬릿이 마운트를 "
                    "못 따라감, 수동 정렬/현장 확인 필요", "error")
        else:
            self._slave_alarmed = False                      # slaved 아님 → 경보 해제

    async def _slit_solar_guard(self, snap: dict, dome: dict, shutter, not_closed) -> None:
        """주간에 셔터가 닫힘 미확증인데 슬릿(돔 개구부=현재 dome.azimuth)이 태양 방위 제외각
        안을 향하면 — OTA가 태양에서 멀어도 슬릿으로 태양이 들어와 광학을 태운다 — 전동이면 닫고
        수동/회전불가면 운영자 경보. 정상 주간은 셔터가 닫혀(not_closed False) 무동작. 책임자
        override(allow_solar_slew)면 비활성. 슬릿은 폭넓은 고도를 덮으므로 방위 기준만으로 보수적
        판정(고도 정밀화는 추후). 추측항법(azimuth_estimated) 돔은 belief 기반이라 부정확 가능."""
        if self._cfg("safety.allow_solar_slew", False) or not not_closed:
            self._slit_alarmed = False
            return
        sun = snap.get("sun") or {}
        sun_alt, sun_az = sun.get("alt"), sun.get("az")
        if sun_alt is None or sun_az is None or sun_alt <= -0.5:   # 야간/태양 위치 불명 → 무동작
            self._slit_alarmed = False
            return
        dome_az = dome.get("azimuth")
        if dome_az is None:                     # 슬릿 방위 불명 → 슬레이빙 추종실패 경보가 별도 담당
            return
        excl = float(self._cfg("safety.sun_avoidance_deg", 15.0))
        sep = abs(dome_geometry.azimuth_error(dome_az, sun_az))
        if sep >= excl:
            self._slit_alarmed = False          # 슬릿이 태양에서 멀어짐 → 재무장
            return
        # 슬릿이 태양 방위 제외각 안 + 주간 + 셔터 미확증닫힘 → 태양 유입 위험.
        can_cmd = bool(dome.get("can_command_shutter"))
        if not self._slit_alarmed:
            self._slit_alarmed = True
            est = " (추측항법 — 방위 belief 기반, 부정확 가능)" if dome.get("azimuth_estimated") else ""
            self.events.log(
                "watchtower",
                f"⚠ 슬릿이 태양 방위 {sep:.0f}° 이내 + 주간 + 셔터 미확증닫힘{est} — 태양 유입 "
                f"위험: {'전동 닫기 시도' if can_cmd else '즉시 수동 닫기 필요'}", "error")
        if can_cmd:
            prev = self._close_task
            if not (prev is not None and not prev.done()):
                self._close_task = self._spawn(self.bus.run(
                    "dome_slit_solar_close", actor="watchtower",
                    params={"slit_sun_sep_deg": round(sep, 1), "shutter": shutter},
                    func=lambda: asyncio.to_thread(self.drivers["dome"].close_shutter)))
