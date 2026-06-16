"""ZWO ASI 네이티브 SDK 드라이버.

SharpCap/FireCapture와 동일한 ASICamera2.dll을 직접 호출한다.
ASCOM/DCOM 불필요 — USB만 연결돼 있으면 SharpCap이 인식한 카메라를 그대로 쓴다.

설치: pip install zwoasi
lib_path는 자동 탐색 (ZWO SDK / SharpCap / NINA / 레지스트리 순).
수동 지정이 필요하면 config.toml [drivers.zwo] lib_path = "경로" 로 고정.
"""

from __future__ import annotations

import glob
import os
import time

import numpy as np

from .base import CameraDriver, CameraStatus


def _find_asi_dll() -> str:
    """ASICamera2.dll 자동 탐색.

    탐색 순서:
      1. ZWO_ASI_LIB 환경변수
      2. Windows 레지스트리 (ZWO SDK 인스톨러가 기록)
      3. 공통 설치 경로 (ZWO SDK / SharpCap / NINA)
      4. ctypes PATH 탐색 (시스템 PATH에 DLL이 있으면)
    """
    candidates: list[str] = []

    # 1. 환경변수
    env = os.environ.get("ZWO_ASI_LIB", "")
    if env:
        candidates.append(env)

    # 2. 레지스트리
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for subkey in (r"SOFTWARE\ZWO\ASICamera2SDK",
                           r"SOFTWARE\WOW6432Node\ZWO\ASICamera2SDK"):
                try:
                    key = winreg.OpenKey(hive, subkey)
                    install_dir = winreg.QueryValueEx(key, "InstallDir")[0]
                    candidates += [
                        os.path.join(install_dir, "x64", "ASICamera2.dll"),
                        os.path.join(install_dir, "ASICamera2.dll"),
                    ]
                except OSError:
                    pass
    except ImportError:
        pass

    # 3. 공통 설치 경로
    pf_list = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramW6432", r"C:\Program Files"),
    ]
    for pf in dict.fromkeys(pf_list):  # 중복 제거
        candidates += [
            os.path.join(pf, "ZWO", "ASI Camera SDK", "x64", "ASICamera2.dll"),
            os.path.join(pf, "ZWO", "ASI Camera SDK", "ASICamera2.dll"),
        ]
        # SharpCap (버전 폴더 무관하게 glob)
        candidates += glob.glob(os.path.join(pf, "SharpCap*", "ASICamera2.dll"))
        candidates += glob.glob(os.path.join(pf, "SharpCap*", "x64", "ASICamera2.dll"))
        # NINA
        candidates += glob.glob(os.path.join(pf, "N.I.N.A*", "ASICamera2.dll"))
        candidates += glob.glob(os.path.join(pf, "NINA*", "ASICamera2.dll"))

    for path in dict.fromkeys(candidates):
        if os.path.isfile(path):
            return path

    # 4. ctypes PATH 탐색 (DLL이 시스템 PATH에 있는 경우)
    import ctypes.util
    found = ctypes.util.find_library("ASICamera2")
    if found:
        return found

    raise FileNotFoundError(
        "ASICamera2.dll을 찾을 수 없습니다.\n"
        "ZWO SDK, SharpCap, NINA 중 하나가 설치돼 있어야 합니다.\n"
        "수동 지정: config.toml [drivers.zwo] lib_path = \"경로/ASICamera2.dll\"")


class ZwoCamera(CameraDriver):
    """ZWO ASI 카메라 — zwoasi 래퍼로 네이티브 SDK 직접 연결."""

    def __init__(self, lib_path: str, camera_index: int = 0,
                 gain: int = 0, saturation: int = 65535):
        self._lib_path = lib_path   # 비어 있으면 자동 탐색
        self._idx = camera_index
        self._gain = gain
        self._sat = saturation
        self._cam = None
        self._name = ""
        self._width = 0
        self._height = 0
        self._state = "idle"

    def connect(self) -> None:
        import zwoasi as asi
        lib = self._lib_path or _find_asi_dll()
        asi.init(lib)
        num = asi.get_num_cameras()
        if num == 0:
            raise RuntimeError("ZWO 카메라를 찾을 수 없습니다 — USB 연결 확인")
        if self._idx >= num:
            raise RuntimeError(
                f"카메라 인덱스 {self._idx} 없음 (연결된 카메라 {num}대)")
        self._cam = asi.Camera(self._idx)
        info = self._cam.get_camera_property()
        self._name = str(info.get("Name", f"ASI Camera {self._idx}"))
        self._width = int(info.get("MaxWidth", 4944))
        self._height = int(info.get("MaxHeight", 3284))
        self._cam.set_image_type(asi.ASI_IMG_RAW16)
        self._cam.set_roi_format(self._width, self._height, 1, asi.ASI_IMG_RAW16)
        self._cam.set_control_value(asi.ASI_GAIN, int(self._gain))
        # USB 대역폭 70% — 전송 안정화 (기본 40%는 USB2 환경용)
        try:
            self._cam.set_control_value(asi.ASI_BANDWIDTHOVERLOAD, 70)
        except Exception:
            pass

    def status(self) -> CameraStatus:
        if self._cam is None:
            return CameraStatus(connected=False, detail="미연결",
                                device_name=self._name)
        try:
            import zwoasi as asi
            temp_raw = self._cam.get_control_value(asi.ASI_TEMPERATURE)[0]
            temp = temp_raw / 10.0   # ZWO SDK는 0.1°C 단위로 반환
            try:
                cooler_on = bool(self._cam.get_control_value(asi.ASI_COOLER_ON)[0])
            except Exception:
                cooler_on = False
            return CameraStatus(
                connected=True, ccd_temp_c=temp, cooler_on=cooler_on,
                state=self._state, detail=self._name, device_name=self._name)
        except Exception as exc:
            return CameraStatus(connected=False, detail=f"ZWO 오류: {exc}",
                                device_name=self._name)

    def expose(self, seconds: float, light: bool = True) -> np.ndarray:
        import zwoasi as asi
        self._state = "exposing"
        try:
            self._cam.set_control_value(asi.ASI_EXPOSURE, int(seconds * 1_000_000))
            self._cam.start_exposure(is_dark=not light)
            while True:
                st = self._cam.get_exposure_status()
                if st == asi.ASI_EXP_SUCCESS:
                    break
                if st == asi.ASI_EXP_FAILED:
                    raise RuntimeError("ZWO 노출 실패")
                time.sleep(0.05)
            data = self._cam.get_data_after_exposure()
            arr = np.frombuffer(data, dtype=np.uint16).reshape(
                self._height, self._width)
            return np.clip(arr, 0, self._sat).astype(np.uint16)
        finally:
            self._state = "idle"

    def set_cooler(self, on: bool, setpoint_c: float | None = None) -> None:
        import zwoasi as asi
        if setpoint_c is not None:
            try:
                self._cam.set_control_value(asi.ASI_TARGET_TEMP, int(setpoint_c))
            except Exception:
                pass
        self._cam.set_control_value(asi.ASI_COOLER_ON, 1 if on else 0)

    def close(self) -> None:
        if self._cam is not None:
            try:
                self._cam.close()
            except Exception:
                pass
            self._cam = None
