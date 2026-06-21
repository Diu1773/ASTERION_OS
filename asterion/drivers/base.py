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


# 연속 조그(MoveAxis) 데드맨 — 클라이언트 keepalive가 이 시간 안에 안 오면 드라이버가
# 축을 0으로 멈춘다(탭 종료·네트워크 단절·pointerup 유실에도 가대가 폭주하지 않게).
# 프론트 keepalive(0.5s)의 ~3배 여유 → 일시적 지연으로 헛멈춤이 나지 않는다.
JOG_DEADMAN_S = 1.6


@dataclass
class MountStatus:
    connected: bool = False
    ra_hours: float | None = None
    dec_degs: float | None = None
    alt_degs: float | None = None
    az_degs: float | None = None
    slewing: bool = False
    tracking: bool = False
    at_park: bool = False
    at_home: bool = False
    can_park: bool = False      # 드라이버가 파킹 지원 (UI 버튼 노출용)
    can_home: bool = False      # 드라이버가 홈 찾기 지원
    homing: bool = False
    stale: bool = False
    coord_source: str = "device"
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
            "at_park": self.at_park, "at_home": self.at_home,
            "can_park": self.can_park, "can_home": self.can_home,
            "homing": self.homing, "stale": self.stale,
            "coord_source": self.coord_source, "detail": self.detail,
        }

    def telemetry(self) -> dict:
        return {"mount.alt": self.alt_degs, "mount.az": self.az_degs}


