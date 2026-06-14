"""드라이버 추상 인터페이스 + 상태 dataclass.

모든 상위 레이어(오토플랫, 상태 샘플러, 액션)는 이 인터페이스만 본다.
프로토콜(PWI4 HTTP / ASCOM COM / 시뮬)이 바뀌어도 위는 그대로다.
드라이버 메서드는 동기(blocking 가능) — 호출부에서 asyncio.to_thread로 감싼다.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

import numpy as np


# device_name = 장비가 스스로 보고하는 표시명 (ASCOM .Name = "Moravian C3-61000",
# PWI4 = "PlaneWave PWI4", 시뮬 = "Sim ..."). 패널 헤더가 이걸 자동 표시한다.

# 각 XxxStatus는 두 가지를 자기서술한다 (제네릭 샘플러가 장치를 몰라도 읽게):
#   snapshot()  → 대시보드/WebSocket용 dict (프론트가 읽는 키 형태 그대로)
#   telemetry() → 시계열 플롯용 device-prefixed 수치 키 (예: "mount.alt")
# 새 장비를 추가해도 샘플러는 이 두 메서드만 호출하므로 코드 변경이 없다.

def _round(v: float | None, n: int) -> float | None:
    return None if v is None else round(v, n)


@dataclass
class MountStatus:
    connected: bool = False
    ra_hours: float | None = None
    dec_degs: float | None = None
    alt_degs: float | None = None
    az_degs: float | None = None
    slewing: bool = False
    tracking: bool = False
    detail: str = ""
    device_name: str = ""

    def snapshot(self) -> dict:
        from ..core import ephemeris
        return {
            "connected": self.connected, "name": self.device_name,
            "alt": _round(self.alt_degs, 3), "az": _round(self.az_degs, 3),
            "ra_hours": self.ra_hours, "dec_degs": self.dec_degs,
            "ra_str": ephemeris.fmt_ra_hours(self.ra_hours),
            "dec_str": ephemeris.fmt_dec_degs(self.dec_degs),
            "slewing": self.slewing, "tracking": self.tracking,
            "detail": self.detail,
        }

    def telemetry(self) -> dict:
        return {"mount.alt": self.alt_degs, "mount.az": self.az_degs}


@dataclass
class CameraStatus:
    connected: bool = False
    ccd_temp_c: float | None = None
    cooler_on: bool = False
    state: str = "idle"  # idle / exposing / error
    detail: str = ""
    device_name: str = ""

    def snapshot(self) -> dict:
        return {
            "connected": self.connected, "name": self.device_name,
            "ccd_temp": self.ccd_temp_c, "cooler_on": self.cooler_on,
            "state": self.state, "detail": self.detail,
        }

    def telemetry(self) -> dict:
        return {"camera.ccd_temp": self.ccd_temp_c}


@dataclass
class FilterStatus:
    connected: bool = False
    position: int | None = None
    name: str = ""              # 현재 필터 이름 (장비명 아님)
    names: list[str] = field(default_factory=list)
    device_name: str = ""

    def snapshot(self) -> dict:
        return {
            "connected": self.connected, "position": self.position,
            "name": self.name, "names": self.names,
            "device_name": self.device_name,
        }

    def telemetry(self) -> dict:
        return {}


@dataclass
class FocuserStatus:
    connected: bool = False
    position: int | None = None
    moving: bool = False
    temperature: float | None = None
    max_position: int = 60000
    detail: str = ""
    device_name: str = ""

    def snapshot(self) -> dict:
        return {
            "connected": self.connected, "name": self.device_name,
            "position": self.position, "moving": self.moving,
            "temperature": self.temperature, "max_position": self.max_position,
            "detail": self.detail,
        }

    def telemetry(self) -> dict:
        return {"focuser.position": self.position,
                "focuser.temp": self.temperature}


@dataclass
class WeatherStatus:
    connected: bool = False
    temp_c: float | None = None
    humidity: float | None = None
    dew_point_c: float | None = None
    wind_ms: float | None = None
    wind_dir_deg: float | None = None
    cloud_score: float | None = None
    rain: bool = False
    detail: str = ""
    device_name: str = ""

    def snapshot(self) -> dict:
        return {
            "connected": self.connected, "name": self.device_name,
            "temp": self.temp_c, "humidity": self.humidity,
            "dew_point": self.dew_point_c, "wind": self.wind_ms,
            "wind_dir": self.wind_dir_deg, "cloud": self.cloud_score,
            "rain": self.rain,
        }

    def telemetry(self) -> dict:
        return {"weather.temp": self.temp_c, "weather.humidity": self.humidity,
                "weather.dew_point": self.dew_point_c, "weather.wind": self.wind_ms,
                "weather.cloud": self.cloud_score}


class MountDriver(abc.ABC):
    is_sim = False

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def status(self) -> MountStatus: ...

    @abc.abstractmethod
    def goto_altaz(self, alt_deg: float, az_deg: float) -> None: ...

    @abc.abstractmethod
    def goto_radec(self, ra_hours: float, dec_degs: float) -> None: ...

    @abc.abstractmethod
    def offset_arcsec(self, dra_arcsec: float, ddec_arcsec: float) -> None: ...

    @abc.abstractmethod
    def set_tracking(self, on: bool) -> None: ...

    @abc.abstractmethod
    def stop(self) -> None: ...

    def close(self) -> None:
        pass


class CameraDriver(abc.ABC):
    is_sim = False

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def status(self) -> CameraStatus: ...

    @abc.abstractmethod
    def expose(self, seconds: float, light: bool = True) -> np.ndarray:
        """노출 완료까지 블로킹, uint16 2D 배열 반환."""

    @abc.abstractmethod
    def set_cooler(self, on: bool, setpoint_c: float | None = None) -> None: ...

    def close(self) -> None:
        pass


class FilterWheelDriver(abc.ABC):
    is_sim = False

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def status(self) -> FilterStatus: ...

    @abc.abstractmethod
    def set_position(self, index: int) -> None:
        """이동 완료까지 블로킹."""

    def close(self) -> None:
        pass


class WeatherDriver(abc.ABC):
    is_sim = False

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def read(self) -> WeatherStatus: ...

    def close(self) -> None:
        pass


class FocuserDriver(abc.ABC):
    is_sim = False

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def status(self) -> "FocuserStatus": ...

    @abc.abstractmethod
    def move_to(self, position: int) -> None:
        """목표 스텝으로 이동 시작 (논블로킹 — status().moving으로 추적)."""

    def close(self) -> None:
        pass
