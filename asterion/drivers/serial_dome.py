"""SerialDome — 시리얼 CW/CCW/STOP 돔 (청람 현 상태) + 추측항법.

청람 돔은 엔코더가 없고 시리얼로 CW/CCW/STOP만 보낸다. 셔터는 수동
(can_command_shutter=False → 안전 레이어가 자동닫힘 대신 운영자 경보로 처리).

방위는 **추측항법(dead reckoning)**: 회전 명령 시각 × 회전속도로 추정한다.
피드백이 없어 드리프트가 누적되므로 sync_azimuth('지금 남쪽=180')로 재영점한다.
홈 센서를 달면(향후) 그 통과 시 자동 sync하도록 이 어댑터만 고치면 된다 —
인터페이스·상위 레이어 불변.

⚠️ 시리얼 명령 문자열(cmd_cw/ccw/stop)·종단자·회전속도는 config 기본값이며
**실제 청람 컨트롤러 프로토콜로 검증·교정해야 한다**(하드웨어 접속 시).
"""

from __future__ import annotations

import threading
import time

from ..core.dome_geometry import azimuth_error
from .base import DomeDriver, DomeStatus


class SerialDome(DomeDriver):
    is_sim = False

    def __init__(self, port: str = "", baud: int = 9600,
                 rot_speed_deg_s: float = 3.0, *,
                 cmd_cw: str = "CW", cmd_ccw: str = "CCW", cmd_stop: str = "STOP",
                 terminator: str = "\n", initial_az: float = 0.0):
        self._port = port
        self._baud = int(baud)
        self._speed = max(0.1, float(rot_speed_deg_s))   # 캘리브 필요
        self._cmd = {"cw": cmd_cw, "ccw": cmd_ccw, "stop": cmd_stop}
        self._term = terminator
        self._ser = None
        self._believed = float(initial_az) % 360.0
        self._rotating: tuple[str, float] | None = None   # (방향, 마지막 적분 시각)
        self._lock = threading.Lock()

    # ---------- 연결 ----------

    def connect(self) -> None:
        if not self._port:
            raise RuntimeError(
                "돔 시리얼 포트 미설정 — config [drivers.dome] serial_port (예: COM3)")
        try:
            import serial  # pyserial
        except ImportError:
            raise RuntimeError("pyserial 미설치 — pip install pyserial")
        self._ser = serial.Serial(self._port, self._baud, timeout=1)

    def _send(self, key: str) -> None:
        if self._ser is None:
            raise RuntimeError("돔이 연결되지 않았습니다")
        self._ser.write((self._cmd[key] + self._term).encode())

    # ---------- 추측항법 적분 ----------

    def _integrate(self) -> None:
        """회전 중이면 경과시간×속도로 추정 방위를 갱신하고 시계를 재설정."""
        if self._rotating is None:
            return
        direction, t0 = self._rotating
        now = time.time()
        sign = 1.0 if direction == "cw" else -1.0
        self._believed = (self._believed + sign * self._speed * (now - t0)) % 360.0
        self._rotating = (direction, now)

    # ---------- 인터페이스 ----------

    def status(self) -> DomeStatus:
        with self._lock:
            self._integrate()
            return DomeStatus(
                connected=self._ser is not None,
                shutter="unknown",            # 수동 셔터 — 센서 없으면 알 수 없음
                azimuth=round(self._believed, 2), azimuth_estimated=True,
                moving=self._rotating is not None, slaved=False, aligned=None,
                has_shutter=True, can_command_shutter=False,  # 셔터 수동
                can_rotate=True, can_slew_azimuth=True, can_slave=True,
                detail="추측항법(피드백 없음) — 주기적 sync 권장",
                device_name=f"Serial Dome ({self._port})")

    def rotate(self, direction: str) -> None:
        d = str(direction).lower()
        if d not in ("cw", "ccw", "stop"):
            raise ValueError(f"방향은 cw/ccw/stop: {direction}")
        with self._lock:
            self._integrate()
            self._send(d)
            self._rotating = None if d == "stop" else (d, time.time())

    def slew_to_azimuth(self, az_deg: float) -> None:
        """개루프: 목표까지 최단방향으로 돌리고 시간만큼 뒤 정지(추측항법).
        블로킹 — 호출부가 to_thread로 감싼다(필터휠 set_position과 동일)."""
        az_deg %= 360.0
        with self._lock:
            self._integrate()
            err = azimuth_error(self._believed, az_deg)   # +면 CW
            direction = "cw" if err >= 0 else "ccw"
            dur = abs(err) / self._speed
            self._send(direction)
        time.sleep(max(0.0, dur))
        with self._lock:
            self._send("stop")
            self._rotating = None
            self._believed = az_deg   # 명령 완료 → 추정을 목표로(드리프트는 sync로 보정)

    def sync_azimuth(self, az_deg: float) -> None:
        with self._lock:
            self._integrate()
            self._believed = float(az_deg) % 360.0

    def set_slaved(self, on: bool) -> None:
        # 내부 슬레이빙 없음 — ASTERION이 slew_to_azimuth로 추종. no-op로 허용.
        pass

    def stop(self) -> None:
        with self._lock:
            self._integrate()
            if self._ser is not None:
                self._send("stop")
            self._rotating = None

    def close(self) -> None:
        try:
            self.stop()
        except Exception:
            pass
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
