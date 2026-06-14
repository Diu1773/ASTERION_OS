"""ASCOM COM 드라이버 (카메라 / 필터휠 / 포커서) — Moravian C3-61000 등.

COM 객체는 생성된 STA 스레드에서만 안전하므로, 장비당 단일 워커
스레드(executor)에서 모든 호출을 직렬 실행한다. ProgID는
scripts/choose_ascom.py 로 선택해 config.toml에 넣는다.
real 모드 + pywin32 설치 환경에서만 import된다.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import math

from .base import (
    CameraDriver, CameraStatus, FilterStatus, FilterWheelDriver,
    FocuserDriver, FocuserStatus, MountDriver, MountStatus,
    WeatherDriver, WeatherStatus,
)

_HINT = "ASCOM ProgID 미설정 — asterion/scripts/choose_ascom.py 실행 후 config.toml에 입력"


def _com_executor() -> ThreadPoolExecutor:
    def _init():
        import pythoncom
        pythoncom.CoInitialize()
    return ThreadPoolExecutor(max_workers=1, initializer=_init,
                              thread_name_prefix="ascom")


class AscomMount(MountDriver):
    """ASCOM Telescope (ITelescopeV3) — RST-135 등 표준 가대.
    PWI4가 아닌 ASCOM 마운트는 이 백엔드로 붙는다 (ProgID는 자동 발견)."""

    def __init__(self, progid: str):
        self._progid = progid
        self._ex = _com_executor()
        self._dev = None
        self._name = ""

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        if not self._progid:
            raise RuntimeError(_HINT)
        def _do():
            import win32com.client
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
            try:
                self._name = str(self._dev.Name)
            except Exception:
                self._name = self._progid
        self._call(_do)

    def status(self) -> MountStatus:
        if self._dev is None:
            return MountStatus(connected=False,
                               detail=_HINT if not self._progid else "미연결",
                               device_name=self._name)
        def _do():
            d = self._dev
            def g(prop):
                try:
                    return float(getattr(d, prop))
                except Exception:
                    return None
            def b(prop):
                try:
                    return bool(getattr(d, prop))
                except Exception:
                    return False
            return MountStatus(
                connected=bool(d.Connected),
                ra_hours=g("RightAscension"), dec_degs=g("Declination"),
                alt_degs=g("Altitude"), az_degs=g("Azimuth"),
                slewing=b("Slewing"), tracking=b("Tracking"),
                at_park=b("AtPark"), can_park=b("CanPark"),
                can_home=b("CanFindHome"),
                detail=self._progid, device_name=self._name)
        try:
            return self._call(_do)
        except Exception as exc:
            return MountStatus(connected=False, detail=f"ASCOM 오류: {exc}",
                               device_name=self._name)

    def goto_altaz(self, alt_deg: float, az_deg: float) -> None:
        def _do():
            d = self._dev
            try:
                d.SlewToAltAzAsync(az_deg, alt_deg)   # ASCOM 순서: (az, alt)
            except Exception:
                d.SlewToAltAz(az_deg, alt_deg)
        self._call(_do)

    def goto_radec(self, ra_hours: float, dec_degs: float) -> None:
        def _do():
            d = self._dev
            try:
                if getattr(d, "CanSetTracking", False) and not d.Tracking:
                    d.Tracking = True
            except Exception:
                pass
            try:
                d.SlewToCoordinatesAsync(ra_hours, dec_degs)
            except Exception:
                d.SlewToCoordinates(ra_hours, dec_degs)
        self._call(_do)

    def offset_arcsec(self, dra_arcsec: float, ddec_arcsec: float) -> None:
        def _do():
            d = self._dev
            ra = float(d.RightAscension)
            dec = float(d.Declination)
            cosd = max(0.1, math.cos(math.radians(dec)))
            ra2 = (ra + (dra_arcsec / 3600.0) / 15.0 / cosd) % 24.0
            dec2 = max(-89.9, min(89.9, dec + ddec_arcsec / 3600.0))
            try:
                d.SlewToCoordinatesAsync(ra2, dec2)
            except Exception:
                d.SlewToCoordinates(ra2, dec2)
        self._call(_do)

    def set_tracking(self, on: bool) -> None:
        self._call(lambda: setattr(self._dev, "Tracking", bool(on)))

    def stop(self) -> None:
        self._call(lambda: self._dev.AbortSlew())

    def park(self) -> None:
        self._call(lambda: self._dev.Park())

    def unpark(self) -> None:
        self._call(lambda: self._dev.Unpark())

    def find_home(self) -> None:
        self._call(lambda: self._dev.FindHome())

    def set_park(self) -> None:
        self._call(lambda: self._dev.SetPark())   # 현재 위치를 파킹 위치로 저장

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._call(lambda: setattr(self._dev, "Connected", False))
        finally:
            self._ex.shutdown(wait=False)


class AscomCamera(CameraDriver):
    # ProgID가 비어 있어도 생성은 허용 — REAL 전환을 막지 않는다.
    # 실제 연결/노출 시점에 안내하고, status()는 '미연결'로 정직하게 보고.
    def __init__(self, progid: str, saturation: int = 65535):
        self._progid = progid
        self._sat = saturation
        self._ex = _com_executor()
        self._dev = None
        self._state = "idle"
        self._name = ""

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        if not self._progid:
            raise RuntimeError(_HINT)
        def _do():
            import win32com.client
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
            try:
                self._name = str(self._dev.Name)  # 예: "Moravian C3-61000"
            except Exception:
                self._name = self._progid
        self._call(_do)

    def status(self) -> CameraStatus:
        if self._dev is None:
            return CameraStatus(connected=False,
                                detail=_HINT if not self._progid else "미연결",
                                device_name=self._name)
        def _do():
            d = self._dev
            temp = None
            try:
                temp = float(d.CCDTemperature)
            except Exception:
                pass
            cooler = False
            try:
                cooler = bool(d.CoolerOn)
            except Exception:
                pass
            return CameraStatus(connected=bool(d.Connected), ccd_temp_c=temp,
                                cooler_on=cooler, state=self._state,
                                detail=self._progid, device_name=self._name)
        try:
            return self._call(_do)
        except Exception as exc:
            return CameraStatus(connected=False, detail=f"ASCOM 오류: {exc}",
                                device_name=self._name)

    def expose(self, seconds: float, light: bool = True) -> np.ndarray:
        def _do():
            d = self._dev
            self._state = "exposing"
            try:
                d.StartExposure(seconds, light)
                while not d.ImageReady:
                    time.sleep(0.25)
                # SafeArray는 (x, y) 순서 → 전치해서 (row, col)로
                arr = np.array(d.ImageArray)
                if arr.ndim == 2:
                    arr = arr.T
                return np.clip(arr, 0, self._sat).astype(np.uint16)
            finally:
                self._state = "idle"
        return self._call(_do)

    def set_cooler(self, on: bool, setpoint_c: float | None = None) -> None:
        def _do():
            if setpoint_c is not None:
                try:
                    self._dev.SetCCDTemperature = float(setpoint_c)
                except Exception:
                    pass  # 일부 드라이버는 설정점 미지원
            self._dev.CoolerOn = bool(on)
        self._call(_do)

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._call(lambda: setattr(self._dev, "Connected", False))
        finally:
            self._ex.shutdown(wait=False)


class AscomFilterWheel(FilterWheelDriver):
    def __init__(self, progid: str, fallback_names: list[str] | None = None):
        self._progid = progid
        self._fallback = fallback_names or []
        self._ex = _com_executor()
        self._dev = None
        self._name = ""

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        if not self._progid:
            raise RuntimeError(_HINT)
        def _do():
            import win32com.client
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
            try:
                self._name = str(self._dev.Name)
            except Exception:
                self._name = self._progid
        self._call(_do)

    def status(self) -> FilterStatus:
        if self._dev is None:
            return FilterStatus(connected=False, names=list(self._fallback),
                                device_name=self._name)
        def _do():
            d = self._dev
            connected = bool(d.Connected)   # 연결 여부를 독립적으로 — 이동/호밍 중
            try:                            # Position 읽기가 실패해도 connected를 뒤집지 않게.
                names = list(d.Names)        # (안 그러면 워치독이 '끊김'으로 보고 재연결→재호밍 무한반복)
            except Exception:
                names = list(self._fallback)
            try:
                pos = int(d.Position)        # ASCOM 규약: 이동/호밍 중 -1
            except Exception:
                pos = -1
            moving = pos < 0
            name = "" if moving or not (0 <= pos < len(names)) else names[pos]
            return FilterStatus(connected=connected,
                                position=(None if moving else pos),
                                name=name, names=names, device_name=self._name)
        try:
            return self._call(_do)
        except Exception:
            return FilterStatus(connected=False, names=list(self._fallback),
                                device_name=self._name)

    def set_position(self, index: int) -> None:
        def _do():
            self._dev.Position = index
            # ASCOM 규약: 이동 중 Position == -1
            while int(self._dev.Position) == -1:
                time.sleep(0.2)
        self._call(_do)

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._call(lambda: setattr(self._dev, "Connected", False))
        finally:
            self._ex.shutdown(wait=False)


class AscomFocuser(FocuserDriver):
    def __init__(self, progid: str):
        self._progid = progid
        self._ex = _com_executor()
        self._dev = None
        self._name = ""

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        if not self._progid:
            raise RuntimeError(_HINT)
        def _do():
            import win32com.client
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
            try:
                self._name = str(self._dev.Name)
            except Exception:
                self._name = self._progid
        self._call(_do)

    def status(self) -> FocuserStatus:
        if self._dev is None:
            return FocuserStatus(connected=False, detail="미연결",
                                 device_name=self._name)
        def _do():
            d = self._dev
            temp = None
            try:
                temp = float(d.Temperature)
            except Exception:
                pass
            maxpos = 60000
            try:
                maxpos = int(d.MaxStep)
            except Exception:
                pass
            return FocuserStatus(connected=bool(d.Connected),
                                 position=int(d.Position),
                                 moving=bool(d.IsMoving), temperature=temp,
                                 max_position=maxpos, detail=self._progid,
                                 device_name=self._name)
        try:
            return self._call(_do)
        except Exception as exc:
            return FocuserStatus(connected=False, detail=f"ASCOM 오류: {exc}",
                                 device_name=self._name)

    def move_to(self, position: int) -> None:
        self._call(lambda: self._dev.Move(int(position)))

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._call(lambda: setattr(self._dev, "Connected", False))
        finally:
            self._ex.shutdown(wait=False)


class AscomWeather(WeatherDriver):
    """ASCOM ObservingConditions — 사실상 표준 기상 인터페이스.
    Davis/AAG/Boltwood 등 다수가 이 드라이버를 제공하므로 ProgID만 바꾸면
    코드 추가 없이 흡수된다. 단위는 ASCOM 규약(°C, %, m/s, deg, CloudCover %).
    미지원 속성은 예외를 던지므로 None으로 정직 보고한다."""

    def __init__(self, progid: str):
        self._progid = progid
        self._ex = _com_executor()
        self._dev = None
        self._name = ""

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        if not self._progid:
            raise RuntimeError(_HINT)
        def _do():
            import win32com.client
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
            try:
                self._name = str(self._dev.Name)
            except Exception:
                self._name = self._progid
        self._call(_do)

    def read(self) -> WeatherStatus:
        if self._dev is None:
            return WeatherStatus(connected=False,
                                 detail=_HINT if not self._progid else "미연결",
                                 device_name=self._name)
        def _do():
            d = self._dev
            try:
                d.Refresh()  # 센서 값 갱신 (미지원이면 무시)
            except Exception:
                pass
            def g(prop):
                try:
                    return float(getattr(d, prop))
                except Exception:
                    return None  # PropertyNotImplemented 등 → 없는 값
            cloud = g("CloudCover")          # 0~100 %
            rain_rate = g("RainRate")        # mm/hr
            return WeatherStatus(
                connected=bool(d.Connected),
                temp_c=g("Temperature"),
                humidity=g("Humidity"),
                dew_point_c=g("DewPoint"),
                wind_ms=g("WindSpeed"),
                wind_dir_deg=g("WindDirection"),
                cloud_score=None if cloud is None else max(0.0, min(1.0, cloud / 100.0)),
                rain=bool(rain_rate and rain_rate > 0),
                detail=self._progid, device_name=self._name)
        try:
            return self._call(_do)
        except Exception as exc:
            return WeatherStatus(connected=False, detail=f"ASCOM 오류: {exc}",
                                 device_name=self._name)

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._call(lambda: setattr(self._dev, "Connected", False))
        finally:
            self._ex.shutdown(wait=False)
