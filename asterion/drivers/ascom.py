"""ASCOM COM 드라이버 (카메라 / 필터휠 / 포커서) — Moravian C3-61000 등.

COM 객체는 생성된 STA 스레드에서만 안전하므로, 장비당 단일 워커
스레드(executor)에서 모든 호출을 직렬 실행한다. ProgID는
scripts/choose_ascom.py 로 선택해 config.toml에 넣는다.
real 모드 + pywin32 설치 환경에서만 import된다.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

import math

from .base import (
    JOG_DEADMAN_S,
    CameraDriver, CameraStatus, DomeDriver, DomeStatus,
    FilterStatus, FilterWheelDriver,
    FocuserDriver, FocuserStatus, MountDriver, MountStatus,
    WeatherDriver, WeatherStatus,
)

_HINT = "ASCOM ProgID 미설정 — asterion/scripts/choose_ascom.py 실행 후 config.toml에 입력"

# ASCOM 드라이버는 오류를 예외가 아니라 자기 '모달 창'에 띄우는 경우가 많다. 그 창이
# 떠 있는 동안 COM 호출이 묶여 결국 '타임아웃'으로 나타나고, 창 안의 텍스트는 다른
# 프로세스 소유라 우리가 읽어올 수 없다(예: ZWO EFW "carousel slipping/blocked, O-ring").
# 그래서 적어도 운영자가 '어디를 봐야 하는지'는 로그가 가리키게 한다.
_EXT_WINDOW_HINT = ("드라이버가 별도 창에 상세 오류를 띄웠을 수 있습니다 — "
                    "서버 화면을 확인하고 ZWO ReCalibrate를 실행하세요")


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
        self._homing = False
        self._motion_sample = None
        self._motion_unchanged_since = None
        # 연속 조그(MoveAxis) 데드맨 — keepalive가 끊기면 status() 폴에서 축을 0으로.
        # _jog_deadline/_jog_axis는 asyncio 스레드(move_axis/keepalive/stop)와 COM 워커
        # 스레드(status._do의 데드맨)가 함께 만지므로 전용 락으로 보호한다(jog_keepalive는
        # COM을 안 거치므로 _call의 직렬화만으론 부족 — 락이 유일한 동기화 지점).
        self._jog_lock = threading.Lock()
        self._jog_deadline = None     # monotonic | None
        self._jog_axis = None         # 0|1|None (마지막 조그 축)
        self._axis_rate_max = 0.0     # AxisRates 최대 (capabilities에서 채움, clamp용)
        # 팬텀(드라이버는 붙었으나 실물 가대 미응답) 동안 True → 워치독이 무의미한
        # 재연결 폭주를 안 하게 한다(재연결해도 또 팬텀). status()가 매번 갱신.
        self.reconnect_blocked = False

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        if not self._progid:
            raise RuntimeError(_HINT)
        def _do():
            import win32com.client
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
            self._homing = False
            self._motion_sample = None
            self._motion_unchanged_since = None
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
            # 조그 데드맨 — keepalive가 끊기면(탭 종료·네트워크) 양축을 0으로 멈춘다.
            # 만료 판정은 락 안에서 읽고(짧게), 실제 정지(COM)는 락 밖에서, 해제는 다시
            # 락 안에서. 정지가 실패하면 deadline을 유지해 다음 폴에서 재시도(가대가
            # 멈출 때까지) — 실패를 삼키고 끝내면 폭주가 남는다.
            with self._jog_lock:
                expired = (self._jog_deadline is not None
                           and time.monotonic() > self._jog_deadline)
            if expired:
                stopped = True
                for _ax in (0, 1):
                    try:
                        d.MoveAxis(_ax, 0.0)
                    except Exception:
                        stopped = False
                if stopped:
                    with self._jog_lock:
                        self._jog_deadline = None
                        self._jog_axis = None
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
            connected = bool(d.Connected)
            ra = g("RightAscension")
            dec = g("Declination")
            alt = g("Altitude")
            az = g("Azimuth")
            slewing = b("Slewing")
            at_home = b("AtHome")
            if self._homing and (at_home or not slewing):
                self._homing = False

            sample = (ra, dec, alt, az)
            stale = False
            now = time.monotonic()
            if connected and slewing and all(v is not None for v in sample):
                if sample == self._motion_sample:
                    if self._motion_unchanged_since is None:
                        self._motion_unchanged_since = now
                    stale = now - self._motion_unchanged_since >= 5.0
                else:
                    self._motion_sample = sample
                    self._motion_unchanged_since = now
            else:
                self._motion_sample = sample
                self._motion_unchanged_since = None

            # 팬텀 = 드라이버는 Connected=True인데 '슬루 중'이라며 좌표가 5초+ 고정
            # → 실물 가대가 응답하지 않는 것(전원/링크 없음). 이때는 드라이버가 붙었어도
            # '연결 안 됨'으로 보고한다: ASCOM의 Connected는 '드라이버 부착'이지
            # '하드웨어 연결'이 아니다. 운영자 원칙대로 "실물 미연결 = disconnected".
            # connected=False면 안전(FAULT)·UI(빨강·미연결)·운영이 모두 자동으로 끊김 취급.
            phantom = connected and stale
            self.reconnect_blocked = phantom   # 워치독 재연결 폭주 차단(재연결해도 또 팬텀)
            detail = ("하드웨어 미응답 — 드라이버는 연결됐으나 가대가 응답하지 않음 "
                      "(가대 전원·연결 확인)") if phantom else ""
            return MountStatus(
                connected=connected and not phantom,
                ra_hours=ra, dec_degs=dec,
                alt_degs=alt, az_degs=az,
                slewing=slewing, tracking=b("Tracking"),
                at_park=b("AtPark"), at_home=at_home,
                can_park=b("CanPark"), can_home=b("CanFindHome"),
                homing=self._homing, stale=stale,
                detail=detail, device_name=self._name)
        try:
            return self._call(_do)
        except Exception as exc:
            return MountStatus(connected=False, detail=f"ASCOM 오류: {exc}",
                               device_name=self._name)

    def goto_altaz(self, alt_deg: float, az_deg: float) -> None:
        def _do():
            d = self._dev
            self._require_slew_ready(d)
            try:
                d.SlewToAltAzAsync(az_deg, alt_deg)   # ASCOM 순서: (az, alt)
            except Exception:
                d.SlewToAltAz(az_deg, alt_deg)
        self._call(_do)

    def goto_radec(self, ra_hours: float, dec_degs: float) -> None:
        def _do():
            d = self._dev
            self._require_slew_ready(d)
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
            self._require_slew_ready(d)
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
        with self._jog_lock:
            self._jog_deadline = None
            self._jog_axis = None
        self._call(lambda: self._dev.AbortSlew())

    def move_axis(self, axis: int, rate_deg_s: float) -> None:
        """연속 조그 — ASCOM MoveAxis(axis, deg/s). rate 0 = 그 축 정지.
        nonzero는 _require_slew_ready로 (미연결·파킹·고착 슬루) 가드한다."""
        ax = 0 if int(axis) == 0 else 1
        rate = float(rate_deg_s)
        def _do():
            d = self._dev
            if rate == 0.0:
                d.MoveAxis(ax, 0.0)
                return
            # 조그는 _require_slew_ready를 쓰지 않는다 — 그건 Slewing 중이면 거부하는데,
            # MoveAxis는 (트래킹/직전 조그 위에) 합성되는 게 정상이라 방향 전환이 막힌다.
            # 연결+미파킹만 확인한다.
            if d is None or not bool(d.Connected):
                raise RuntimeError("가대가 연결되지 않았습니다")
            try:
                if bool(d.AtPark):
                    raise RuntimeError("가대가 파킹 상태입니다. 먼저 Unpark 하세요")
            except RuntimeError:
                raise
            except Exception:
                pass
            r = rate
            if self._axis_rate_max > 0:               # 드라이버가 보고한 밴드로 클램프
                mag = min(abs(r), self._axis_rate_max)
                r = mag if r > 0 else -mag
            d.MoveAxis(ax, r)
        self._call(_do)
        with self._jog_lock:
            if rate == 0.0:
                self._jog_deadline = None
                self._jog_axis = None
            else:
                self._jog_axis = ax
                self._jog_deadline = time.monotonic() + JOG_DEADMAN_S

    def jog_keepalive(self) -> None:
        # 데드맨 재무장 — COM은 건드리지 않는다(가대는 이미 움직이는 중). 조그 중일 때만.
        with self._jog_lock:
            if self._jog_deadline is not None:
                self._jog_deadline = time.monotonic() + JOG_DEADMAN_S

    def capabilities(self) -> dict:
        """연속 조그 능력 — CanMoveAxis(축별) + AxisRates(가용 속도 밴드).
        connect 시 1회. jog 느림/보통/빠름은 밴드 최대속도의 비율로 제시한다."""
        if self._dev is None:
            return {}

        def _do():
            d = self._dev
            caps: dict = {}
            cmp_ = cms = False
            try:
                cmp_ = bool(d.CanMoveAxis(0))
            except Exception:
                pass
            try:
                cms = bool(d.CanMoveAxis(1))
            except Exception:
                pass
            caps["can_move_axis_primary"] = cmp_
            caps["can_move_axis_secondary"] = cms
            rate_max = 0.0
            for ax in (0, 1):                          # AxisRates는 1-기반 컬렉션
                try:
                    rates = d.AxisRates(ax)
                    try:
                        n = int(rates.Count)
                        for i in range(1, n + 1):
                            rate_max = max(rate_max, float(rates.Item(i).Maximum))
                    except Exception:
                        for r in rates:                # 열거 가능 드라이버 폴백
                            rate_max = max(rate_max, float(r.Maximum))
                except Exception:
                    pass
            self._axis_rate_max = rate_max
            if rate_max > 0:
                caps["axis_rate_max"] = round(rate_max, 4)
                caps["jog_rates"] = {
                    "slow": round(rate_max * 0.05, 4),
                    "normal": round(rate_max * 0.25, 4),
                    "fast": round(rate_max * 0.70, 4),
                }
            return caps

        try:
            return self._call(_do)
        except Exception:
            return {}

    def park(self) -> None:
        self._call(lambda: self._dev.Park())

    def unpark(self) -> None:
        self._call(lambda: self._dev.Unpark())

    def find_home(self) -> None:
        def _do():
            self._require_slew_ready(self._dev)
            self._homing = True
            try:
                self._dev.FindHome()
            except Exception:
                self._homing = False
                raise
        self._call(_do)

    def set_park(self) -> None:
        self._call(lambda: self._dev.SetPark())   # 현재 위치를 파킹 위치로 저장

    def _require_slew_ready(self, d) -> None:
        if d is None or not bool(d.Connected):
            raise RuntimeError("가대가 연결되지 않았습니다")
        try:
            if bool(d.AtPark):
                raise RuntimeError("가대가 파킹 상태입니다. 먼저 Unpark 하세요")
        except RuntimeError:
            raise
        except Exception:
            pass
        try:
            if bool(d.Slewing):
                # 좌표가 5초+ 고착(phantom slew) → AbortSlew 후 통과
                stale = (
                    self._motion_unchanged_since is not None
                    and time.monotonic() - self._motion_unchanged_since >= 5.0
                )
                if stale:
                    try:
                        d.AbortSlew()
                    except Exception:
                        pass
                else:
                    raise RuntimeError("가대가 이미 슬루/홈 탐색 중입니다. 먼저 정지하세요")
        except RuntimeError:
            raise
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._call(lambda: setattr(self._dev, "Connected", False))
        finally:
            self._ex.shutdown(wait=False)


class AscomCamera(CameraDriver):
    # ProgID가 비어 있어도 생성은 허용 — REAL 전환을 막지 않는다.
    # 실제 연결/노출 시점에 안내하고, status()는 '미연결'로 정직하게 보고.
    def __init__(self, progid: str, saturation: int = 65535,
                 gain: int | None = None):
        self._progid = progid
        self._sat = saturation
        self._gain = gain          # setup.camera.gain (None = 드라이버 기본 유지)
        self._ex = _com_executor()
        self._dev = None
        self._state = "idle"
        self._name = ""
        # 노출 중(단일 COM 워커가 StartExposure로 점유)에는 status가 COM을 건드리면 노출 시간만큼
        # 블록된다 → 직전 폴의 온도/쿨러 값을 캐시해 노출 중 status가 즉시 반환하게 한다.
        self._last_temp: float | None = None
        self._last_cooler = False
        self._last_power: float | None = None

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        if not self._progid:
            raise RuntimeError(_HINT)
        def _do():
            import win32com.client
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
            if self._gain is not None:          # Setup의 게인 적용 (지원 카메라만)
                try:
                    self._dev.Gain = int(self._gain)
                except Exception:
                    pass
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
        # 노출 중에는 단일 COM 워커가 StartExposure로 점유돼 status 제출이 노출 시간(60~300s)만큼
        # 블록된다 → 샘플러 STATUS_TIMEOUT_S(3s)→_stuck→offline→missing_required FAULT 플랩(매
        # 천문 노출마다). 노출 중엔 COM을 건드리지 않고 직전 폴 캐시 + state='exposing'으로 즉시
        # 반환한다 — 노출은 '정상 점유'이지 미연결이 아니다(거짓 FAULT 차단).
        if self._state == "exposing":
            return CameraStatus(connected=True, ccd_temp_c=self._last_temp,
                                cooler_on=self._last_cooler, cooler_power=self._last_power,
                                state="exposing", detail="노출 중", device_name=self._name)
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
            power = None
            try:                                  # Moravian 등은 CoolerPower(%) 보고
                power = float(d.CoolerPower)
            except Exception:
                pass
            # 노출 중 캐시 반환에 쓸 직전 값 갱신(평시 폴마다).
            self._last_temp, self._last_cooler, self._last_power = temp, cooler, power
            return CameraStatus(connected=bool(d.Connected), ccd_temp_c=temp,
                                cooler_on=cooler, cooler_power=power,
                                state=self._state, detail="", device_name=self._name)
        try:
            return self._call(_do)
        except Exception as exc:
            return CameraStatus(connected=False, detail=f"ASCOM 오류: {exc}",
                                device_name=self._name)

    def expose(self, seconds: float, light: bool = True,
               binning: int = 1) -> np.ndarray:
        def _do():
            d = self._dev
            try:
                b = max(1, int(binning))
                applied_b = 1                     # 실제 적용된 비닝(capture가 기록에 사용)
                try:                              # 비닝 + 전체 프레임
                    mx = int(getattr(d, "MaxBinX", 1) or 1)
                    b = max(1, min(b, mx))
                    d.BinX = b
                    d.BinY = b
                    d.NumX = int(d.CameraXSize) // b
                    d.NumY = int(d.CameraYSize) // b
                    d.StartX = 0
                    d.StartY = 0
                    applied_b = b
                except Exception:                 # 실패 → 1x1 전체프레임 복구(불일치 방지)
                    try:
                        d.BinX = 1
                        d.BinY = 1
                        d.NumX = int(d.CameraXSize)
                        d.NumY = int(d.CameraYSize)
                        d.StartX = 0
                        d.StartY = 0
                    except Exception:
                        pass
                    applied_b = 1
                self.last_binning = applied_b
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
        # COM 제출 *전에* exposing으로 — 워커가 점유되기 전에 set해야 동시 status()가 즉시 캐시
        # 경로를 탄다(제출 후 set하면 그 사이 status가 COM 큐 뒤에 걸려 블록될 수 있음).
        self._state = "exposing"
        try:
            return self._call(_do)
        except Exception:
            self._state = "idle"   # 제출/실행 실패 시 상태 복구(다음 status가 COM 폴로 정상화)
            raise

    def set_cooler(self, on: bool, setpoint_c: float | None = None) -> None:
        def _do():
            if setpoint_c is not None:
                try:
                    self._dev.SetCCDTemperature = float(setpoint_c)
                except Exception:
                    pass  # 일부 드라이버는 설정점 미지원
            self._dev.CoolerOn = bool(on)
        self._call(_do)

    def capabilities(self) -> dict:
        """연결된 카메라의 정적 능력 — 게인/오프셋 범위·읽기모드·냉각가부·노출한계.
        connect 시 1회. Setup 폼이 이걸로 드롭다운/범위를 그린다."""
        if self._dev is None:
            return {}

        def _do():
            d = self._dev
            caps: dict = {}
            try:                                  # 게인: 이산(Gains[]) 또는 연속(Min/Max)
                gains = [str(g) for g in d.Gains]
                if gains:
                    caps["gains"] = gains
            except Exception:
                pass
            if "gains" not in caps:
                try:
                    caps["gain_min"] = int(d.GainMin)
                    caps["gain_max"] = int(d.GainMax)
                except Exception:
                    pass
            try:
                caps["offset_min"] = int(d.OffsetMin)
                caps["offset_max"] = int(d.OffsetMax)
            except Exception:
                pass
            try:
                caps["readout_modes"] = [str(m) for m in d.ReadoutModes]
            except Exception:
                pass
            try:
                caps["can_set_ccd_temperature"] = bool(d.CanSetCCDTemperature)
            except Exception:
                pass
            try:
                caps["max_bin"] = int(d.MaxBinX)
            except Exception:
                pass
            try:
                caps["exposure_min_s"] = float(d.ExposureMin)
                caps["exposure_max_s"] = float(d.ExposureMax)
            except Exception:
                pass
            try:
                caps["pixel_size_um"] = float(d.PixelSizeX)
            except Exception:
                pass
            return caps

        try:
            return self._call(_do)
        except Exception:
            return {}

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._call(lambda: setattr(self._dev, "Connected", False))
        finally:
            self._ex.shutdown(wait=False)


class AscomFilterWheel(FilterWheelDriver):
    def __init__(self, progid: str, fallback_names: list[str] | None = None,
                 init_timeout_s: float = 20.0, move_timeout_s: float = 20.0):
        self._progid = progid
        self._fallback = fallback_names or []
        self._init_timeout_s = max(1.0, float(init_timeout_s))
        self._move_timeout_s = max(1.0, float(move_timeout_s))
        self._ex = _com_executor()
        self._dev = None
        self._name = ""
        self._fault = ""
        self.reconnect_blocked = False

    def _call(self, fn):
        return self._ex.submit(fn).result()

    def connect(self) -> None:
        if not self._progid:
            raise RuntimeError(_HINT)
        def _do():
            import win32com.client
            self._fault = ""
            self.reconnect_blocked = False
            self._dev = win32com.client.Dispatch(self._progid)
            self._dev.Connected = True
            try:
                self._name = str(self._dev.Name)
            except Exception:
                self._name = self._progid
            deadline = time.monotonic() + self._init_timeout_s
            while True:
                try:
                    position = int(self._dev.Position)
                except Exception:
                    position = -1
                if position >= 0:
                    break
                if time.monotonic() >= deadline:
                    self._fault = (
                        f"EFW 초기화가 {self._init_timeout_s:.0f}초 안에 "
                        "끝나지 않았습니다. 휠 고정 나사/회전판 간섭과 "
                        f"USB 전원을 확인하세요. {_EXT_WINDOW_HINT}")
                    self.reconnect_blocked = True
                    try:
                        self._dev.Connected = False
                    except Exception:
                        pass
                    raise RuntimeError(self._fault)
                time.sleep(0.2)
        self._call(_do)

    def status(self) -> FilterStatus:
        if self._dev is None:
            return FilterStatus(connected=False, names=list(self._fallback),
                                detail=self._fault, device_name=self._name)
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
                                name=name, names=names, moving=moving,
                                detail=(self._fault or
                                        ("초기화/이동 중" if moving else "")),
                                device_name=self._name)
        try:
            return self._call(_do)
        except Exception as exc:
            return FilterStatus(connected=False, names=list(self._fallback),
                                detail=self._fault or f"ASCOM 오류: {exc}",
                                device_name=self._name)

    def capabilities(self) -> dict:
        """연결된 휠의 정적 정보 — 필터명 + Focus Offset(+ 슬롯 수). connect 시 1회만.
        MaximDL의 Filter Name/Focus Offset 표가 바로 여기서 온다 (d.FocusOffsets)."""
        if self._dev is None:
            return {}

        def _do():
            d = self._dev
            try:
                names = [str(n) for n in d.Names]
            except Exception:
                names = list(self._fallback)
            try:
                offsets = [int(x) for x in d.FocusOffsets]
            except Exception:
                offsets = [0] * len(names)
            if len(offsets) < len(names):       # 길이 안 맞으면 0으로 패딩/절단
                offsets += [0] * (len(names) - len(offsets))
            return {"names": names, "focus_offsets": offsets[:len(names)],
                    "slot_count": len(names)}

        try:
            return self._call(_do)
        except Exception:
            return {}

    def set_position(self, index: int) -> None:
        def _do():
            if self._dev is None or not bool(self._dev.Connected):
                raise RuntimeError("필터휠이 연결되지 않았습니다")
            if int(self._dev.Position) == -1:
                raise RuntimeError("필터휠이 이미 초기화/이동 중입니다")
            self._dev.Position = index
            # ASCOM 규약: 이동 중 Position == -1
            deadline = time.monotonic() + self._move_timeout_s
            while int(self._dev.Position) == -1:
                if time.monotonic() >= deadline:
                    self._fault = (
                        f"EFW 이동이 {self._move_timeout_s:.0f}초 안에 "
                        f"끝나지 않았습니다. {_EXT_WINDOW_HINT}")
                    self.reconnect_blocked = True
                    try:
                        self._dev.Connected = False
                    except Exception:
                        pass
                    raise TimeoutError(self._fault)
                time.sleep(0.2)
            self._fault = ""
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
                                 max_position=maxpos, detail="",
                                 device_name=self._name)
        try:
            return self._call(_do)
        except Exception as exc:
            return FocuserStatus(connected=False, detail=f"ASCOM 오류: {exc}",
                                 device_name=self._name)

    def capabilities(self) -> dict:
        if self._dev is None:
            return {}

        def _do():
            d = self._dev
            caps: dict = {}
            try:
                caps["max_step"] = int(d.MaxStep)
            except Exception:
                pass
            try:
                caps["step_size_um"] = float(d.StepSize)
            except Exception:
                pass
            try:
                caps["temp_comp_available"] = bool(d.TempCompAvailable)
            except Exception:
                pass
            try:
                caps["absolute"] = bool(d.Absolute)
            except Exception:
                pass
            caps["unit"] = "steps"     # ASCOM Position은 스텝 단위(PWI3 네이티브는 micron)
            caps["can_halt"] = True    # ASCOM IFocuser는 Halt() 보유
            caps["can_home"] = False   # ASCOM 표준엔 Home 없음
            return caps

        try:
            return self._call(_do)
        except Exception:
            return {}

    def move_to(self, position: int) -> None:
        self._call(lambda: self._dev.Move(int(position)))

    def halt(self) -> None:
        self._call(lambda: self._dev.Halt())

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
            # 센서 갱신 경과 — ObservingConditions 표준. 스테이션이 멈춰 Connected=True+고착값을
            # 돌려줄 때 이 값이 커져 status의 fail-closed stale 게이트를 발화시킨다. ""=전체 중 최신.
            try:
                age = float(d.TimeSinceLastUpdate(""))
                reading_age = age if age >= 0 else None
            except Exception:
                reading_age = None
            return WeatherStatus(
                connected=bool(d.Connected),
                temp_c=g("Temperature"),
                humidity=g("Humidity"),
                dew_point_c=g("DewPoint"),
                wind_ms=g("WindSpeed"),
                wind_dir_deg=g("WindDirection"),
                cloud_score=None if cloud is None else max(0.0, min(1.0, cloud / 100.0)),
                rain=bool(rain_rate and rain_rate > 0),
                reading_age_s=reading_age,
                detail="", device_name=self._name)
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


# ASCOM IDomeV2 ShutterStatus 열거값
_SHUTTER = {0: "open", 1: "closed", 2: "opening", 3: "closing", 4: "error"}


class AscomDome(DomeDriver):
    """ASCOM Dome (IDomeV2) — NexDome·MaxDome II·ScopeDome·Beaver 등 표준 돔.
    엔코더·슬레이빙·셔터 제어는 ASCOM 드라이버가 추상화하므로 ProgID만 바꾸면
    코드 추가 없이 흡수된다. capability는 드라이버의 CanXxx로 정직 보고.
    (실하드웨어 검증 전 — 지상국/자동돔 연결 시 확인 필요.)"""

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

    def status(self) -> DomeStatus:
        if self._dev is None:
            return DomeStatus(connected=False,
                              detail=_HINT if not self._progid else "미연결",
                              device_name=self._name)
        def _do():
            d = self._dev
            def b(prop):
                try:
                    return bool(getattr(d, prop))
                except Exception:
                    return False
            def f(prop):
                try:
                    return float(getattr(d, prop))
                except Exception:
                    return None
            can_shutter = b("CanSetShutter")
            can_az = b("CanSetAzimuth")
            try:
                shutter = _SHUTTER.get(int(d.ShutterStatus), "unknown")
            except Exception:
                shutter = "unknown"
            return DomeStatus(
                connected=bool(d.Connected), shutter=shutter,
                azimuth=f("Azimuth") if can_az else None,
                azimuth_estimated=False,
                moving=b("Slewing"), slaved=b("Slaved"),
                at_park=b("AtPark"), at_home=b("AtHome"),
                has_shutter=can_shutter, can_command_shutter=can_shutter,
                can_rotate=can_az, can_slew_azimuth=can_az,
                can_slave=b("CanSlave"), detail="", device_name=self._name)
        try:
            return self._call(_do)
        except Exception as exc:
            return DomeStatus(connected=False, detail=f"ASCOM 오류: {exc}",
                              device_name=self._name)

    def open_shutter(self) -> None:
        self._call(lambda: self._dev.OpenShutter())

    def close_shutter(self) -> None:
        self._call(lambda: self._dev.CloseShutter())

    def slew_to_azimuth(self, az_deg: float) -> None:
        self._call(lambda: self._dev.SlewToAzimuth(float(az_deg) % 360.0))

    def sync_azimuth(self, az_deg: float) -> None:
        self._call(lambda: self._dev.SyncToAzimuth(float(az_deg) % 360.0))

    def set_slaved(self, on: bool) -> None:
        self._call(lambda: setattr(self._dev, "Slaved", bool(on)))

    def park(self) -> None:
        self._call(lambda: self._dev.Park())

    def find_home(self) -> None:
        self._call(lambda: self._dev.FindHome())

    def stop(self) -> None:
        self._call(lambda: self._dev.AbortSlew())

    def close(self) -> None:
        try:
            if self._dev is not None:
                self._call(lambda: setattr(self._dev, "Connected", False))
        finally:
            self._ex.shutdown(wait=False)
