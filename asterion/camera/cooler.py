"""쿨러 램프 — Max ΔT 안전 거버너.

CCD/CMOS 센서는 급격한 온도변화에 열충격으로 손상될 수 있다. 운영자가
`setup.camera.max_dt_c`(°C/min)를 지정하면 쿨러 '명령 셋포인트'를 그 속도 이하로만
단계적으로 올/내려(쿨다운·웜업 모두) 센서를 보호한다. 특히 웜업: 차가운 상태에서
쿨러를 그냥 끄지 않고 주변온도(warm_c)까지 점진적으로 올린 뒤 끈다(MaximDL/NINA 표준).

설계: 거버너는 '명령 셋포인트(_commanded)'를 목표(쿨다운=setpoint, 웜업=warm_c)를
향해 1틱마다 `max_dt*dt/60`만큼 이동시키며 `cam.set_cooler(True, _commanded)`를
호출한다. 하드웨어/sim의 실제 온도는 명령을 따라간다. `max_dt_c` 미설정 시 즉시 적용
(기존 동작 보존). 틱은 StatusSampler 1 Hz 루프가 구동(별도 루프 불필요).

동시성: request()는 스레드풀(asyncio.to_thread)에서, tick()은 이벤트루프 코루틴에서
실행되어 서로 다른 스레드로 동시 진입할 수 있다 → 상태(_mode/_target/_commanded/
_last_mono)는 threading.Lock으로 보호한다. 단, 블로킹 가능한 set_cooler(COM)는 락
밖에서 호출한다(멈춘 워커에 락을 잡힌 채 묶이지 않게). asyncio.Lock이 아닌 threading.
Lock인 이유: request()가 이벤트루프 밖(스레드풀)에서 돌기 때문.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any


class CoolerController:
    def __init__(self, cfg: Any, drivers: dict, events: Any):
        self.cfg = cfg
        self.drivers = drivers
        self.events = events
        self._lock = threading.Lock()
        self._want_on = False
        self._target: float | None = None       # 운영자 셋포인트(°C)
        self._commanded: float | None = None     # 현재 명령 셋포인트(°C)
        self._mode = "idle"                       # idle|cooling|holding|warming
        self._last_mono: float | None = None      # dt 측정 기준(monotonic)

    # ---------- 설정값 ----------
    def _max_dt(self) -> float | None:
        """°C/min. 미설정/0/음수면 None(거버너 비활성 → 즉시 적용)."""
        try:
            v = float(self.cfg.get("setup.camera.max_dt_c", None))
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    def _warm_c(self) -> float:
        """웜업 목표(끄기 전 도달). 설정 없으면 +15°C."""
        try:
            return float(self.cfg.get("setup.camera.warm_c", 15.0))
        except (TypeError, ValueError):
            return 15.0

    def _status_unlocked(self) -> dict:
        return {
            "mode": self._mode,
            "ramping": self._mode in ("cooling", "warming"),
            "target": self._target,
            "commanded": (None if self._commanded is None
                          else round(self._commanded, 1)),
            "max_dt_c": self._max_dt(),
            "want_on": self._want_on,
        }

    def status_dict(self) -> dict:
        with self._lock:
            return self._status_unlocked()

    # ---------- 운영자 요청 (엔드포인트, 스레드풀) ----------
    def request(self, on: bool, setpoint: float | None) -> dict:
        """쿨러 의도 설정. 거버너 활성이면 틱이 점진 적용, 비활성이면 즉시 적용."""
        op: tuple[bool, float | None] | None = None   # 락 밖에서 실행할 하드웨어 명령
        with self._lock:
            self._want_on = bool(on)
            if self._max_dt() is None:                # 거버너 비활성 — 기존 즉시 동작
                op = (bool(on), setpoint)
                self._target = (float(setpoint) if (on and setpoint is not None)
                                else None)
                self._commanded = self._target if on else None
                self._mode = "holding" if on else "idle"
            elif on:
                if setpoint is not None:
                    self._target = float(setpoint)
                if self._target is None:              # 셋포인트 없음 → 램프 목표 불명:
                    op = (True, None)                 # 쿨러만 ON(카메라 기본 셋포인트)
                    self._mode = "holding"
                    self._commanded = None
                else:
                    self._mode = "cooling"
                    self._last_mono = None            # dt 리셋 — 다음 틱이 기준
            else:                                     # 끄기 → 웜업 후 OFF
                self._mode = "warming"
                self._last_mono = None
            status = self._status_unlocked()
        if op is not None:                            # COM은 락 밖에서
            self.drivers["camera"].set_cooler(op[0], op[1])
        return status

    # ---------- 1 Hz 틱 (StatusSampler, 이벤트루프) ----------
    async def tick(self, snap: dict) -> None:
        cam = (snap or {}).get("camera") or {}
        connected = bool(cam.get("connected"))
        temp = cam.get("ccd_temp")
        now = time.monotonic()
        ops: list[tuple[bool, float | None]] = []     # 락 밖에서 실행할 명령들
        done: tuple[str, float] | None = None

        with self._lock:
            if self._mode not in ("cooling", "warming"):
                return
            max_dt = self._max_dt()
            if max_dt is None:
                # 거버너가 틱 도중 비활성화됨 → 즉시 완료(중간 셋포인트에 멈추지 않게)
                if self._mode == "warming":
                    ops.append((False, None))
                    self._mode, self._commanded, self._target = "idle", None, None
                else:
                    if self._target is not None:
                        ops.append((True, self._target))
                        self._commanded = self._target
                    self._mode = "holding"
            elif not connected:
                self._last_mono = None                # 재연결 후 dt 점프 방지(재프라임)
                return
            elif self._last_mono is None:             # 요청 후 첫 틱 — 명령값을 현재온도로
                self._last_mono = now
                if self._commanded is None and temp is not None:
                    self._commanded = float(temp)
                return
            else:
                dt = now - self._last_mono
                self._last_mono = now
                if self._commanded is None:
                    self._commanded = (float(temp) if temp is not None
                                       else (self._target if self._target is not None else 0.0))
                step = max_dt * dt / 60.0
                if self._mode == "warming":
                    warm = self._warm_c()
                    if self._commanded >= warm:       # 이미 충분히 따뜻 → 바로 OFF
                        reached = True
                    else:
                        self._commanded = min(warm, self._commanded + step)  # 위로만
                        reached = self._commanded >= warm
                    if reached:
                        ops.append((False, None))
                        self._mode, self._commanded, self._target = "idle", None, None
                        done = ("warm", warm)
                    else:
                        ops.append((True, round(self._commanded, 2)))
                else:                                 # cooling (목표가 위/아래 모두 가능)
                    eff = (self._target if self._target is not None
                           else self._commanded)
                    if abs(eff - self._commanded) <= step:
                        self._commanded = eff
                        reached = True
                    else:
                        self._commanded += step if eff > self._commanded else -step
                        reached = False
                    ops.append((True, round(self._commanded, 2)))
                    if reached:
                        self._mode = "holding"
                        done = ("cool", eff)

        for on, sp in ops:                            # COM은 락 밖에서
            await asyncio.to_thread(self.drivers["camera"].set_cooler, on, sp)
        if done is not None:
            kind, t = done
            self.events.log("camera",
                            f"웜업 완료 — 쿨러 OFF ({t:.0f}°C 도달)" if kind == "warm"
                            else f"쿨다운 완료 — {t:.0f}°C 유지")
