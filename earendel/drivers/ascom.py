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

from .base import (
    CameraDriver, CameraStatus, FilterStatus, FilterWheelDriver,
    FocuserDriver, FocuserStatus,
)

_HINT = "ASCOM ProgID 미설정 — earendel/scripts/choose_ascom.py 실행 후 config.toml에 입력"


def _com_executor() -> ThreadPoolExecutor:
    def _init():
        import pythoncom
        pythoncom.CoInitialize()
    return ThreadPoolExecutor(max_workers=1, initializer=_init,
                              thread_name_prefix="ascom")


class AscomCamera(CameraDriver):
    def __init__(self, progid: str, saturation: int = 65535):
        if not progid:
            raise RuntimeError(_HINT)
        self._progid = progid
        self._sat = saturation
        self._ex = _com_executor()
        self._dev = None
        self._state = "idle"

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        def _do():
            import win32com.client
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
        self._call(_do)

    def status(self) -> CameraStatus:
        if self._dev is None:
            return CameraStatus(connected=False, detail=_HINT if not self._progid else "미연결")
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
                                detail=self._progid)
        try:
            return self._call(_do)
        except Exception as exc:
            return CameraStatus(connected=False, detail=f"ASCOM 오류: {exc}")

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
        if not progid:
            raise RuntimeError(_HINT)
        self._progid = progid
        self._fallback = fallback_names or []
        self._ex = _com_executor()
        self._dev = None

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        def _do():
            import win32com.client
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
        self._call(_do)

    def status(self) -> FilterStatus:
        if self._dev is None:
            return FilterStatus(connected=False, names=list(self._fallback))
        def _do():
            d = self._dev
            try:
                names = list(d.Names)
            except Exception:
                names = list(self._fallback)
            pos = int(d.Position)
            name = names[pos] if 0 <= pos < len(names) else ""
            return FilterStatus(connected=bool(d.Connected), position=pos,
                                name=name, names=names)
        try:
            return self._call(_do)
        except Exception:
            return FilterStatus(connected=False, names=list(self._fallback))

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
        if not progid:
            raise RuntimeError(_HINT)
        self._progid = progid
        self._ex = _com_executor()
        self._dev = None

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        def _do():
            import win32com.client
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
        self._call(_do)

    def status(self) -> FocuserStatus:
        if self._dev is None:
            return FocuserStatus(connected=False, detail="미연결")
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
                                 max_position=maxpos, detail=self._progid)
        try:
            return self._call(_do)
        except Exception as exc:
            return FocuserStatus(connected=False, detail=f"ASCOM 오류: {exc}")

    def move_to(self, position: int) -> None:
        self._call(lambda: self._dev.Move(int(position)))

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._call(lambda: setattr(self._dev, "Connected", False))
        finally:
            self._ex.shutdown(wait=False)
