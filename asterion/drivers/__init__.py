"""장비 레지스트리 + 연결 관리자 — 데이터 주도 (ARCHITECTURE §2-2).

핵심: 장비는 *공용 백본에 플러그*될 뿐이다. 새 ASCOM 장비를 붙일 때
REGISTRY에 DeviceSpec 한 줄 + 팩토리 한두 개만 추가하면 연결 UI·상태·
텔레메트리·액션·안전 판정이 전부 공짜로 따라온다 (코드 변경 0).

  - REGISTRY          : device key → DeviceSpec(sim 팩토리 + 백엔드별 real 팩토리)
  - DriverContext     : 팩토리가 쓰는 공용 의존성 묶음
  - ConnectionManager : build_all · backend · connect/disconnect/reconnect ·
                        list_ascom · configure — 모든 장비에 균일

drivers.mode = "sim"  → 전부 시뮬 (마스터 스위치, 기본값)
drivers.mode = "real" → 장비별 drivers.{key} 백엔드를 따름 (sim 혼용 가능)
real 전용 모듈(pwi4/ascom)은 그 백엔드가 선택됐을 때만 import한다.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..config import Config
from .base import (
    CameraStatus, DomeStatus, FilterStatus, FocuserStatus, MountStatus,
    WeatherStatus,
)
from .sim import (
    SimCamera, SimDome, SimFilterWheel, SimFocuser, SimMount, SimWeather,
    TwilightSim,
)


# ---------- 빌드 컨텍스트 ----------

@dataclass
class DriverContext:
    """드라이버 팩토리가 필요로 하는 공용 의존성. `drivers`는 살아있는
    레지스트리 dict 참조 — 시뮬 카메라가 *현재* 필터휠을 교차 참조할 때 쓴다
    (재빌드돼도 항상 최신 필터휠을 본다)."""
    cfg: Config
    twilight: TwilightSim
    sun_alt_fn: Callable[[], float]
    lst_fn: Callable[[], float]
    drivers: dict[str, Any]

    @property
    def lat(self) -> float:
        return float(self.cfg.get("site.latitude", 36.6))

    @property
    def filter_names(self) -> list[str]:
        return list(self.cfg.get("filters.names", ["B", "V", "R", "I"]))


# ---------- 장비 팩토리 (백엔드별) ----------

def _sim_mount(ctx: DriverContext):
    return SimMount(ctx.lat, ctx.lst_fn)


def _pwi4_mount(ctx: DriverContext):
    from .pwi4 import Pwi4Mount
    return Pwi4Mount(str(ctx.cfg.get("drivers.pwi4.base_url",
                                     "http://127.0.0.1:8220")))


def _ascom_mount(ctx: DriverContext):
    from .ascom import AscomMount
    return AscomMount(str(ctx.cfg.get("drivers.ascom.mount_progid", "")))


def _sim_camera(ctx: DriverContext):
    return SimCamera(
        width=int(ctx.cfg.get("sim.image_width", 958)),
        height=int(ctx.cfg.get("sim.image_height", 639)),
        twilight=ctx.twilight, sun_alt_fn=ctx.sun_alt_fn,
        filter_name_fn=lambda: ctx.drivers["filterwheel"].status().name,
        exposure_sleep_cap_s=float(ctx.cfg.get("sim.exposure_sleep_cap_s", 2.0)),
        saturation=int(ctx.cfg.get("camera.saturation_adu", 65535)),
    )


def _ascom_camera(ctx: DriverContext):
    from .ascom import AscomCamera
    return AscomCamera(str(ctx.cfg.get("drivers.ascom.camera_progid", "")),
                       saturation=int(ctx.cfg.get("camera.saturation_adu", 65535)))


def _zwo_camera(ctx: DriverContext):
    from .zwo import ZwoCamera
    return ZwoCamera(
        lib_path=str(ctx.cfg.get("drivers.zwo.lib_path", "")),
        camera_index=int(ctx.cfg.get("drivers.zwo.camera_index", 0)),
        gain=int(ctx.cfg.get("drivers.zwo.gain", 0)),
        saturation=int(ctx.cfg.get("camera.saturation_adu", 65535)),
    )


def _sim_filterwheel(ctx: DriverContext):
    return SimFilterWheel(ctx.filter_names)


def _ascom_filterwheel(ctx: DriverContext):
    from .ascom import AscomFilterWheel
    return AscomFilterWheel(
        str(ctx.cfg.get("drivers.ascom.filterwheel_progid", "")),
        fallback_names=ctx.filter_names,
        init_timeout_s=float(ctx.cfg.get(
            "drivers.filterwheel_init_timeout_s", 20.0)),
        move_timeout_s=float(ctx.cfg.get(
            "drivers.filterwheel_move_timeout_s", 20.0)))


def _sim_focuser(ctx: DriverContext):
    return SimFocuser()


def _ascom_focuser(ctx: DriverContext):
    from .ascom import AscomFocuser
    return AscomFocuser(str(ctx.cfg.get("drivers.ascom.focuser_progid", "")))


def _sim_weather(ctx: DriverContext):
    return SimWeather()


def _ascom_weather(ctx: DriverContext):
    from .ascom import AscomWeather
    return AscomWeather(str(ctx.cfg.get("drivers.ascom.weather_progid", "")))


def _davis_weather(ctx: DriverContext):
    from .davis import DavisWeather
    return DavisWeather(str(ctx.cfg.get("drivers.davis.base_url", "http://127.0.0.1")))


def _sim_dome(ctx: DriverContext):
    return SimDome(
        feedback=bool(ctx.cfg.get("sim.dome_feedback", True)),
        shutter_auto=bool(ctx.cfg.get("sim.dome_shutter_auto", True)))


def _ascom_dome(ctx: DriverContext):
    from .ascom import AscomDome
    return AscomDome(str(ctx.cfg.get("drivers.ascom.dome_progid", "")))


def _serial_dome(ctx: DriverContext):
    from .serial_dome import SerialDome
    return SerialDome(
        port=str(ctx.cfg.get("drivers.dome.serial_port", "")),
        baud=int(ctx.cfg.get("drivers.dome.baud", 9600)),
        rot_speed_deg_s=float(ctx.cfg.get("drivers.dome.rot_speed_deg_s", 3.0)))


# ---------- 레지스트리 (= 장비를 흡수하는 단일 데이터) ----------

@dataclass(frozen=True)
class DeviceSpec:
    key: str
    label: str                              # UI 표시명
    status_attr: str                        # status() | read()
    sim_factory: Callable[[DriverContext], Any]
    real_factories: dict[str, Callable[[DriverContext], Any]]
    ascom_type: str | None = None           # ASCOM Chooser/Profile DeviceType
    progid_key: str | None = None           # ASCOM ProgID를 담는 config 경로
    url_key: str | None = None              # PWI4 URL을 담는 config 경로
    snapshot_key: str | None = None         # 스냅샷 공개 키 (기본 = key). 필터휠→"filter"
    offline_factory: Callable[[], Any] | None = None  # 타임아웃/무응답 시 오프라인 Status
    safety_role: str | None = None          # None | "required" | "weather" (확장: "shutter")

    @property
    def real_kinds(self) -> list[str]:
        return list(self.real_factories)

    @property
    def snap_key(self) -> str:
        return self.snapshot_key or self.key


_OFFLINE_DETAIL = "응답 없음 (시간 초과)"

REGISTRY: dict[str, DeviceSpec] = {
    "mount": DeviceSpec(
        "mount", "마운트", "status", _sim_mount,
        {"pwi4": _pwi4_mount, "ascom": _ascom_mount},
        ascom_type="Telescope",
        progid_key="drivers.ascom.mount_progid",
        url_key="drivers.pwi4.base_url",
        offline_factory=lambda: MountStatus(connected=False, detail=_OFFLINE_DETAIL),
        safety_role="required"),
    "camera": DeviceSpec(
        "camera", "카메라", "status", _sim_camera,
        {"ascom": _ascom_camera, "zwo": _zwo_camera},
        ascom_type="Camera", progid_key="drivers.ascom.camera_progid",
        offline_factory=lambda: CameraStatus(connected=False, detail=_OFFLINE_DETAIL),
        safety_role="required"),
    "filterwheel": DeviceSpec(
        "filterwheel", "필터휠", "status", _sim_filterwheel,
        {"ascom": _ascom_filterwheel},
        ascom_type="FilterWheel", progid_key="drivers.ascom.filterwheel_progid",
        snapshot_key="filter",
        offline_factory=lambda: FilterStatus(connected=False)),
    "focuser": DeviceSpec(
        "focuser", "포커서", "status", _sim_focuser, {"ascom": _ascom_focuser},
        ascom_type="Focuser", progid_key="drivers.ascom.focuser_progid",
        offline_factory=lambda: FocuserStatus(connected=False, detail=_OFFLINE_DETAIL)),
    "weather": DeviceSpec(
        "weather", "기상", "read", _sim_weather,
        {"ascom": _ascom_weather, "davis": _davis_weather},
        ascom_type="ObservingConditions",
        progid_key="drivers.ascom.weather_progid",
        url_key="drivers.davis.base_url",
        offline_factory=lambda: WeatherStatus(connected=False, detail=_OFFLINE_DETAIL),
        safety_role="weather"),
    "dome": DeviceSpec(
        "dome", "돔", "status", _sim_dome,
        {"ascom": _ascom_dome, "serial": _serial_dome},
        ascom_type="Dome",
        progid_key="drivers.ascom.dome_progid",
        url_key="drivers.dome.serial_port",   # serial 백엔드 포트(COM3 등) — url 필드 재사용
        offline_factory=lambda: DomeStatus(connected=False, detail=_OFFLINE_DETAIL),
        safety_role="shutter"),
}


# 백엔드 kind가 요구하는 운영자 설정의 *종류*. describe()/UI가 이걸 보고
# 현재 선택된 백엔드에 맞는 입력 필드만 그린다 — ASCOM=ProgID, PWI4/Davis=URL,
# ZWO=자동탐색(설정 없음), sim=설정 없음. 새 백엔드는 여기 한 줄로 흡수된다.
#   progid → ASCOM ProgID 드롭다운    url → URL/IP 입력
#   auto   → 자동 연결 안내(필드 없음)  none → 설정 없음
BACKEND_CONFIG: dict[str, str] = {
    "sim": "none",
    "ascom": "progid",
    "pwi4": "url",
    "davis": "url",
    "zwo": "auto",
    "serial": "url",   # 시리얼 돔 — 포트(COM3 등)를 url 텍스트 필드로 입력
}


# ---------- COM 헬퍼 ----------

def _safe_close(drv: Any) -> None:
    if drv is None:
        return
    try:
        drv.close()
    except Exception:
        pass


def _run_com(fn: Callable[[], Any]) -> Any:
    """COM 호출을 CoInitialize된 일회성 STA 스레드에서 실행 (Profile/Chooser).
    기본 스레드풀 워커는 CoInitialize되지 않아 Dispatch가 실패할 수 있다."""
    box: dict[str, Any] = {}

    def worker() -> None:
        import pythoncom
        pythoncom.CoInitialize()
        try:
            box["v"] = fn()
        except Exception as exc:  # noqa: BLE001 — 호출부로 전달
            box["e"] = exc
        finally:
            pythoncom.CoUninitialize()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join()
    if "e" in box:
        raise box["e"]
    return box.get("v")


# ---------- 연결 관리자 ----------

class ConnectionManager:
    """레지스트리를 데이터로 보고 모든 장비를 균일하게 빌드·연결·해제한다.

    `drivers` dict는 sampler/runner/capture가 공유하는 살아있는 단일 상태다 —
    재빌드 시 같은 dict 객체를 in-place 교체해 즉시 반영된다. 연결 실패는
    절대 전환을 막지 않고 '미연결'로 정직 보고한다 (REAL 자유 전환)."""

    def __init__(self, cfg: Config, twilight: TwilightSim,
                 sun_alt_fn: Callable[[], float], lst_fn: Callable[[], float],
                 events: Any = None):
        self.cfg = cfg
        self.events = events
        self.drivers: dict[str, Any] = {}
        self.ctx = DriverContext(cfg, twilight, sun_alt_fn, lst_fn, self.drivers)
        self._lock = threading.RLock()
        self.build_all()  # 초기 빌드 (연결은 lifespan/connect_all에서)
        # desired[key] = "이 장비는 연결돼 있어야 한다"는 운영자 의도. 워치독이
        # desired=True인데 끊긴 장비만 자동 복구한다 (운영자가 끈 건 안 건드림).
        self.desired: dict[str, bool] = {k: True for k in REGISTRY}
        # connected_at[key] = 마지막으로 연결에 성공한 시각. 워치독이 방금 (재)연결한
        # 장비는 호밍/캘리브레이션을 끝낼 때까지 재연결 후보에서 빼는 데 쓴다 — ZWO EFW
        # 등은 연결 직후 호밍 중 잠깐 응답을 못 줘 '끊김'으로 오인되기 쉽다.
        self.connected_at: dict[str, float] = {}
        self._op_locks: dict[str, asyncio.Lock] = {
            key: asyncio.Lock() for key in REGISTRY
        }

    def _log(self, msg: str, level: str = "info") -> None:
        if self.events is not None:
            self.events.log("system", msg, level)

    # ---- 모드 / 백엔드 선택 ----

    @property
    def master_mode(self) -> str:
        return str(self.cfg.get("drivers.mode", "sim"))

    def selected_backend(self, key: str) -> str:
        """운영자가 REAL용으로 골라둔 백엔드 kind (마스터가 sim이어도 유지).
        설정 UI가 어느 입력 필드(ProgID/URL/자동)를 보일지 정하는 근거다 —
        sim 모드에서 미리 백엔드/설정을 잡아두고 REAL로 전환하는 흐름을 지원한다."""
        spec = REGISTRY[key]
        kind = str(self.cfg.get(f"drivers.{key}", "sim"))
        return kind if kind in spec.real_factories else "sim"

    def backend(self, key: str) -> str:
        """이 장비가 *지금 실제로* 쓰는 백엔드 kind. sim 모드면 무조건 sim,
        real 모드면 운영자가 고른 백엔드(real_factories에 있을 때만)."""
        if self.master_mode == "sim":
            return "sim"
        return self.selected_backend(key)

    def _make(self, key: str) -> Any:
        spec = REGISTRY[key]
        kind = self.backend(key)
        factory = spec.sim_factory if kind == "sim" else spec.real_factories[kind]
        return factory(self.ctx)

    def _derive_mode(self) -> str:
        sims = [getattr(self.drivers[k], "is_sim", False) for k in REGISTRY]
        if all(sims):
            return "sim"
        if not any(sims):
            return "real"
        return "mixed"

    # ---- 빌드 / 연결 ----

    def build_all(self) -> dict[str, Any]:
        """모든 장비를 현재 config대로 (재)생성. 연결은 하지 않는다."""
        with self._lock:
            old = {k: self.drivers.get(k) for k in REGISTRY}
            new = {k: self._make(k) for k in REGISTRY}
            self.drivers.clear()
            self.drivers.update(new)
            self.drivers["mode"] = self._derive_mode()
            for drv in old.values():
                _safe_close(drv)
        return self.drivers

    async def connect_all(self) -> None:
        for key in REGISTRY:
            await self.connect(key)

    def _rebuild_slot(self, key: str) -> Any:
        """슬롯을 현재 config대로 새(미연결) 인스턴스로 교체하고 옛 것을 돌려준다."""
        with self._lock:
            old = self.drivers.get(key)
            self.drivers[key] = self._make(key)
            self.drivers["mode"] = self._derive_mode()
            self.connected_at.pop(key, None)
        return old

    async def _connect_unlocked(self, key: str,
                                *, propagate: bool = False) -> None:
        """슬롯의 드라이버를 연결. 실패는 미연결로 보고 (예외 삼킴 + 로그)."""
        self.desired[key] = True
        drv = self.drivers[key]
        try:
            await asyncio.to_thread(drv.connect)
            self.connected_at[key] = time.time()  # 연결 직후 호밍 유예의 기준 시각
            self._log(f"{REGISTRY[key].label} 연결 "
                      f"({'SIM' if getattr(drv, 'is_sim', False) else type(drv).__name__})")
        except Exception as exc:  # noqa: BLE001
            self._log(f"{REGISTRY[key].label} 연결 실패: {exc}", "warn")
            if getattr(drv, "reconnect_blocked", False):
                self.desired[key] = False
                self._log(f"{REGISTRY[key].label} 자동 재연결 중지: "
                          "장비 점검 후 수동 연결 필요", "error")
            if propagate:
                raise

    async def connect(self, key: str, *, propagate: bool = False) -> None:
        async with self._op_locks[key]:
            await self._connect_unlocked(key, propagate=propagate)

    async def disconnect(self, key: str) -> None:
        """연결 해제 = 닫고 같은 백엔드의 새(미연결) 인스턴스로 교체.
        운영자 의도를 '해제'로 기록 → 워치독이 자동 재연결하지 않는다."""
        async with self._op_locks[key]:
            if not self.desired.get(key, True):
                return
            self.desired[key] = False
            old = self._rebuild_slot(key)
            await asyncio.to_thread(_safe_close, old)
            self._log(f"{REGISTRY[key].label} 연결 해제")

    async def reconnect(self, key: str, *, propagate: bool = False) -> None:
        """재연결 = 새 인스턴스로 교체 후 연결. desired는 True 유지(자동복구 포함)."""
        async with self._op_locks[key]:
            self.desired[key] = True
            old = self._rebuild_slot(key)
            await asyncio.to_thread(_safe_close, old)
            await self._connect_unlocked(key, propagate=propagate)

    async def set_mode(self, mode: str) -> str:
        """마스터 SIM↔REAL 전환 — 전부 재빌드 후 연결. 실패는 미연결로."""
        self.cfg.set("drivers.mode", mode)
        self.build_all()
        for key in REGISTRY:
            self.desired[key] = True  # 모드 전환 = 전부 연결 의도
        await self.connect_all()
        self._log(f"드라이버 모드 전환 → {self.drivers['mode'].upper()}")
        return self.drivers["mode"]

    async def close_all(self) -> None:
        for key in REGISTRY:
            await asyncio.to_thread(_safe_close, self.drivers.get(key))

    # ---- 설정 (ASCOM ProgID / PWI4 URL / 백엔드) ----

    def list_ascom(self, key: str) -> list[dict[str, str]]:
        """이 장비 타입으로 등록된 ASCOM 드라이버 ProgID 목록 (설정 드롭다운용).
        ASCOM Platform/pywin32가 없거나 비-Windows면 빈 목록."""
        spec = REGISTRY[key]
        if not spec.ascom_type:
            return []

        def _enum() -> list[dict[str, str]]:
            import win32com.client
            profile = win32com.client.Dispatch("ASCOM.Utilities.Profile")
            profile.DeviceType = spec.ascom_type
            out: list[dict[str, str]] = []
            for kv in profile.RegisteredDevices(spec.ascom_type):
                try:
                    out.append({"progid": str(kv.Key), "name": str(kv.Value)})
                except Exception:
                    pass
            return out

        try:
            return _run_com(_enum) or []
        except Exception as exc:  # noqa: BLE001
            self._log(f"ASCOM 목록 조회 실패 ({spec.ascom_type}): {exc}", "warn")
            return []

    def setup_dialog(self, key: str) -> None:
        """장치 ASCOM 드라이버의 설정창(SetupDialog)을 띄운다 — COM 포트 등.
        모달 창이 이 프로세스 데스크톱(=관측 PC)에 뜬다. 사용자가 포트를 정하고
        닫으면 ASCOM Profile에 저장돼 이후 connect가 성공한다 (NINA의 'Properties'와
        동일). ProgID가 설정돼 있어야 한다."""
        spec = REGISTRY[key]
        progid = str(self.cfg.get(spec.progid_key, "")) if spec.progid_key else ""
        if not spec.ascom_type:
            raise ValueError(f"{spec.label}: ASCOM 장비가 아니라 설정창이 없습니다")
        if not progid:
            raise ValueError(f"{spec.label}: 먼저 ASCOM ProgID를 선택·저장하세요")

        def _show():
            import win32com.client
            dev = win32com.client.Dispatch(progid)
            try:
                dev.SetupDialog()
            finally:
                try:
                    dev.Dispose()
                except Exception:
                    pass

        _run_com(_show)
        self._log(f"{spec.label} 드라이버 설정창 닫힘 ({progid})")

    def configure(self, key: str, *, progid: str | None = None,
                  url: str | None = None, backend: str | None = None) -> None:
        """기기별 설정을 오버레이에 저장 (config.toml은 손대지 않음)."""
        spec = REGISTRY[key]
        if progid is not None and spec.progid_key:
            self.cfg.set(spec.progid_key, progid)
        if url is not None and spec.url_key:
            self.cfg.set(spec.url_key, url)
        if backend is not None:
            if backend != "sim" and backend not in spec.real_factories:
                raise ValueError(f"{key}: 지원하지 않는 백엔드 '{backend}'")
            self.cfg.set(f"drivers.{key}", backend)

    def describe(self) -> dict[str, Any]:
        """SYSTEM 탭용 장비 설정/역량 목록. 실시간 연결상태·장비명은
        /api/status 스냅샷에서 가져온다 (여기선 COM을 건드리지 않음).

        설정 필드는 *현재 선택된 백엔드*가 요구하는 것만 노출한다(config_kind).
        그래서 카메라를 zwo로 잡으면 ASCOM ProgID 줄이 사라지고 '자동연결'로 뜬다."""
        devices = []
        for key, spec in REGISTRY.items():
            selected = self.selected_backend(key)
            cfg_kind = BACKEND_CONFIG.get(selected, "none")
            devices.append({
                "key": key, "label": spec.label,
                "backend": self.backend(key),     # 지금 실제 구동 중 백엔드 (칩)
                "selected": selected,             # 운영자가 REAL용으로 고른 백엔드
                "real_kinds": spec.real_kinds,
                "ascom_type": spec.ascom_type,
                "config_kind": cfg_kind,          # progid | url | auto | none
                "has_progid": cfg_kind == "progid" and bool(spec.progid_key),
                "has_url": cfg_kind == "url" and bool(spec.url_key),
                "progid": str(self.cfg.get(spec.progid_key, "")) if spec.progid_key else "",
                "url": str(self.cfg.get(spec.url_key, "")) if spec.url_key else "",
            })
        return {"mode": self.drivers.get("mode", "sim"),
                "master_mode": self.master_mode, "devices": devices}
