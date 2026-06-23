"""SessionWatchdog — 원격 운영자 데드맨 (REMOTE_ACCESS_PLAN Phase C).

원격 *수동* 운영 중 운영자 UI의 하트비트가 끊기면(브라우저 종료·네트워크 단절·세션 만료)
관측소를 세이프-스테이트로 떨어뜨린다: 추적 정지 → 슬루 정지 → (전동)돔 닫힘 → 파킹(드라이버가
park 미지원=PWI4 등이면 안전 stow 위치 goto 폴백, 둘 다 없으면 정지만 하고 정직하게 격상).
곁에서 물리 E-stop을 누를 사람이 없는 원격 운영의 최후 안전장치.

설계 원칙(자율 설계 존중 + 기존 흐름 불가침):
  · **NightRunner 무인 운영 중이면 발화 안 함** — 무인은 사람 부재가 정상이고 자체 안전 보유.
  · **하트비트를 한 번도 못 받았으면 무장 안 함** — 로컬/헤드리스/직접-API 운영을 깨지 않는다.
  · **보호할 위험이 있을 때만 발화** — 돔 열림 OR 추적/슬루 OR 수동 세션 진행 중. 이미 안전이면
    무동작(잠든 대시보드를 닫았다고 한밤중에 파킹하지 않음).
  · 에피소드당 1회 발화(하트비트 재개 시 재무장). 행동은 ActionBus 통과(DomeGuard/SolarWatchdog 동형).
  · 발화 시 셔터가 열렸는데 SW로 못 닫는 수동 셔터면 경보를 별도 rule_id(session_deadman_
    shutter_stuck)로 격상 — 사람이 즉시 슬릿을 닫으러 가야 하는 최악 케이스를 정상 데드맨과 구분.
  · config로 끈다(기본 off) — 켜야 동작. 안전 '판정'(safety.evaluate)은 건드리지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable


class SessionWatchdog:
    def __init__(self, drivers: dict[str, Any], bus: Any, events: Any, cfg: Any,
                 *, alert_fn: Callable[..., Any] | None = None, safety_pool: Any = None):
        self.drivers = drivers
        self.bus = bus
        self.events = events
        self.cfg = cfg
        self._alert = alert_fn
        self._pool = safety_pool               # 전용 안전 액추에이터 풀(rank8) — 없으면 to_thread
        self._last_hb: float | None = None     # 마지막 하트비트 monotonic (None=미무장)
        self._fired = False                    # 에피소드당 1회 발화 디바운스
        self._task: asyncio.Task | None = None

    def _exec(self, fn):
        """세이프-스테이트 블로킹 호출 — 전용 안전 풀이 있으면 거기서(상태 폴링 풀과 격리). 없으면 to_thread."""
        if self._pool is not None:
            return asyncio.get_running_loop().run_in_executor(self._pool, fn)
        return asyncio.to_thread(fn)

    # ---------- 하트비트 (엔드포인트가 호출 — 고빈도, 감사 안 함) ----------

    def heartbeat(self) -> None:
        self._last_hb = time.monotonic()
        self._fired = False                    # 하트비트 재개 → 재무장

    def _enabled(self) -> bool:
        return bool(self.cfg.get("safety.session_deadman.enabled", False))

    def _timeout(self) -> float:
        return float(self.cfg.get("safety.session_deadman.timeout_seconds", 120.0))

    def status(self) -> dict:
        age = None if self._last_hb is None else round(time.monotonic() - self._last_hb, 1)
        return {"enabled": self._enabled(), "armed": self._last_hb is not None,
                "age_s": age, "timeout_s": self._timeout(), "fired": self._fired}

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        t.add_done_callback(lambda x: x.cancelled() or x.exception())
        return t

    # ---------- 틱 (샘플러 safety_actuator가 매 스냅샷 호출) ----------

    async def __call__(self, snap: dict) -> None:
        if not self._enabled() or self._last_hb is None or self._fired:
            return
        if (snap.get("night_runner") or {}).get("active"):
            return                              # 무인 운영 — 데드맨 면제
        if time.monotonic() - self._last_hb < self._timeout():
            return                              # 아직 신선
        if not self._risky(snap):
            return                              # 보호할 위험 없음 → 무동작
        busy = self._task is not None and not self._task.done()
        if busy:
            return
        self._fired = True
        self._task = self._spawn(
            self._safe_state(time.monotonic() - self._last_hb, snap))

    @staticmethod
    def _risky(snap: dict) -> bool:
        mount = snap.get("mount") or {}
        dome = snap.get("dome") or {}
        dome_open = (dome.get("shutter") in ("open", "opening")
                     or dome.get("open") is True)
        return bool(dome_open or mount.get("tracking") or mount.get("slewing")
                    or (snap.get("capture") or {}).get("active")
                    or (snap.get("autoflat") or {}).get("running")
                    or (snap.get("orchestrator") or {}).get("running"))

    async def _safe_state(self, age: float, snap: dict) -> None:
        dome_snap = snap.get("dome") or {}
        dome_open = (dome_snap.get("shutter") in ("open", "opening")
                     or dome_snap.get("open") is True)
        dome_can_close = bool(dome_snap.get("can_command_shutter"))
        # 열려 있는데 SW로 못 닫는 수동 셔터 — 데드맨이 슬릿을 닫을 수 없는 최악 케이스.
        # 정지·파킹은 그대로 하되(추적 켠 채 두는 것보다 안전), 사람이 즉시 가야 하므로
        # 경보를 별도 rule_id로 분리·격상한다(정상 데드맨=돔 자동닫힘=완전안전과 구분).
        shutter_stuck = dome_open and not dome_can_close

        self.events.log(
            "watchtower",
            f"⚠ 원격 운영자 하트비트 {age:.0f}s 끊김 — 세이프-스테이트 전환 "
            "(추적·슬루 정지 → 돔 닫기 → 파킹/stow)", "error")
        mount = self.drivers.get("mount")
        dome = self.drivers.get("dome")
        # park 미지원 마운트(PWI4 등)는 park()가 NotImplementedError를 던진다 — 조용히 삼켜
        # '파킹 완료'로 단언하지 않고(phantom-park), 설정된 안전 stow 위치(safety.stow_altaz=
        # [alt,az])로 goto 폴백한다. park도 stow도 없으면 정지만 하고 정직하게 격상(no_park).
        stow = self.cfg.get("safety.stow_altaz", None)
        outcome = {"parked": False, "stowed": False}

        def _act():
            # best-effort, 각각 가드(하나 실패해도 나머지 시도). 정지 → 돔닫힘 → 파킹/stow 순.
            if mount is not None:
                for fn in (lambda: mount.set_tracking(False), mount.stop):
                    try:
                        fn()
                    except Exception:
                        pass
            if dome is not None and dome_can_close:
                try:
                    dome.close_shutter()
                except Exception:
                    pass
            if mount is not None:
                try:
                    mount.park()
                    outcome["parked"] = True
                except NotImplementedError:
                    if stow and len(stow) == 2:    # park 미지원 → 안전 stow 위치로 goto 폴백
                        try:
                            mount.goto_altaz(float(stow[0]), float(stow[1]))
                            outcome["stowed"] = True
                        except Exception:
                            pass
                except Exception:
                    pass

        await self.bus.run("session_deadman_safe_state", actor="watchtower",
                           params={"heartbeat_age_s": round(age, 1),
                                   "shutter_stuck_open": shutter_stuck},
                           func=lambda: self._exec(_act))
        # 정직 로깅 — 실제 수행분만 반영(phantom-park 금지).
        no_park = not outcome["parked"] and not outcome["stowed"]
        if outcome["stowed"]:
            self.events.log("watchtower",
                            f"가대 파킹 미지원 — 안전 stow(alt {float(stow[0]):.0f}, "
                            f"az {float(stow[1]):.0f})로 이동 완료", "warn")
        elif no_park:
            self.events.log("watchtower",
                            "⚠ 가대 파킹 미지원 + 안전 stow 미설정 — 정지만 수행, 가대가 하늘 "
                            "좌표에 잔류(추적off). 현장 확인 필요", "error")
        if self._alert is not None:
            try:
                if shutter_stuck:
                    self._alert(
                        "⚠ 원격 데드맨 — 수동 셔터 폐쇄 불가",
                        f"운영자 하트비트 {age:.0f}s 끊김 + 돔 셔터 열림(수동) — "
                        "자동으로 못 닫음, 즉시 현장 조치 필요",
                        rule_id="session_deadman_shutter_stuck")
                elif no_park:
                    self._alert(
                        "⚠ 원격 데드맨 — 파킹 미지원",
                        f"운영자 하트비트 {age:.0f}s 끊김 — 가대 파킹 미지원(드라이버)·안전 stow "
                        "미설정으로 정지만 수행. 가대가 하늘 좌표에 잔류 — 현장 확인 필요",
                        rule_id="session_deadman_no_park")
                else:
                    self._alert(
                        "원격 세션 데드맨 발화",
                        f"운영자 하트비트 {age:.0f}s 끊김 — 세이프-스테이트 전환",
                        rule_id="session_deadman")
            except Exception:
                pass
