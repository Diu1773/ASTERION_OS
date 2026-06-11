"""드라이버 추상 인터페이스 + 상태 dataclass.

모든 상위 레이어(오토플랫, 상태 샘플러, 액션)는 이 인터페이스만 본다.
프로토콜(PWI4 HTTP / ASCOM COM / 시뮬)이 바뀌어도 위는 그대로다.
드라이버 메서드는 동기(blocking 가능) — 호출부에서 asyncio.to_thread로 감싼다.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

import numpy as np


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


@dataclass
class CameraStatus:
    connected: bool = False
    ccd_temp_c: float | None = None
    cooler_on: bool = False
    state: str = "idle"  # idle / exposing / error
    detail: str = ""


@dataclass
class FilterStatus:
    connected: bool = False
    position: int | None = None
    name: str = ""
    names: list[str] = field(default_factory=list)


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


class MountDriver(abc.ABC):
    is_sim = False

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def status(self) -> MountStatus: ...

    @abc.abstractmethod
    def goto_altaz(self, alt_deg: float, az_deg: float) -> None: ...

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
    def set_cooler(self, on: bool) -> None: ...

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