@dataclass
class CameraStatus:
    connected: bool = False
    ccd_temp_c: float | None = None
    cooler_on: bool = False
    cooler_power: float | None = None   # 쿨러 부하 % (Moravian 등). 실관측은 80% 미만 권장
    state: str = "idle"  # idle / exposing / error
    detail: str = ""
    device_name: str = ""

    def snapshot(self) -> dict:
        return {
            "connected": self.connected, "name": self.device_name,
            "ccd_temp": self.ccd_temp_c, "cooler_on": self.cooler_on,
            "cooler_power": self.cooler_power,
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
    moving: bool = False
    detail: str = ""
    device_name: str = ""

    def snapshot(self) -> dict:
        return {
            "connected": self.connected, "position": self.position,
            "name": self.name, "names": self.names,
            "moving": self.moving, "detail": self.detail,
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
    # 센서값이 마지막으로 *갱신*된 뒤 경과 초(드라이버가 알면). connected=True인데 센서
    # 스테이션이 내부적으로 멈춰 고착값을 돌려주는 경우를 잡는 신선도 신호(ASCOM
    # TimeSinceLastUpdate). None이면 '신선도 미보고' → status가 수신시각을 신선으로 본다.
    reading_age_s: float | None = None

    def snapshot(self) -> dict:
        return {
            "connected": self.connected, "name": self.device_name,
            "temp": self.temp_c, "humidity": self.humidity,
            "dew_point": self.dew_point_c, "wind": self.wind_ms,
            "wind_dir": self.wind_dir_deg, "cloud": self.cloud_score,
            "rain": self.rain, "reading_age_s": self.reading_age_s,
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

    # 연속 조그(velocity slew) — 기본 미지원. 지원 드라이버(sim/ascom)가 오버라이드.
    # axis: 0=primary(RA/Az), 1=secondary(Dec/Alt). rate_deg_s: 부호=방향, 0=그 축 정지.
    # PWI4 등 MoveAxis가 없는 백엔드는 이 기본을 상속 → capabilities()에 can_move_axis가
    # 없어 프론트가 조그 패드를 '비활성(숨김 아님)'으로 표시한다.
    def move_axis(self, axis: int, rate_deg_s: float) -> None:
        raise NotImplementedError("이 마운트는 연속 조그를 지원하지 않습니다")

    # 파킹/홈 — 기본은 미지원. 지원 드라이버(sim/ascom)가 오버라이드한다.
    def park(self) -> None:
        raise NotImplementedError("이 마운트는 파킹을 지원하지 않습니다")

    def unpark(self) -> None:
        raise NotImplementedError("이 마운트는 언파킹을 지원하지 않습니다")

    def find_home(self) -> None:
        raise NotImplementedError("이 마운트는 홈 찾기를 지원하지 않습니다")

    def set_park(self) -> None:
        raise NotImplementedError("이 마운트는 파킹 위치 설정을 지원하지 않습니다")

    def close(self) -> None:
        pass


class CameraDriver(abc.ABC):
    is_sim = False
    # expose()가 *실제로* 적용한 비닝(MaxBin 클램프·실패 폴백 반영). capture가 이 값을
    # Frame/FITS에 기록한다 — 요청값과 다를 수 있어 보정 마스터 매칭이 어긋나지 않게.
    last_binning: int = 1

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def status(self) -> CameraStatus: ...

    @abc.abstractmethod
    def expose(self, seconds: float, light: bool = True,
               binning: int = 1) -> np.ndarray:
        """노출 완료까지 블로킹, uint16 2D 배열 반환.
        binning: NxN 하드웨어 비닝(기본 1). 미지원 드라이버는 1로 동작."""

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


@dataclass
class DomeStatus:
    connected: bool = False
    shutter: str = "unknown"        # open|closed|opening|closing|unknown|error
    azimuth: float | None = None    # 현재 돔 방위 (피드백 없으면 None 또는 추정치)
    azimuth_estimated: bool = False  # 추측항법 추정치면 True (엔코더/피드백 없음)
    target_azimuth: float | None = None
    moving: bool = False
    slaved: bool = False
    aligned: bool | None = None     # 슬릿 정렬? 모르면 None, ROR/클램셸=항상 True
    at_park: bool = False
    at_home: bool = False
    # ── capability (UI·safety·orchestrator가 보고 분기; 방식 불문) ──
    has_shutter: bool = False           # 셔터(또는 지붕) 존재
    can_command_shutter: bool = False   # SW로 개폐 가능?  (수동 셔터=False → 안전은 경보로)
    can_rotate: bool = False            # 회전 모터 있음 (수동 조그 가능)
    can_slew_azimuth: bool = False      # 절대 방위로 갈 수 있음 (피드백 보유)
    can_slave: bool = False             # 마운트 자동추종 가능
    detail: str = ""
    device_name: str = ""

    def snapshot(self) -> dict:
        return {
            "connected": self.connected, "name": self.device_name,
            "shutter": self.shutter, "azimuth": _round(self.azimuth, 2),
            "azimuth_estimated": self.azimuth_estimated,
            "target_azimuth": _round(self.target_azimuth, 2),
            "moving": self.moving, "slaved": self.slaved, "aligned": self.aligned,
            "at_park": self.at_park, "at_home": self.at_home,
            "has_shutter": self.has_shutter,
            "can_command_shutter": self.can_command_shutter,
            "can_rotate": self.can_rotate, "can_slew_azimuth": self.can_slew_azimuth,
            "can_slave": self.can_slave, "detail": self.detail,
        }

    def telemetry(self) -> dict:
        return {"dome.azimuth": self.azimuth}


class DomeDriver(abc.ABC):
    """돔/지붕 추상. 인터페이스는 '의도'(열기/닫기/정렬/영점)만 안다 — 엔코더·레이저·
    CCTV·수동·추측항법 등 '방식'은 어댑터 안에 격리된다. 미지원 동작은
    NotImplementedError를 던지고, capability 플래그(DomeStatus.can_*)로 광고한다."""

    is_sim = False

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def status(self) -> DomeStatus: ...

    # 셔터 — 기본 미지원(수동 돔). 전동 돔이 오버라이드. can_command_shutter로 광고.
    def open_shutter(self) -> None:
        raise NotImplementedError("이 돔은 셔터를 SW로 열 수 없습니다 (수동)")

    def close_shutter(self) -> None:
        raise NotImplementedError("이 돔은 셔터를 SW로 닫을 수 없습니다 (수동)")

    # 회전 — 절대 방위(피드백 보유) / 수동·개루프 조그
    def slew_to_azimuth(self, az_deg: float) -> None:
        raise NotImplementedError("이 돔은 절대 방위 이동을 지원하지 않습니다")

    def rotate(self, direction: str) -> None:
        """수동/개루프 조그. direction: 'cw' | 'ccw' | 'stop'."""
        raise NotImplementedError("이 돔은 회전을 지원하지 않습니다")

    def sync_azimuth(self, az_deg: float) -> None:
        """현재 방위를 az_deg로 영점화 (추측항법 기준점 — '지금 남쪽=180')."""
        raise NotImplementedError("이 돔은 방위 영점화를 지원하지 않습니다")

    def set_slaved(self, on: bool) -> None:
        """드라이버 내부 슬레이빙 on/off (자체 슬레이빙 돔용). 외부 계산형은
        ASTERION이 slew_to_azimuth로 추종하므로 미지원이어도 된다."""
        raise NotImplementedError("이 돔은 내부 슬레이빙을 지원하지 않습니다")

    def park(self) -> None:
        raise NotImplementedError("이 돔은 파킹을 지원하지 않습니다")

    def find_home(self) -> None:
        raise NotImplementedError("이 돔은 홈 찾기를 지원하지 않습니다")

    def stop(self) -> None:
        """모든 움직임 즉시 정지 (안전). 기본 no-op — 회전 돔이 오버라이드."""

    def close(self) -> None:
        pass
