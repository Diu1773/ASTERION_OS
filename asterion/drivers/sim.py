"""시뮬레이터 드라이버 — 하드웨어 없이 전체 파이프라인 검증용.

황혼 시뮬(TwilightSim)을 켜면 실제 시각과 무관하게 하늘 밝기가
지수적으로 어두워져 오토플랫의 노출 피드백 루프를 언제든 시험할 수 있다.
"""

from __future__ import annotations

import math
import random
import threading
import time
from typing import Callable

import numpy as np

from .base import (
    CameraDriver, CameraStatus, FilterStatus, FilterWheelDriver,
    FocuserDriver, FocuserStatus, MountDriver, MountStatus,
    WeatherDriver, WeatherStatus,
)


class TwilightSim:
    """가짜 황혼 — 켜는 순간을 t0으로 하늘 밝기가 e-folding으로 감쇠."""

    def __init__(self, efold_s: float = 240.0):
        self.enabled = False
        self.t0 = 0.0
        self.efold_s = efold_s

    def set(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            self.t0 = time.time()

    def sky_factor(self, sun_alt_deg: float | None) -> float:
        """하늘 밝기 배율. 1.0 = 태양고도 -4° 수준의 박명 하늘."""
        if self.enabled:
            return math.exp(-(time.time() - self.t0) / self.efold_s)
        if sun_alt_deg is None:
            return 0.0
        # 실제 태양고도 기반: -4°에서 1.0, 2°당 약 2.1배 감쇠
        return 10.0 ** (0.42 * (sun_alt_deg + 4.0))


class SimMount(MountDriver):
    """슬루를 연속 보간하고(돔에서 이동이 보이게), 트래킹 시 항성시
    드리프트를 모델링한다 — 위치 값이 1 Hz마다 실제로 바뀐다."""
    is_sim = True
    SLEW_RATE = 6.0   # deg/s

    def __init__(self, lat_deg: float, lst_fn: Callable[[], float]):
        self._lat = lat_deg
        self._lst_fn = lst_fn
        self._lock = threading.Lock()
        self._connected = False
        self._alt = 40.0   # 현재 실제 포인팅
        self._az = 150.0
        self._tracking = False
        self._track_radec: tuple[float, float] | None = None  # 트래킹 중 고정 RA/Dec
        # 슬루: (t0, dur, a0, z0, a1, z1) | None
        self._slew: tuple[float, float, float, float, float, float] | None = None
        self._at_park = False
        self._park_altaz = (0.0, 90.0)    # 기본 파킹 위치 (set_park로 변경 가능)
        self._home_altaz = (45.0, 0.0)

    def connect(self) -> None:
        self._connected = True

    @staticmethod
    def _az_delta(z0: float, z1: float) -> float:
        return ((z1 - z0 + 540.0) % 360.0) - 180.0  # 최단 경로

    def _current_altaz(self) -> tuple[float, float]:
        """슬루/트래킹을 반영한 현재 alt/az. 슬루 완료 시 상태를 정착시킨다."""
        from ..core import ephemeris
        if self._slew is not None:
            t0, dur, a0, z0, a1, z1 = self._slew
            f = min(1.0, max(0.0, (time.time() - t0) / dur))
            alt = a0 + (a1 - a0) * f
            az = (z0 + self._az_delta(z0, z1) * f) % 360.0
            if f >= 1.0:
                self._alt, self._az = a1, z1
                self._slew = None
                if self._tracking:
                    self._track_radec = ephemeris.altaz_to_radec(
                        a1, z1, self._lat, self._lst_fn())
            return alt, az
        if self._tracking and self._track_radec is not None:
            ra, dec = self._track_radec
            return ephemeris.radec_to_altaz(ra, dec, self._lat, self._lst_fn())
        return self._alt, self._az

    def status(self) -> MountStatus:
        from ..core import ephemeris
        with self._lock:
            slewing = self._slew is not None
            alt, az = self._current_altaz()
            ra, dec = ephemeris.altaz_to_radec(alt, az, self._lat, self._lst_fn())
            return MountStatus(
                connected=self._connected, ra_hours=ra, dec_degs=dec,
                alt_degs=alt, az_degs=az,
                slewing=slewing and self._slew is not None,
                tracking=self._tracking, at_park=self._at_park,
                can_park=True, can_home=True, detail="SIM",
                device_name="Sim Telescope",
            )

    def _begin_slew(self, alt1: float, az1: float, min_dur: float = 2.0) -> None:
        a0, z0 = self._current_altaz()
        self._alt, self._az = a0, z0
        self._track_radec = None  # 정착 후 재고정
        self._at_park = False     # 움직이면 파킹 해제 (park()는 슬루 뒤 다시 True)
        dist = math.hypot(alt1 - a0, abs(self._az_delta(z0, az1)))
        dur = max(min_dur, dist / self.SLEW_RATE)
        self._slew = (time.time(), dur, a0, z0, alt1, az1)

    def goto_altaz(self, alt_deg: float, az_deg: float) -> None:
        with self._lock:
            self._begin_slew(alt_deg, az_deg % 360.0)

    def goto_radec(self, ra_hours: float, dec_degs: float) -> None:
        from ..core import ephemeris
        alt, az = ephemeris.radec_to_altaz(ra_hours, dec_degs, self._lat,
                                           self._lst_fn())
        if alt < 5.0:
            raise ValueError(f"대상 고도 {alt:.1f}° — 지평선 근처/아래라 이동 불가")
        with self._lock:
            self._begin_slew(alt, az)

    def offset_arcsec(self, dra_arcsec: float, ddec_arcsec: float) -> None:
        with self._lock:
            a0, z0 = self._current_altaz()
            self._begin_slew(a0 + ddec_arcsec / 3600.0,
                             (z0 + dra_arcsec / 3600.0) % 360.0, min_dur=1.2)

    def set_tracking(self, on: bool) -> None:
        from ..core import ephemeris
        with self._lock:
            self._tracking = on
            if on and self._slew is None:
                a, z = self._current_altaz()
                self._track_radec = ephemeris.altaz_to_radec(
                    a, z, self._lat, self._lst_fn())
            elif not on:
                self._track_radec = None

    def stop(self) -> None:
        with self._lock:
            self._alt, self._az = self._current_altaz()
            self._slew = None
            self._tracking = False
            self._track_radec = None

    def park(self) -> None:
        with self._lock:
            self._tracking = False
            self._begin_slew(self._park_altaz[0], self._park_altaz[1])
            self._at_park = True

    def unpark(self) -> None:
        with self._lock:
            self._at_park = False

    def find_home(self) -> None:
        with self._lock:
            self._tracking = False
            self._begin_slew(self._home_altaz[0], self._home_altaz[1])

    def set_park(self) -> None:
        with self._lock:
            self._park_altaz = self._current_altaz()   # 현재 위치를 파킹 위치로


class SimFilterWheel(FilterWheelDriver):
    is_sim = True

    def __init__(self, names: list[str]):
        self._names = list(names)
        self._pos = 0
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def status(self) -> FilterStatus:
        name = self._names[self._pos] if 0 <= self._pos < len(self._names) else ""
        return FilterStatus(connected=self._connected, position=self._pos,
                            name=name, names=list(self._names),
                            device_name="Sim Filter Wheel")

    def set_position(self, index: int) -> None:
        if not 0 <= index < len(self._names):
            raise ValueError(f"필터 인덱스 범위 밖: {index}")
        time.sleep(0.8)  # 휠 회전 시뮬
        self._pos = index


class SimCamera(CameraDriver):
    is_sim = True

    # 박명 factor=1.0일 때 하늘 신호율 (ADU/s) — 필터별
    SKY_RATE = {"L": 20000.0, "B": 9000.0, "V": 14000.0,
                "R": 16000.0, "I": 11000.0, "Ha": 1500.0}
    BIAS = 500.0

    def __init__(self, width: int, height: int, twilight: TwilightSim,
                 sun_alt_fn: Callable[[], float],
                 filter_name_fn: Callable[[], str],
                 exposure_sleep_cap_s: float = 2.0,
                 saturation: int = 65535):
        self._w, self._h = width, height
        self._twilight = twilight
        self._sun_alt_fn = sun_alt_fn
        self._filter_name_fn = filter_name_fn
        self._sleep_cap = exposure_sleep_cap_s
        self._sat = saturation
        self._connected = False
        self._cooler = True
        self._temp = -10.0
        self._state = "idle"
        self._rng = np.random.default_rng()
        # 약한 비네팅(중심 대비 가장자리 -5%) — 플랫 같은 느낌
        yy, xx = np.mgrid[0:height, 0:width]
        r2 = ((xx - width / 2) ** 2 + (yy - height / 2) ** 2)
        self._vignette = 1.0 - 0.05 * (r2 / r2.max())

    def connect(self) -> None:
        self._connected = True

    def status(self) -> CameraStatus:
        return CameraStatus(connected=self._connected, ccd_temp_c=self._temp,
                            cooler_on=self._cooler, state=self._state,
                            detail="SIM", device_name="Sim Camera")

    def expose(self, seconds: float, light: bool = True) -> np.ndarray:
        self._state = "exposing"
        try:
            time.sleep(min(seconds, self._sleep_cap))
            rate = self.SKY_RATE.get(self._filter_name_fn(), 12000.0)
            factor = self._twilight.sky_factor(self._sun_alt_fn())
            level = self.BIAS + (rate * factor * seconds if light else 0.0)
            level = min(level, float(self._sat) * 1.2)
            img = self._rng.normal(level * self._vignette,
                                   np.sqrt(max(level, 1.0)))
            return np.clip(img, 0, self._sat).astype(np.uint16)
        finally:
            self._state = "idle"

    def set_cooler(self, on: bool, setpoint_c: float | None = None) -> None:
        self._cooler = on
        if on:
            self._temp = float(setpoint_c) if setpoint_c is not None else -10.0
        else:
            self._temp = 15.0


class SimFocuser(FocuserDriver):
    is_sim = True
    SPEED = 1500.0  # steps/s

    def __init__(self, max_position: int = 60000):
        self._connected = False
        self._pos = 30000.0
        self._target = 30000.0
        self._t_last = time.time()
        self._max = max_position
        self._lock = threading.Lock()

    def connect(self) -> None:
        self._connected = True

    def _tick(self) -> None:
        now = time.time()
        dt = now - self._t_last
        self._t_last = now
        if self._pos != self._target:
            step = self.SPEED * dt
            if abs(self._target - self._pos) <= step:
                self._pos = self._target
            else:
                self._pos += step if self._target > self._pos else -step

    def status(self) -> FocuserStatus:
        with self._lock:
            self._tick()
            return FocuserStatus(
                connected=self._connected, position=int(round(self._pos)),
                moving=abs(self._pos - self._target) > 0.5,
                temperature=8.5, max_position=self._max, detail="SIM",
                device_name="Sim Focuser",
            )

    def move_to(self, position: int) -> None:
        with self._lock:
            self._tick()
            self._target = float(max(0, min(self._max, int(position))))


class SimWeather(WeatherDriver):
    is_sim = True

    def __init__(self):
        self._connected = False
        self._temp = 12.0
        self._hum = 55.0
        self._wind = 2.0
        self._wind_dir = 310.0

    def connect(self) -> None:
        self._connected = True

    def read(self) -> WeatherStatus:
        self._temp += random.uniform(-0.05, 0.05)
        self._hum = min(95.0, max(20.0, self._hum + random.uniform(-0.3, 0.3)))
        self._wind = max(0.0, self._wind + random.uniform(-0.15, 0.15))
        self._wind_dir = (self._wind_dir + random.uniform(-3, 3)) % 360.0
        dew = self._temp - (100.0 - self._hum) / 5.0
        return WeatherStatus(
            connected=self._connected, temp_c=round(self._temp, 1),
            humidity=round(self._hum, 1), dew_point_c=round(dew, 1),
            wind_ms=round(self._wind, 1), wind_dir_deg=round(self._wind_dir),
            cloud_score=0.1, rain=False, detail="SIM",
            device_name="Sim Weather",
        )
