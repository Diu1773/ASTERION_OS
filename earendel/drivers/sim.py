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
    MountDriver, MountStatus, WeatherDriver, WeatherStatus,
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
    is_sim = True

    def __init__(self, lat_deg: float, lst_fn: Callable[[], float]):
        self._lat = lat_deg
        self._lst_fn = lst_fn
        self._lock = threading.Lock()
        self._connected = False
        self._alt = 30.0   # 파킹 자세 (낮은 고도 → 오토플랫이 슬루를 시연)
        self._az = 180.0
        self._tracking = False
        self._slew_until = 0.0
        self._target = (30.0, 180.0)

    def connect(self) -> None:
        self._connected = True

    def _tick(self) -> None:
        if self._slew_until and time.time() >= self._slew_until:
            self._alt, self._az = self._target
            self._slew_until = 0.0

    def status(self) -> MountStatus:
        from ..core import ephemeris
        with self._lock:
            self._tick()
            slewing = self._slew_until > 0.0
            ra, dec = ephemeris.altaz_to_radec(self._alt, self._az, self._lat, self._lst_fn())
            return MountStatus(
                connected=self._connected, ra_hours=ra, dec_degs=dec,
                alt_degs=self._alt, az_degs=self._az,
                slewing=slewing, tracking=self._tracking, detail="SIM",
            )

    def goto_altaz(self, alt_deg: float, az_deg: float) -> None:
        with self._lock:
            self._tick()
            dist = math.hypot(alt_deg - self._alt, az_deg - self._az)
            self._target = (alt_deg, az_deg)
            self._slew_until = time.time() + max(1.5, dist / 8.0)  # 8°/s

    def offset_arcsec(self, dra_arcsec: float, ddec_arcsec: float) -> None:
        with self._lock:
            self._tick()
            # 디더: alt/az에 근사 반영, 짧은 정착 시간 시뮬
            self._target = (
                self._alt + ddec_arcsec / 3600.0,
                self._az + dra_arcsec / 3600.0,
            )
            self._slew_until = time.time() + 2.0

    def set_tracking(self, on: bool) -> None:
        with self._lock:
            self._tracking = on

    def stop(self) -> None:
        with self._lock:
            self._tick()
            self._slew_until = 0.0
            self._tracking = False


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
                            name=name, names=list(self._names))

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
                            cooler_on=self._cooler, state=self._state, detail="SIM")

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

    def set_cooler(self, on: bool) -> None:
        self._cooler = on
        self._temp = -10.0 if on else 15.0


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
        )
