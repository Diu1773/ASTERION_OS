"""SolarWatchdog — 주간에 OTA가 태양 제외각 안을 *지향*하면 정지/경보(폐루프 최후 방어선).

진입 가드(슬루 시작 거부, _solar_precond / sun_sep_ok)를 뚫고 들어온 경우 —
연속 move_axis가 태양으로 드리프트, 주간 추적 드리프트, override 후 방치 등 — 를
샘플러가 매 틱(1Hz) 잡아 mount.stop + move_axis 0 + tracking off로 정지시킨다.

핵심: 보호 불변식은 *운동*이 아니라 *지향*이다. 구동 중일 때만 보지 않고, 주간+연결이면
정지(idle) 상태여도 분리각을 평가한다. 구동 중 태양 근접이면 긴급 정지, 정지해 있는데 태양
근처를 지향하면(슬루가 태양 근처서 끝남 / 태양이 ~15°/h로 정지 OTA에 드리프트 진입 / park
탈출수단 없는 개방형·PWI4) 추적off 재확인 + 운영자 경보 — 능동 회피 슬루는 또 다른 태양통과를
유발할 수 있어 자동 해소하지 않고 사람을 부른다. 개방형/수동돔은 돔 가림이 없어 dwell=광학 소손.

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
    def __init__(self, drivers: dict[str, Any], bus: Any, events: Any, cfg: Any,
                 *, safety_pool: Any = None):
        self.drivers = drivers
        self.bus = bus
        self.events = events
        self.cfg = cfg
        self._pool = safety_pool              # 전용 안전 액추에이터 풀(rank8) — 없으면 to_thread
        self._stopped = False                 # 에피소드당 1회 발화 디바운스
        self._task: asyncio.Task | None = None

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        t.add_done_callback(lambda x: x.cancelled() or x.exception())
        return t

    def _exec(self, fn):
        """블로킹 드라이버 호출 실행 — 전용 안전 풀이 주입됐으면 거기서(상태 폴링·_recover_stuck이
        점유하는 기본 to_thread 풀과 격리: 다장비 COM hang에도 긴급 정지가 워커를 잡게). 없으면 to_thread."""
        if self._pool is not None:
            return asyncio.get_running_loop().run_in_executor(self._pool, fn)
        return asyncio.to_thread(fn)

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
        # 통합원리 #1('마운트가 태양을 안 보게')은 *운동*이 아니라 *지향*의 불변식이다. 그래서
        # 움직일 때만 보지 않고, 주간+연결이면 정지 상태여도 분리각을 평가한다 — 정지해 있어도
        # OTA가 태양 근처를 지향하면 위험은 동일하기 때문: 슬루가 태양 근처에서 끝남 / 태양이
        # ~15°/h로 정지 OTA에 드리프트 진입(20분=20°) / park로 못 빠져나가는 개방형·PWI4. 개방형·
        # 수동돔(can_command_shutter=False)은 돔 가림이 없어 이 dwell이 곧 광학·센서 소손이다.
        # 좌표 결측은 fail-closed로 위험 취급(주간에 위치 불명).
        alt, az = mount.get("alt"), mount.get("az")
        if alt is None or az is None:
            sep = None
        else:
            sep = ephemeris.angular_separation_altaz(alt, az, sun_alt, sun_az)
        excl = float(self.cfg.get("safety.sun_avoidance_deg", 15.0))
        moving = bool(mount.get("slewing") or mount.get("tracking"))
        if sep is None or sep < excl:
            prev = self._task
            busy = prev is not None and not prev.done()
            if not busy:
                # 직전 긴급정지가 실패(예외)했으면 _stopped latch에도 불구하고 재발화(rank7) — idle
                # dwell/좌표결측은 sep<excl이 계속 참인데 정지가 한 번도 성공 못 했는데 침묵하지 않게.
                failed = (prev is not None and not prev.cancelled()
                          and prev.exception() is not None)
                if not self._stopped or failed:
                    if failed:
                        self.events.log("watchtower",
                                        f"⚠ 태양 긴급정지 실패 — 재시도 ({prev.exception()})", "error")
                    self._stopped = True
                    self._task = self._spawn(self._emergency_stop(sep, moving))
        else:
            self._stopped = False             # 멀어짐 → 재무장

    async def _emergency_stop(self, sep: float | None, moving: bool = True) -> None:
        where = f"태양 {sep:.0f}° 이내" if sep is not None else "주간 위치 불명 상태"
        if moving:
            self.events.log("watchtower",
                            f"⚠ OTA가 {where}에서 이동 중 — 긴급 정지", "error")
        else:
            # 정지해 있는데 태양 근처를 지향(dwell). _halt는 정지·제로레이트·추적off를 모두 발행하나
            # idle 마운트엔 이동을 유발 안 해 무해하다(능동 회피 슬루는 또 다른 태양통과 위험이라
            # 하지 않음). 시스템이 자동 해소 못 하므로 운영자에게 즉시 경보 — 개방형/수동돔은 돔
            # 가림이 없어 즉시 수동 회피 필요.
            self.events.log("watchtower",
                            f"⚠ OTA가 {where}에 정지 지향 중 — 능동 회피 불가, 수동 개입 필요 "
                            f"(인클로저 없으면 광학 직사 위험)", "error")
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
                           params={"sun_sep_deg": round(sep, 1) if sep is not None else None,
                                   "moving": moving},
                           func=lambda: self._exec(_halt))
