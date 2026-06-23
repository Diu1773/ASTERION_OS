"""Asterion — 통합 관측 플랫폼 (FastAPI: REST + WebSocket + 대시보드).

플랫폼 본체. core(데이터·액션 백본)와 drivers(하드웨어 브리지) 위에
named system들(watchtower 환경·안전, skyflat 오토플랫, capture 수동
촬영)을 조립해 단일 웹으로 노출한다.
"""

from __future__ import annotations

import asyncio
import os
import re
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    Body, FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .access.auth import AccessPolicy, build_auth_router
from .access.middleware import AccessMiddleware
from .access.ratelimit import RateLimiter, RateLimitMiddleware
from .camera.capture import CaptureService
from .camera.cooler import CoolerController
from .config import Config
from .core import ephemeris, skygraph
from .core.actions import ActionBus, ActionError
from .core.events import EventHub
from .core.focus_offset import apply_filter_focus_offset
from .archive import ArchiveRecovery
from .core.ontology import (
    ActionLog, Alert, Db, Frame, ObservationSession, WeatherRecord,
    row_to_dict, set_current_site,
)
from .core.preview import stretch_to_png
from .drivers import REGISTRY, ConnectionManager
from .drivers.sim import TwilightSim
from .agent.api import build_agent_router
from .agent.core import Agent
from .agent.providers import ProviderHub
from .agent.toolkit import ToolKit
from .analysis.api import build_analysis_router
from .analysis.calibration import CalibrationLibrary
from .analysis.forge import Forge
from .analysis.framedata import FrameData
from .analysis.sentinel import Sentinel
from .operation.api import build_operation_router
from .operation.hooks import sim_autofocus, sim_platesolve
from .operation.meridian import Meridian
from .operation.orchestrator import ObservationOrchestrator
from .operation.night_runner import NightRunner
from .satellite import SatelliteProxy
from .skyflat.autoflat import AutoFlatParams, AutoFlatRunner
from .watchtower.dome_guard import DomeGuard
from .watchtower.metrics import render_metrics
from .watchtower.recovery import ConnectionWatchdog
from .watchtower.status import StatusSampler

WEB_DIR = Path(__file__).resolve().parent / "web"


# ---------- 요청 모델 ----------

class AutoFlatStartReq(BaseModel):
    filters: list[str] | None = None
    frames_per_filter: int | None = None
    adu_min: float | None = None
    adu_max: float | None = None
    dither_arcsec: float | None = None
    settle_seconds: float | None = None
    initial_exposure: float | None = None
    min_exposure: float | None = None
    max_exposure: float | None = None


class GotoReq(BaseModel):
    alt: float
    az: float


class TrackingReq(BaseModel):
    on: bool


class FilterReq(BaseModel):
    position: int


class CoolerReq(BaseModel):
    on: bool
    setpoint: float | None = None


class TwilightReq(BaseModel):
    enabled: bool


class DevModeReq(BaseModel):
    mode: str  # "sim" | "real"


class DomeShutterReq(BaseModel):
    open: bool


class DomeRotateReq(BaseModel):
    direction: str   # cw | ccw | stop


class DomeAzReq(BaseModel):
    azimuth: float


class DomeSlaveReq(BaseModel):
    on: bool


class GotoRaDecReq(BaseModel):
    ra: str   # "5.59" | "05:35:17" | "5h35m17s"
    dec: str  # "-5.39" | "-05:23:28"


class JogReq(BaseModel):
    direction: str       # N | S | E | W
    arcsec: float = 10.0


class MoveAxisReq(BaseModel):
    direction: str            # N | S | E | W
    rate: str = "normal"      # slow | normal | fast (서버가 caps로 deg/s 해석)


class FocuserMoveReq(BaseModel):
    position: int


class FocuserNudgeReq(BaseModel):
    delta: int


class CaptureStartReq(BaseModel):
    exposure_s: float = 2.0
    frame_type: str = "LIGHT"   # LIGHT | FLAT | DARK | BIAS
    count: int = 1              # 0 = 무한 (정지 버튼까지)
    interval_s: float = 1.0
    binning: int = 1            # NxN 하드웨어 비닝


class AutosaveReq(BaseModel):
    on: bool


class DeviceReq(BaseModel):
    device: str   # REGISTRY 키: mount | camera | filterwheel | focuser | weather


class DeviceConfigReq(BaseModel):
    device: str
    progid: str | None = None   # ASCOM ProgID
    url: str | None = None      # PWI4 base URL
    backend: str | None = None  # "sim" | "ascom" | "pwi4"


class SetupConfigReq(BaseModel):
    device: str
    setup: dict | None = None   # 그 장비 Setup 섹션 부분/전체 패치 (DEVICE_SETUP_CONTRACT §3)


def create_app() -> FastAPI:
    cfg = Config.load(os.environ.get("ASTERION_CONFIG"))
    import time as _time
    app_start = _time.monotonic()   # 업타임 기준 (/api/sysinfo)
    # 사이트 식별자 — 모든 INSERT의 site 컬럼 기본값에 반영(멀티사이트 프리베이크). DB 쓰기 전에.
    set_current_site(str(cfg.get("site.name", "default")))
    lat = float(cfg.get("site.latitude", 36.6))
    lon = float(cfg.get("site.longitude", 127.5))

    def sun_alt_now() -> float:
        return ephemeris.sun_altaz(ephemeris.now_utc(), lat, lon)[0]

    def lst_now() -> float:
        return ephemeris.lst_hours(ephemeris.now_utc(), lon)

    twilight = TwilightSim(efold_s=float(cfg.get("sim.twilight_efold_s", 240.0)))
    events = EventHub()
    # 연결 관리자가 레지스트리대로 드라이버를 빌드·소유한다. drivers dict는
    # sampler/runner/capture가 공유하는 살아있는 단일 상태 (재빌드해도 같은 객체).
    conn = ConnectionManager(cfg, twilight, sun_alt_now, lst_now, events)
    drivers = conn.drivers
    db = Db(cfg.data_dir / "asterion.db")
    archive = ArchiveRecovery(db, cfg.data_dir / "frames")   # 파일↔DB 정합성(§9.3)
    # 시뮬레이터 보존 정리 — sim 모드 + 활성화일 때만(실측 데이터 미적용). cfg.data_dir이
    # sim이면 data/sim/로 분기돼 있으므로 정리는 그 격리 저장소에만 닿는다. 기동 시 1회 +
    # 주기 스윕으로 cutoff(기본 90일=한 3달)보다 오래된 프레임 파일+DB행을 지운다.
    from .core.retention import Retention
    retention = None
    if drivers.get("mode") == "sim" and bool(cfg.get("sim_retention.enabled", True)):
        retention = Retention(db, cfg.data_dir / "frames",
                              days=float(cfg.get("sim_retention.days", 90.0)),
                              events=events)
    sampler = StatusSampler(cfg, drivers, twilight, events, db)
    # 분산 §7: 로컬 기상 장치 없을 때 원격 ingestion 값으로 폴백(fail-closed 유지). config로 끔.
    if bool(cfg.get("weather.ingest_fallback", True)):
        from .watchtower.ingest import current_weather
        _wx_max_age = float(cfg.get("safety.weather_unsafe_seconds", 120.0))
        # rank6 — 최근 보고하던 소스가 침묵(dropout)하면 위험 마스킹 방지 위해 fail-closed(기본 on).
        _wx_drop_win = float(cfg.get("safety.weather_dropout_window_seconds", 600.0))
        _wx_drop_holds = bool(cfg.get("safety.weather_dropout_holds", True))
        sampler.weather_ingest_fn = lambda: current_weather(
            db, _wx_max_age, dropout_window_s=_wx_drop_win, dropout_holds=_wx_drop_holds)
    # 위험 알림(무인 운영 안전 루프) — 안전 스냅샷을 룰로 평가해 Alert 발화(추가형, 읽기만).
    from .watchtower.alert import AlertManager
    alert_mgr = AlertManager(db, events)
    sampler.alert_fn = alert_mgr.evaluate
    # 끊긴 장비를 자동으로 다시 붙이는 워치독 (자율형 복구)
    watchdog = ConnectionWatchdog(cfg, conn, sampler, events)
    bus = ActionBus(db, events, lambda: sampler.snapshot)
    # 프레임 미리보기 홀더 (캡처/플랫 직후 스트레치 PNG)
    frame_preview = {"png": b"", "token": 0, "meta": {}}
    # 위성영상 프록시 — KMA GK-2A 최신 프레임을 고정 로컬 URL로 서빙 (lazy fetch)
    satellite = SatelliteProxy(cfg)
    # Meridian — 관측 계획 계층(ObservationPlan CRUD/승인). 실행은 Orchestrator가(Ph7-2~).
    meridian = Meridian(db, events)

    async def publish_preview(img, meta):
        # Forge 실시간 보정(켜져 있고 LIGHT일 때만) — 보정본을 프리뷰로. 마스터 없으면
        # forge.process가 원본 그대로 돌려준다. forge는 아래에서 생성(런타임 late-bind).
        _ftype = (meta.get("type") or "").upper()
        if forge.enabled and _ftype == "LIGHT":
            img, finfo = await asyncio.to_thread(forge.process, img, meta)
            if finfo.get("applied"):
                meta = {**meta, "forge": finfo}
                if forge.save_calibrated:
                    cal_path = await asyncio.to_thread(
                        forge.save_calibrated_fits, img, meta)
                    if cal_path:
                        meta = {**meta, "forge_file": cal_path}
        elif forge.enabled and _ftype in ("FLAT", "DARK", "BIAS"):
            # OS가 새 보정 프레임을 캡처함 → 마스터 캐시 무효화해 다음 LIGHT가
            # 이걸 반영해 재해석하게 한다 (자율형: 찍으면 자동 연결).
            forge.clear_cache()
        png = await asyncio.to_thread(stretch_to_png, img)
        if not png:
            return
        frame_preview["png"] = png
        frame_preview["token"] += 1
        frame_preview["meta"] = meta
        events.emit({"type": "preview", "token": frame_preview["token"],
                     "meta": meta})

    runner = AutoFlatRunner(cfg, drivers, bus, db, events, twilight,
                            sun_alt_now, cfg.data_dir / "frames",
                            preview_cb=publish_preview,
                            safety_fn=lambda: sampler.current_safety())
    sampler.autoflat_status = runner.status_dict
    capture = CaptureService(
        cfg, drivers, bus, db, events, cfg.data_dir / "frames",
        blocked_fn=lambda: ("오토플랫 실행 중 — 카메라 사용 불가"
                            if runner.running()
                            else "관측(Orchestrator) 실행 중 — 카메라 사용 불가"
                            if orch.running() else None),
        preview_cb=publish_preview)
    sampler.capture_status = capture.status_dict
    # 쿨러 램프 거버너 — setup.camera.max_dt_c(°C/min)대로 쿨다운/웜업 속도 제한(센서 보호).
    # 틱은 샘플러 1Hz 루프가 구동, 상태는 /api/status snapshot.cooler로 노출.
    cooler = CoolerController(cfg, drivers, events)
    sampler.cooler_status = cooler.status_dict
    sampler.cooler_tick = cooler.tick
    # Orchestrator — 승인된 ObservationPlan을 표준 과학 시퀀스로 실행(Ph7). 안전 게이트는
    # State Store의 safety 스냅샷을 소비하고, plate-solve/autofocus는 SIM 후크를 주입한다.
    # (real 모드용 실제 솔버/AF로 교체 전까지 SIM 후크는 즉시성공 — PLAN 결정 로그 참조.)
    orch = ObservationOrchestrator(
        cfg, drivers, bus, db, events, meridian, cfg.data_dir / "frames",
        preview_cb=publish_preview,
        safety_fn=lambda: sampler.current_safety(),
        platesolve_fn=sim_platesolve, autofocus_fn=sim_autofocus,
        occupancy_fn=lambda: ("수동 캡처 실행 중 — 관측 시작 거부" if capture.active()
                              else "오토플랫 실행 중 — 관측 시작 거부" if runner.running()
                              else None))
    sampler.orchestrator_status = orch.status_dict
    # Night Runner — 무인 야간 운영기(NIGHT_RUNNER_PLAN.md). 승인된 시간표를 슬롯 순서대로
    # Orchestrator 위에서 시퀀싱한다(장비 직접조작 X). 안전 스냅샷을 슬롯 진입 전 소비.
    night_runner = NightRunner(meridian, orch, events, cfg=cfg,
                               safety_fn=lambda: sampler.current_safety())
    sampler.night_runner_status = night_runner.status_dict
    # Sentinel — 프레임 품질 평가(Ph8, 로드맵 §10.4). 적재된 QualityMetric/Frame 지표로 판정.
    sentinel = Sentinel(cfg, db)
    # FrameData — 이미지/픽셀 뷰어 백엔드(저장 FITS→히스토그램/라인프로파일/통계 JSON).
    framedata = FrameData(db, float(cfg.get("camera.saturation_adu", 65535)))
    # Calibration Library — bias/dark/flat 마스터 등록·매칭(Ph8, 로드맵 §10.5).
    calibration = CalibrationLibrary(db)
    # Forge — 실시간 단일 프레임 보정(퀵룩). 캡처된 LIGHT에 마스터 즉시 적용(numpy, 경량).
    # 무거운 정렬·적분 스택은 AstralImage 서브프로세스(온디맨드)로 별도. 토글은 config/API.
    forge = Forge(cfg, db, calibration, frames_dir=cfg.data_dir / "frames",
                  events=events)
    sampler.forge_status = forge.status_dict
    # 시계열 품질: 캡처 시 Forge로 보정→측정→QualityMetric 영속(lazy 주입 — forge가 orch보다 뒤 생성).
    orch.measure_fn = forge.measure_calibrated
    # 돔 가드 — 안전 '판정'(safety)과 분리된 '행동'(비상 자동닫힘 + 슬레이빙). 샘플러가
    # 매 스냅샷마다 호출. 장비 키 비의존 — 돔이 REGISTRY에 있으면 자동 동작.
    _dome_guard = DomeGuard(
        drivers, bus, events,
        dome_cfg={
            "dome_radius_m": float(cfg.get("dome.radius_m", 2.0)),
            "mount_offset_e_m": float(cfg.get("dome.mount_offset_e_m", 0.0)),
            "mount_offset_n_m": float(cfg.get("dome.mount_offset_n_m", 0.0)),
            "mount_offset_up_m": float(cfg.get("dome.mount_offset_up_m", 0.0)),
            "gem_dec_offset_m": float(cfg.get("dome.gem_dec_offset_m", 0.0)),
        },
        az_tolerance_deg=float(cfg.get("dome.az_tolerance_deg", 4.0)),
        shutter_close_timeout_s=float(cfg.get("dome.shutter_close_timeout_s", 90.0)),
        cfg=cfg)
    # 태양 폐루프 감시 — 슬루/추적 중 OTA가 태양 제외각 안으로 들어오면 긴급 정지(진입 가드를
    # 뚫고 들어온 경우의 최후 방어선; 주간에만 동작해 야간 정상 슬루는 방해 안 함).
    from .watchtower.solar_watchdog import SolarWatchdog
    _solar_watchdog = SolarWatchdog(drivers, bus, events, cfg)
    # 원격 운영자 데드맨(REMOTE_ACCESS_PLAN Phase C) — 수동 원격 세션 하트비트가 끊기면
    # 세이프-스테이트(추적정지·돔닫힘·파킹). 무인 운영(NightRunner)은 면제. config로 끔(기본 off).
    from .watchtower.session_watchdog import SessionWatchdog
    _session_watchdog = SessionWatchdog(
        drivers, bus, events, cfg,
        alert_fn=lambda title, detail, rule_id="session_deadman": alert_mgr.fire(
            rule_id, "critical", title, detail, cooldown_s=60.0))

    async def _safety_actuator(snap):
        await _dome_guard(snap)
        await _solar_watchdog(snap)
        await _session_watchdog(snap)
    sampler.safety_actuator = _safety_actuator

    # 기상예보 — 스케줄러 게이팅·대시보드. config에 KMA 키 있으면 실예보, 없으면 Sim 폴백.
    from .watchtower.forecast import ForecastService
    forecast_svc = ForecastService(cfg)

    # 강수 예보 조기경보 — 선제 '경고'만(물리행동 0). 예보는 확률이라 그걸로 닫지 않는다;
    # 실제 닫기는 센서 감지(EMERGENCY_CLOSE)가 한다. 샘플러 alert 평가에 동반(예보는 정시
    # 캐시라 무비용, fire 쿨다운이 스팸 차단). config [weather.forecast_alert]로 끔.
    from .watchtower.forecast_watch import ForecastWatch
    _forecast_watch = ForecastWatch(forecast_svc, alert_mgr, cfg)
    _alert_eval = sampler.alert_fn
    def _alerts_with_forecast(snap):
        if _alert_eval is not None:
            _alert_eval(snap)
        _forecast_watch.check()
    sampler.alert_fn = _alerts_with_forecast
    # 예보→긴 노출 보류(되돌릴 수 있는 선제 결정, 물리행동 아님; 실제 닫기는 센서가 함).
    # orch는 measure_fn처럼 post-hoc 주입(생성순서 무관). 안전 게이트와 별개 계층.
    orch.defer_fn = _forecast_watch.should_defer_exposure

    # AI 에이전트 (§12 입구) — 대시보드 임베디드 대화 제어. ProviderHub가 named
    # provider(groq/openai/ollama/자체) 여러 개를 들고 active 하나를 LLM으로 노출 —
    # 공급자/모델을 런타임에 스왑. 도구는 인프로세스, 실행계는 ActionBus 안전게이트 통과.
    toolkit = ToolKit(cfg=cfg, snapshot_fn=lambda: sampler.snapshot, meridian=meridian,
                      orchestrator=orch, bus=bus, drivers=drivers, db=db, sentinel=sentinel,
                      night_runner=night_runner, forecast=forecast_svc,
                      safety_fn=lambda: sampler.current_safety())
    agent = Agent(
        ProviderHub(cfg), toolkit,
        system_prompt=str(cfg.get("agent.system_prompt", "")))

    # 멀티나잇 캠페인 — 대상군을 여러 밤에 완주. 정의 영속 + 진행률/소요밤, plan-night은 기존 스케줄러.
    from .core.campaign import CampaignManager
    campaign_mgr = CampaignManager(db, meridian)

    # 태양 회피 사전조건 — 운영자 슬루도 태양/근방을 향하면 거부. allow_solar_slew(책임자 config)면
    # 통과(ActionLog에 감사 기록). ra/dec 또는 alt/az 중 하나를 준다.
    def _solar_precond(*, ra_hours=None, dec_degs=None, alt=None, az=None
                       ) -> tuple[str, bool, str]:
        ok, _sep, msg = ephemeris.solar_exclusion_check(
            exclusion_deg=float(cfg.get("safety.sun_avoidance_deg", 15.0)),
            ra_hours=ra_hours, dec_deg=dec_degs, alt_deg=alt, az_deg=az,
            lat_deg=lat, lon_deg=lon)
        allow = bool(cfg.get("safety.allow_solar_slew", False))
        return ("sun_sep_ok", ok or allow, msg)

    # 상대 이동(jog/move_axis) 태양 가드 — 최대한 안전. 결과 지향을 단정하긴 어려우므로:
    #   현재 지향을 읽을 수 있으면 그 위치가 (제외각+여유) 안인지 정밀 검사(태양 근접이면 거부 →
    #   근처에서 살살 미는 것도 차단), 못 읽으면 주간일 때 보수적으로 거부(fail-closed).
    # allow_solar_slew(책임자 config)면 통과. AI엔 이 경로(상대이동) 자체가 노출되지 않는다.
    def _relative_slew_solar_block() -> tuple[str, bool, str]:
        if bool(cfg.get("safety.allow_solar_slew", False)):
            return ("sun_rel_ok", True, "")
        margin = float(cfg.get("safety.sun_avoidance_deg", 15.0)) + 5.0  # 이동 여유 마진
        m = (sampler.snapshot or {}).get("mount") or {}
        if m.get("connected"):
            if m.get("alt") is not None and m.get("az") is not None:
                ok, sep, _ = ephemeris.solar_exclusion_check(
                    exclusion_deg=margin, alt_deg=m["alt"], az_deg=m["az"],
                    lat_deg=lat, lon_deg=lon)
                return ("sun_rel_ok", ok,
                        f"현재 지향이 태양 {sep:.0f}° 이내 — 상대 슬루 거부 (태양 근접, 책임자만 우회)")
            if m.get("ra_hours") is not None and m.get("dec_degs") is not None:
                ok, sep, _ = ephemeris.solar_exclusion_check(
                    exclusion_deg=margin, ra_hours=m["ra_hours"], dec_deg=m["dec_degs"])
                return ("sun_rel_ok", ok,
                        f"현재 지향이 태양 {sep:.0f}° 이내 — 상대 슬루 거부 (태양 근접, 책임자만 우회)")
        # 지향 미상(미연결/좌표 없음) → 주간이면 거부(fail-closed)
        return ("sun_rel_ok", sun_alt_now() <= -0.5,
                "현재 지향 불명 + 주간 — 상대 슬루 거부 (태양 회피, 책임자만 우회)")

    # 장비 연결 변경(연결/해제/모드전환)을 막는 공용 사전조건 — 세션 중엔 금지.
    def conn_preconditions() -> list[tuple[str, bool, str]]:
        return [
            ("autoflat_idle", not runner.running(),
             "오토플랫 실행 중에는 연결 변경 불가"),
            ("capture_idle", not capture.active(),
             "캡처 실행 중에는 연결 변경 불가"),
            ("orchestrator_idle", not orch.running(),
             "관측 실행 중에는 연결 변경 불가"),
        ]

    async def _retention_loop():
        """기동 시 1회 + sweep_interval_hours마다 sim 저장소 보존 정리(추가형)."""
        interval_s = max(300.0, float(
            cfg.get("sim_retention.sweep_interval_hours", 6.0)) * 3600.0)
        while True:
            try:
                await asyncio.to_thread(retention.prune)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                events.log("retention", f"보존 정리 오류: {exc}", "error")
            await asyncio.sleep(interval_s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        events.attach_loop(asyncio.get_running_loop())
        # 장비 연결은 백그라운드로 — 한 장비가 꺼져있거나 응답이 없어도 서버 기동(포트 bind)·
        # 대시보드를 막지 않는다(관측 OS 견고성). 장비는 연결되는 대로 /api/status에 반영.
        app.state.connect_task = asyncio.create_task(conn.connect_all())
        sampler.start()
        watchdog.start()
        app.state.retention_task = (asyncio.create_task(_retention_loop())
                                    if retention is not None else None)
        events.log("system",
                   f"Asterion v{__version__} 가동 — 모드 {drivers['mode'].upper()}")
        # 위험 오버라이드 가시화(Phase C2) — allow_solar_slew가 켜진 채 기동하면 경보로 알린다.
        if bool(cfg.get("safety.allow_solar_slew", False)):
            alert_mgr.fire("solar_override_active", "critical",
                           "태양 회피 오버라이드 활성",
                           "allow_solar_slew=true — OTA 태양 근접 슬루 허용(센서/광학 손상 위험)")
        # 시크릿 파일 권한 점검(Phase D, POSIX) — config.local.json이 group/other 읽기 가능하면 경고.
        try:
            import os as _os
            _p = cfg.overlay_path
            if _os.name == "posix" and _p.exists() and (_p.stat().st_mode & 0o077):
                events.log("system", f"⚠ 보안: {_p.name} 권한이 느슨합니다(group/other 접근 가능) "
                           "— `chmod 600` 권장(시크릿 보호)", "warn")
        except Exception:
            pass
        yield
        if app.state.retention_task is not None:
            app.state.retention_task.cancel()
            try:
                await app.state.retention_task
            except asyncio.CancelledError:
                pass
        await watchdog.stop()
        await sampler.stop()
        await conn.close_all()

    app = FastAPI(title="Asterion", version=__version__, lifespan=lifespan)

    # 원격 접속 인증/인가 게이트 (REMOTE_ACCESS_PLAN Phase A) — 추가형, 기본 꺼짐(하위호환).
    # 켜면(config server.auth.enabled=true) 모든 /api·/ws가 세션/토큰+역할을 요구하고,
    # 감사로그 actor에 사람별 신원이 박힌다. 라우트 본문은 무수정(경로/메서드 기반 정책).
    access_policy = AccessPolicy(cfg)
    app.add_middleware(AccessMiddleware, policy=access_policy)
    # Host 헤더 핀(Phase B) — 설정 시 Tailscale .ts.net 이름/로컬만 허용(호스트 헤더 위조 차단).
    # 미설정(기본)이면 추가 안 함 → 로컬 개발 무영향. TrustedHost를 뒤에 add해 최외곽(먼저 실행).
    _allowed = cfg.get("server.allowed_hosts", None)
    if isinstance(_allowed, list) and _allowed:
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        app.add_middleware(TrustedHostMiddleware,
                           allowed_hosts=[str(h) for h in _allowed])
    # 레이트리밋(Phase D) — 인증 무관 최외곽에서 /login(브루트포스)·/api/agent/chat(비용)만 제한.
    # 마지막 add라 가장 바깥 → 인증 처리 전에 throttle. 지정 경로 외에는 무영향.
    app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(cfg))

    @app.exception_handler(ActionError)
    async def _action_error(_, exc: ActionError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    # ---------- 페이지/정적 ----------

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/preview.png", include_in_schema=False)
    async def preview_png():
        if not frame_preview["png"]:
            raise HTTPException(404, "아직 캡처된 프레임 없음")
        return Response(frame_preview["png"], media_type="image/png",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/preview/meta")
    async def preview_meta():
        return {"token": frame_preview["token"], "meta": frame_preview["meta"]}

    # ---------- 위성영상 (KMA GK-2A 프록시) ----------

    @app.get("/api/satellite/latest.png", include_in_schema=False)
    async def satellite_latest():
        """천리안2A 최신 프레임 (고정 URL). 패널은 이 URL만 주기적으로 새로고침."""
        png, stamp = await satellite.get()
        if not png:
            raise HTTPException(503, "위성 영상을 가져오지 못했습니다 (KMA 응답 없음/비활성)")
        return Response(png, media_type=satellite.media_type,
                        headers={"Cache-Control": "no-store", "X-Sat-Frame": stamp})

    @app.get("/api/satellite/meta")
    async def satellite_meta():
        """현재 캐시된 프레임 시각·신선도 (패널 메타 표시용)."""
        return satellite.meta()

    # ---------- 조회 ----------

    @app.get("/api/status")
    async def api_status():
        return sampler.snapshot or {"mode": "starting"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        """Prometheus 노출 — Grafana가 스크레이프할 현재 텔레메트리/상태 게이지.
        인증 켜짐: scope=metrics 토큰(Prometheus Bearer) 또는 로그인 사용자."""
        import shutil
        extra = {"asterion_uptime_seconds": round(_time.monotonic() - app_start)}
        try:
            extra["asterion_disk_free_bytes"] = shutil.disk_usage(
                str(cfg.data_dir)).free
        except Exception:
            pass
        text = render_metrics(sampler.snapshot or {}, extra)
        return Response(text, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.post("/api/session/heartbeat")
    async def session_heartbeat():
        """원격 운영자 생존 신호 — 데드맨 재무장(고빈도, ActionLog 미경유)."""
        _session_watchdog.heartbeat()
        return {"ok": True, "deadman": _session_watchdog.status()}

    @app.get("/api/session/deadman")
    async def session_deadman_status():
        """원격 세션 데드맨 상태 — 활성/무장/하트비트 경과/타임아웃/발화 여부."""
        return _session_watchdog.status()

    # Skygraph(Ph9) — 대상 중심 dossier. 한 대상의 관측요청·프레임·품질·가시성·추천 집계.
    _site_lat = float(cfg.get("site.latitude", 36.64))

    @app.get("/api/targets")
    async def api_targets():
        return skygraph.list_targets(db, lat=_site_lat)

    @app.get("/api/targets/{name}")
    async def api_target_dossier(name: str):
        return skygraph.target_dossier(db, name, lat=_site_lat)

    @app.get("/api/photometry/{name}")
    async def api_photometry(name: str):
        """대상의 LIGHT 프레임에 경량 조리개 측광 → 시간↔등급 라이트커브(점광원용)."""
        frames = skygraph.target_light_frames(db, name)
        return {"target": name, "n": len(frames), "zp": 25.0,
                "points": framedata.light_curve(frames)}

    @app.get("/api/forecast")
    async def api_forecast(hours: int = 24):
        """단기 기상예보(정시별 구름·강수확률·바람·기온). 제공자=sim/kma. 스케줄러 게이팅과 동일 소스."""
        h = max(1, min(48, int(hours)))
        fc = forecast_svc.upcoming(h)
        return {"provider": forecast_svc.provider.name,
                "hours": [f.as_dict() for f in fc]}

    @app.get("/api/campaigns")
    async def api_campaigns():
        """멀티나잇 캠페인 목록 — 각 진행률(완료/잔여/퍼센트/예상 소요밤) 포함."""
        return {"campaigns": campaign_mgr.list()}

    @app.post("/api/campaigns")
    async def api_campaign_create(body: dict = Body(...)):
        """캠페인 생성 — name(필수)·goal·target_set(messier/galaxies/…)·type_filter·profile·
        strategy(filters/exposure_s/count_per_filter)·per_night·deadline_utc."""
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name 필수")
        return campaign_mgr.create(
            name=name, goal=body.get("goal", ""),
            target_set=body.get("target_set", "all"), type_filter=body.get("type_filter", ""),
            profile=body.get("profile", ""), strategy=body.get("strategy"),
            per_night=body.get("per_night", 6), deadline_utc=body.get("deadline_utc"))

    @app.get("/api/campaigns/{cid}")
    async def api_campaign(cid: int):
        p = campaign_mgr.progress(cid)
        if p is None:
            raise HTTPException(404, f"캠페인 #{cid} 없음")
        return p

    @app.patch("/api/campaigns/{cid}")
    async def api_campaign_patch(cid: int, body: dict = Body(...)):
        try:
            p = campaign_mgr.set_status(cid, (body.get("status") or "").strip())
        except ValueError as e:
            raise HTTPException(400, str(e))
        if p is None:
            raise HTTPException(404, f"캠페인 #{cid} 없음")
        return p

    @app.post("/api/campaigns/{cid}/plan-night")
    async def api_campaign_plan_night(cid: int, hours: float = 8.0, count: int = 6):
        """오늘 밤 잔여 대상을 비겹침 시간표로 배분(draft) — 캠페인 정의로 기존 스케줄러 호출."""
        inp = campaign_mgr.plan_inputs(cid)
        if inp is None:
            raise HTTPException(404, f"캠페인 #{cid} 없음")
        created, had_dark, moon_sum = await asyncio.to_thread(
            toolkit._night_plan, float(hours), inp["types"], int(count),
            inp["strategy"], True, inp["exclude"], inp["idpred"], inp["profile"])
        return {"created": created, "count": len(created), "dark_window": had_dark,
                "progress": campaign_mgr.progress(cid)}

    @app.get("/api/frames/{frame_id}/provenance")
    async def api_frame_provenance(frame_id: int):
        """프레임 계보(§9.4) — Target→Plan→Session→Frame + 품질·기상·보정·결정."""
        p = skygraph.frame_provenance(db, frame_id)
        if p is None:
            raise HTTPException(404, f"프레임 #{frame_id} 없음")
        return p

    @app.get("/api/feedback/{name}")
    async def api_feedback(name: str):
        """피드백 학습 — 대상의 결과를 분석해 다음 관측 추천(노출/재촬영/필터). 조회는 읽기전용
        (persist=False) — 단순 페이지 조회가 Decision을 무한 적재하지 않게. 학습 기록은 에이전트
        도구(target_feedback)가 의도적으로 호출할 때만."""
        from .analysis.feedback import target_feedback
        return target_feedback(db, name, persist=False,
                               sat_adu=float(cfg.get("camera.saturation_adu", 65535)))

    @app.get("/api/sysinfo")
    async def api_sysinfo():
        """런타임 자원 — 버전·업타임·디스크 여유(이미지 저장용). 1Hz status와 분리."""
        import shutil
        disk = None
        try:
            du = shutil.disk_usage(str(cfg.data_dir))
            disk = {"free_gb": round(du.free / 1e9, 1),
                    "used_pct": round((du.total - du.free) / du.total * 100)}
        except Exception:
            pass
        return {"version": __version__,
                "uptime_s": round(_time.monotonic() - app_start),
                "disk": disk}

    @app.get("/api/logs")
    async def api_logs():
        return list(events.log_buffer)

    @app.get("/api/frames")
    async def api_frames(limit: int = 30):
        return db.recent(Frame, limit)

    @app.get("/api/actionlog")
    async def api_actionlog(limit: int = 50):
        return db.recent(ActionLog, limit)

    @app.get("/api/sessions")
    async def api_sessions(limit: int = 20):
        return db.recent(ObservationSession, limit)

    @app.get("/api/weather/history")
    async def api_weather_history(limit: int = 240):
        return db.recent(WeatherRecord, limit)

    @app.post("/api/weather/ingest")
    async def api_weather_ingest(payload=Body(...)):
        """분산 수집(§7) — 원격 PC Weather Agent가 표준 기상 JSON(단일/배열)을 올린다.
        검증·매핑·재정렬(중복제거) 후 WeatherRecord에 적재. 로컬 weather/safety 흐름과 별개."""
        from .watchtower.ingest import ingest_records
        return ingest_records(db, payload)

    @app.get("/api/weather/sources")
    async def api_weather_sources():
        """분산 소스별 최신 기상 1건씩 — 어느 PC가 무엇을 올렸는지."""
        from .watchtower.ingest import latest_per_source
        return latest_per_source(db)

    # 위험 알림(무인 운영 안전 루프) — 발화는 샘플러가, 여기는 조회/확인.
    @app.get("/api/alerts")
    async def api_alerts(limit: int = 50):
        return db.recent(Alert, limit)

    @app.get("/api/alerts/active")
    async def api_alerts_active():
        """미확인(acknowledged=False) 알림 — 배지/토스트용."""
        def _q(s):
            rows = (s.query(Alert).filter(Alert.acknowledged.is_(False))
                    .order_by(Alert.id.desc()).all())
            return [row_to_dict(r) for r in rows]
        return db.query(_q)

    @app.post("/api/alerts/acknowledge")
    async def api_alerts_ack(payload=Body(default={})):
        """알림 확인 — id 주면 1건, 없으면 미확인 전체. {acknowledged: n}."""
        aid = (payload or {}).get("id")
        by = str((payload or {}).get("by") or "operator")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cnt: list[int] = []

        def _upd(s):
            q = s.query(Alert).filter(Alert.acknowledged.is_(False))
            if aid is not None:
                q = q.filter(Alert.id == aid)
            rows = q.all()
            for r in rows:
                r.acknowledged = True
                r.acked_by = by
                r.acked_utc = now
            cnt.append(len(rows))
        db.update(_upd)
        return {"acknowledged": cnt[0] if cnt else 0}

    # ---------- 액션 ----------

    @app.post("/api/actions/autoflat/start")
    async def autoflat_start(req: AutoFlatStartReq):
        params = AutoFlatParams.from_config(cfg, req.model_dump())
        await runner.start(params, extra_preconditions=[
            ("capture_idle", not capture.active(),
             "수동 캡처 실행 중 — 정지 후 시작"),
            ("orchestrator_idle", not orch.running(),
             "관측(Orchestrator) 실행 중 — 정지 후 시작"),
        ])
        return {"started": True}

    @app.post("/api/actions/autoflat/stop")
    async def autoflat_stop():
        await runner.request_stop()
        return {"stopping": True}

    @app.post("/api/actions/mount/goto")
    async def mount_goto(req: GotoReq):
        if not (0.0 <= req.alt <= 89.5):
            raise HTTPException(400, "고도는 0~89.5° 범위")
        mount = drivers["mount"]
        await bus.run("mount_goto_altaz", actor="operator",
                      params={"alt": req.alt, "az": req.az},
                      func=lambda: asyncio.to_thread(mount.goto_altaz,
                                                     req.alt, req.az % 360.0),
                      preconditions=[_solar_precond(alt=req.alt, az=req.az % 360.0)])
        return {"ok": True}

    @app.post("/api/actions/mount/goto_radec")
    async def mount_goto_radec(req: GotoRaDecReq):
        try:
            ra = ephemeris.parse_ra_hours(req.ra)
            dec = ephemeris.parse_dec_degs(req.dec)
        except Exception as exc:
            raise HTTPException(400, f"RA/Dec 해석 실패: {exc}")
        mount = drivers["mount"]
        await bus.run("mount_goto_radec", actor="operator",
                      params={"ra_hours": round(ra, 5),
                              "dec_degs": round(dec, 5),
                              "input": [req.ra, req.dec]},
                      func=lambda: asyncio.to_thread(mount.goto_radec, ra, dec),
                      preconditions=[_solar_precond(ra_hours=ra, dec_degs=dec)])
        return {"ok": True, "ra_hours": ra, "dec_degs": dec}

    @app.post("/api/actions/mount/jog")
    async def mount_jog(req: JogReq):
        d = req.direction.upper()
        if d not in ("N", "S", "E", "W"):
            raise HTTPException(400, "direction은 N/S/E/W")
        arc = max(0.5, min(3600.0, float(req.arcsec)))
        dra = arc if d == "E" else (-arc if d == "W" else 0.0)
        ddec = arc if d == "N" else (-arc if d == "S" else 0.0)
        mount = drivers["mount"]
        await bus.run("mount_jog", actor="operator",
                      params={"direction": d, "arcsec": arc},
                      func=lambda: asyncio.to_thread(mount.offset_arcsec,
                                                     dra, ddec),
                      preconditions=[_relative_slew_solar_block()])
        return {"ok": True}

    # 연속 조그(PWI4식 MoveAxis) — 버튼 유지 동안 축을 속도 슬루. 방향→축+부호는
    # 서버가 단일 소스로 잡는다(N/S=secondary, E/W=primary). rate(느림/보통/빠름)는
    # 가대 capabilities의 jog_rates(deg/s)로 해석; 없으면 보수적 기본값.
    _JOG_RATE_FALLBACK = {"slow": 0.3, "normal": 1.5, "fast": 4.0}   # deg/s

    @app.post("/api/actions/mount/move_axis")
    async def mount_move_axis(req: MoveAxisReq):
        d = req.direction.upper()
        if d not in ("N", "S", "E", "W"):
            raise HTTPException(400, "direction은 N/S/E/W")
        axis = 1 if d in ("N", "S") else 0          # secondary=Dec/Alt, primary=RA/Az
        sign = 1.0 if d in ("N", "E") else -1.0
        cm = conn.caps("mount")
        axis_ok = (cm.get("can_move_axis_secondary") if axis == 1
                   else cm.get("can_move_axis_primary"))
        if not axis_ok:
            raise HTTPException(400, "이 가대는 해당 축의 연속 조그를 지원하지 않습니다")
        rates = cm.get("jog_rates") or _JOG_RATE_FALLBACK
        rate_deg_s = float(rates.get(req.rate, rates.get("normal", 1.5))) * sign
        mount = drivers["mount"]
        await bus.run("mount_move_axis", actor="operator",
                      params={"direction": d, "rate": req.rate,
                              "deg_s": round(rate_deg_s, 4), "axis": axis},
                      func=lambda: asyncio.to_thread(mount.move_axis, axis, rate_deg_s),
                      preconditions=[_relative_slew_solar_block()])
        return {"ok": True, "axis": axis, "deg_s": round(rate_deg_s, 4)}

    @app.post("/api/actions/mount/jog_keepalive")
    async def mount_jog_keepalive():
        # 데드맨 재무장 — 고빈도라 감사로그를 남기지 않는다(bus.run 미경유).
        mount = drivers["mount"]
        ka = getattr(mount, "jog_keepalive", None)
        if callable(ka):
            await asyncio.to_thread(ka)
        return {"ok": True}

    @app.post("/api/actions/mount/jog_stop")
    async def mount_jog_stop():
        cm = conn.caps("mount")
        # 조그 능력이 없으면 멈출 것도 없다 → 즉시 ok. (미연결/팬텀 ASCOM 가대에서
        # COM 워커가 묶여 정지 호출이 블로킹되는 것을 방지 — 안전 정지 경로는 막히면 안 됨.)
        if not (cm.get("can_move_axis_primary") or cm.get("can_move_axis_secondary")):
            return {"ok": True}
        mount = drivers["mount"]
        def _stop_both():
            for ax in (0, 1):
                try:
                    mount.move_axis(ax, 0.0)
                except Exception:
                    pass
        await bus.run("mount_jog_stop", actor="operator", params={},
                      func=lambda: asyncio.to_thread(_stop_both))
        return {"ok": True}

    @app.post("/api/actions/mount/tracking")
    async def mount_tracking(req: TrackingReq):
        mount = drivers["mount"]
        await bus.run("mount_tracking", actor="operator",
                      params={"on": req.on},
                      func=lambda: asyncio.to_thread(mount.set_tracking, req.on))
        return {"ok": True}

    @app.post("/api/actions/mount/stop")
    async def mount_stop():
        mount = drivers["mount"]
        await bus.run("mount_stop", actor="operator", params={},
                      func=lambda: asyncio.to_thread(mount.stop))
        return {"ok": True}

    @app.post("/api/actions/mount/park")
    async def mount_park():
        mount = drivers["mount"]
        await bus.run("mount_park", actor="operator", params={},
                      func=lambda: asyncio.to_thread(mount.park))
        return {"ok": True}

    @app.post("/api/actions/mount/unpark")
    async def mount_unpark():
        mount = drivers["mount"]
        await bus.run("mount_unpark", actor="operator", params={},
                      func=lambda: asyncio.to_thread(mount.unpark))
        return {"ok": True}

    @app.post("/api/actions/mount/home")
    async def mount_home():
        mount = drivers["mount"]
        await bus.run("mount_find_home", actor="operator", params={},
                      func=lambda: asyncio.to_thread(mount.find_home))
        return {"ok": True}

    @app.post("/api/actions/mount/setpark")
    async def mount_setpark():
        mount = drivers["mount"]
        await bus.run("mount_set_park", actor="operator", params={},
                      func=lambda: asyncio.to_thread(mount.set_park))
        return {"ok": True}

    @app.post("/api/actions/filter")
    async def filter_set(req: FilterReq):
        fw = drivers["filterwheel"]
        status = await asyncio.to_thread(fw.status)
        if not status.connected:
            raise HTTPException(409, status.detail or "필터휠이 연결되지 않았습니다")
        if status.moving:
            raise HTTPException(409, "필터휠이 초기화/이동 중입니다")
        names = status.names
        if not 0 <= req.position < len(names):
            raise HTTPException(400, f"필터 위치 0~{len(names) - 1} 범위")
        prev = status.position   # 교체 직전 필터 — 포커스 오프셋 델타의 기준
        await bus.run("filter_set", actor="operator",
                      params={"position": req.position,
                              "filter": names[req.position]},
                      func=lambda: asyncio.to_thread(fw.set_position, req.position))
        # 필터별 포커스 오프셋 자동 보정 (best-effort — 포커서 없거나 실패해도 교체는 성공)
        applied = None
        try:
            applied = await apply_filter_focus_offset(
                cfg, drivers,
                lambda name, params, fn: bus.run(name, actor="operator",
                                                 params=params, func=fn),
                prev, req.position)
        except Exception:
            pass
        return {"ok": True, "focus_offset": applied}

    @app.post("/api/actions/camera/cooler")
    async def camera_cooler(req: CoolerReq):
        # 쿨러 램프 거버너 경유 — max_dt_c 설정 시 점진(쿨다운/웜업), 미설정 시 즉시.
        await bus.run("camera_cooler", actor="operator",
                      params={"on": req.on, "setpoint": req.setpoint},
                      func=lambda: asyncio.to_thread(cooler.request, req.on,
                                                     req.setpoint))
        return {"ok": True, "cooler": cooler.status_dict()}

    @app.post("/api/actions/camera/capture")
    async def camera_capture(req: CaptureStartReq):
        await capture.start(exposure_s=req.exposure_s,
                            frame_type=req.frame_type,
                            count=req.count, interval_s=req.interval_s,
                            binning=req.binning)
        return {"started": True}

    @app.post("/api/actions/camera/capture/stop")
    async def camera_capture_stop():
        await capture.stop()
        return {"stopping": True}

    @app.post("/api/actions/camera/autosave")
    async def camera_autosave(req: AutosaveReq):
        await bus.run("capture_autosave", actor="operator",
                      params={"on": req.on},
                      func=lambda: asyncio.to_thread(capture.set_autosave,
                                                     req.on))
        return {"autosave": capture.autosave}

    @app.post("/api/actions/focuser/move")
    async def focuser_move(req: FocuserMoveReq):
        foc = drivers["focuser"]
        st = await asyncio.to_thread(foc.status)
        if not 0 <= req.position <= st.max_position:
            raise HTTPException(400, f"포커서 위치 0~{st.max_position} 범위")
        await bus.run("focuser_move", actor="operator",
                      params={"position": req.position},
                      func=lambda: asyncio.to_thread(foc.move_to, req.position))
        return {"ok": True}

    @app.post("/api/actions/focuser/nudge")
    async def focuser_nudge(req: FocuserNudgeReq):
        foc = drivers["focuser"]
        st = await asyncio.to_thread(foc.status)
        if st.position is None:
            raise HTTPException(409, "포커서 위치를 읽을 수 없음")
        target = max(0, min(st.max_position, st.position + int(req.delta)))
        await bus.run("focuser_nudge", actor="operator",
                      params={"delta": int(req.delta), "target": target},
                      func=lambda: asyncio.to_thread(foc.move_to, target))
        return {"ok": True, "target": target}

    @app.post("/api/actions/focuser/stop")
    async def focuser_stop():
        foc = drivers["focuser"]
        if not hasattr(foc, "halt"):
            raise HTTPException(400, "이 포커서는 정지를 지원하지 않습니다")
        await bus.run("focuser_stop", actor="operator", params={},
                      func=lambda: asyncio.to_thread(foc.halt))
        return {"ok": True}

    @app.post("/api/actions/focuser/home")
    async def focuser_home():
        foc = drivers["focuser"]
        if not hasattr(foc, "home"):
            raise HTTPException(400, "이 포커서는 홈을 지원하지 않습니다")
        await bus.run("focuser_home", actor="operator", params={},
                      func=lambda: asyncio.to_thread(foc.home))
        return {"ok": True}

    # ---------- 돔 ----------

    @app.post("/api/actions/dome/shutter")
    async def dome_shutter(req: DomeShutterReq):
        dome = drivers["dome"]
        st = await asyncio.to_thread(dome.status)
        if not st.can_command_shutter:
            raise HTTPException(409, "이 돔은 셔터 수동 — SW로 개폐할 수 없습니다")
        fn = dome.open_shutter if req.open else dome.close_shutter
        await bus.run("dome_shutter", actor="operator",
                      params={"open": req.open},
                      func=lambda: asyncio.to_thread(fn))
        return {"ok": True}

    @app.post("/api/actions/dome/rotate")
    async def dome_rotate(req: DomeRotateReq):
        d = req.direction.lower()
        if d not in ("cw", "ccw", "stop"):
            raise HTTPException(400, "direction은 cw/ccw/stop")
        dome = drivers["dome"]
        await bus.run("dome_rotate", actor="operator", params={"direction": d},
                      func=lambda: asyncio.to_thread(dome.rotate, d))
        return {"ok": True}

    @app.post("/api/actions/dome/slew")
    async def dome_slew(req: DomeAzReq):
        dome = drivers["dome"]
        az = req.azimuth % 360.0
        await bus.run("dome_slew", actor="operator", params={"azimuth": round(az, 2)},
                      func=lambda: asyncio.to_thread(dome.slew_to_azimuth, az))
        return {"ok": True, "azimuth": az}

    @app.post("/api/actions/dome/sync")
    async def dome_sync(req: DomeAzReq):
        """현재 돔 방위를 이 값으로 영점화 (추측항법 기준 — '지금 남쪽=180')."""
        dome = drivers["dome"]
        az = req.azimuth % 360.0
        await bus.run("dome_sync", actor="operator", params={"azimuth": round(az, 2)},
                      func=lambda: asyncio.to_thread(dome.sync_azimuth, az))
        return {"ok": True, "azimuth": az}

    @app.post("/api/actions/dome/slave")
    async def dome_slave(req: DomeSlaveReq):
        """마운트 자동추종 on/off. 켜면 가드가 기하로 목표방위를 계산해 추종한다."""
        dome = drivers["dome"]
        await bus.run("dome_slave", actor="operator", params={"on": req.on},
                      func=lambda: asyncio.to_thread(dome.set_slaved, req.on))
        return {"ok": True, "slaved": req.on}

    @app.post("/api/actions/dome/stop")
    async def dome_stop():
        dome = drivers["dome"]
        await bus.run("dome_stop", actor="operator", params={},
                      func=lambda: asyncio.to_thread(dome.stop))
        return {"ok": True}

    # ---------- 아카이브 정합성 (§9.3) ----------

    @app.get("/api/archive/scan")
    async def archive_scan(deep: bool = False):
        """파일↔DB 정합성 스캔 — 누락/미등록/고아 + deep=true면 sha256 무결성(느림)."""
        return await asyncio.to_thread(archive.scan, deep=deep)

    # ---------- 대상 해석 / 천체력 ----------

    @app.get("/api/resolve")
    async def resolve_target(name: str):
        """CDS Sesame(SIMBAD/NED/VizieR) 이름 해석 — 인터넷 필요."""
        def _fetch() -> str:
            import httpx
            url = ("https://cds.unistra.fr/cgi-bin/nph-sesame/-ox/SNV?"
                   + urllib.parse.quote(name))
            r = httpx.get(url, timeout=8.0)
            r.raise_for_status()
            return r.text
        try:
            text = await asyncio.to_thread(_fetch)
        except Exception as exc:
            raise HTTPException(502, f"이름 해석 서비스 연결 실패: {exc}")
        m_ra = re.search(r"<jradeg>([\d.+\-eE]+)</jradeg>", text)
        m_de = re.search(r"<jdedeg>([\d.+\-eE]+)</jdedeg>", text)
        if not (m_ra and m_de):
            raise HTTPException(404, f"'{name}' 해석 실패 (SIMBAD/NED/VizieR)")
        ra_h = float(m_ra.group(1)) / 15.0
        dec = float(m_de.group(1))
        return {"name": name, "ra_hours": round(ra_h, 6),
                "dec_degs": round(dec, 6),
                "ra_str": ephemeris.fmt_ra_hours(ra_h),
                "dec_str": ephemeris.fmt_dec_degs(dec)}

    @app.get("/api/night/timeline")
    async def night_timeline():
        return ephemeris.night_timeline(
            lat, lon,
            flat_high=float(cfg.get("autoflat.flat_sun_alt_high", -1.0)),
            flat_low=float(cfg.get("autoflat.flat_sun_alt_low", -12.0)))

    @app.get("/api/night/track")
    async def night_track(ra: str, dec: str):
        try:
            ra_h = ephemeris.parse_ra_hours(ra)
            dec_d = ephemeris.parse_dec_degs(dec)
        except Exception as exc:
            raise HTTPException(400, f"RA/Dec 해석 실패: {exc}")
        tl = ephemeris.night_timeline(lat, lon)
        alts = ephemeris.target_track(ra_h, dec_d, lat, lon, tl["t"])
        return {"t": tl["t"], "alt": alts,
                "ra_hours": ra_h, "dec_degs": dec_d}

    # ---------- 텔레메트리 (시계열 플롯 빌더) ----------

    @app.get("/api/telemetry/keys")
    async def telemetry_keys():
        return sampler.telemetry_keys()

    @app.get("/api/telemetry/history")
    async def telemetry_history(keys: str, seconds: int = 900):
        key_list = [k.strip() for k in keys.split(",") if k.strip()]
        if not key_list:
            raise HTTPException(400, "keys 파라미터 필요 (쉼표 구분)")
        return sampler.telemetry_history(key_list, seconds)

    @app.post("/api/dev/mode")
    async def dev_mode(req: DevModeReq):
        if req.mode not in ("sim", "real"):
            raise HTTPException(400, "mode는 sim 또는 real")
        await bus.run("dev_set_mode", actor="developer",
                      params={"mode": req.mode},
                      func=lambda: conn.set_mode(req.mode),
                      preconditions=conn_preconditions())
        return {"mode": drivers["mode"]}

    # ---------- 시스템: 장비 연결 관리 (SYSTEM 탭) ----------

    def _device_or_404(device: str) -> None:
        if device not in REGISTRY:
            raise HTTPException(404, f"알 수 없는 장비: {device}")

    @app.get("/api/system/devices")
    async def system_devices():
        """장비별 백엔드/설정/역량. 실시간 연결상태·장비명은 /api/status에서."""
        return conn.describe()

    @app.get("/api/system/ascom")
    async def system_ascom(device: str):
        """이 장비 타입으로 등록된 ASCOM ProgID 목록 (드롭다운용)."""
        _device_or_404(device)
        return {"device": device,
                "drivers": await asyncio.to_thread(conn.list_ascom, device)}

    @app.post("/api/system/configure")
    async def system_configure(req: DeviceConfigReq):
        _device_or_404(req.device)
        try:
            conn.configure(req.device, progid=req.progid, url=req.url,
                           backend=req.backend)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        events.log("system", f"{REGISTRY[req.device].label} 설정 저장")
        return conn.describe()

    @app.post("/api/system/setup-config")
    async def system_setup_config(req: SetupConfigReq):
        """per-device Setup(필터표·게인·Max ΔT 등) 저장 → config.local.json setup.{key}.*"""
        _device_or_404(req.device)
        try:
            conn.set_setup(req.device, req.setup or {})
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        events.log("system", f"{REGISTRY[req.device].label} Setup 저장")
        return conn.describe()

    @app.post("/api/system/setup")
    async def system_setup(req: DeviceReq):
        """ASCOM 드라이버 설정창(SetupDialog) — 포트 등 (NINA의 Properties 격)."""
        _device_or_404(req.device)
        try:
            await asyncio.to_thread(conn.setup_dialog, req.device)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(500, f"설정창 실패: {exc}")
        return {"ok": True}

    @app.post("/api/system/connect")
    async def system_connect(req: DeviceReq):
        _device_or_404(req.device)
        await bus.run("device_connect", actor="operator",
                      params={"device": req.device},
                      func=lambda: conn.connect(req.device, propagate=True),
                      preconditions=conn_preconditions())
        return conn.describe()

    @app.post("/api/system/disconnect")
    async def system_disconnect(req: DeviceReq):
        _device_or_404(req.device)
        await bus.run("device_disconnect", actor="operator",
                      params={"device": req.device},
                      func=lambda: conn.disconnect(req.device),
                      preconditions=conn_preconditions())
        return conn.describe()

    @app.post("/api/system/reconnect")
    async def system_reconnect(req: DeviceReq):
        _device_or_404(req.device)
        await bus.run("device_reconnect", actor="operator",
                      params={"device": req.device},
                      func=lambda: conn.reconnect(req.device, propagate=True),
                      preconditions=conn_preconditions())
        return conn.describe()

    @app.post("/api/system/connect-all")
    async def system_connect_all():
        await bus.run("device_connect_all", actor="operator", params={},
                      func=lambda: conn.connect_all(),
                      preconditions=conn_preconditions())
        return conn.describe()

    @app.post("/api/system/disconnect-all")
    async def system_disconnect_all():
        await bus.run("device_disconnect_all", actor="operator", params={},
                      func=lambda: conn.disconnect_all(),
                      preconditions=conn_preconditions())
        return conn.describe()

    @app.get("/api/sim/retention")
    async def sim_retention_status():
        """시뮬레이터 보존 정리 상태 — 활성 여부·보존일수·격리 저장소·마지막 스윕 결과."""
        if retention is None:
            return {"enabled": False,
                    "reason": "sim 모드가 아니거나 sim_retention.enabled=false"}
        return {"enabled": True, "days": retention.days,
                "frames_dir": str(retention.frames_dir),
                "last_result": retention.last_result}

    @app.post("/api/sim/retention/sweep")
    async def sim_retention_sweep():
        """지금 즉시 보존 정리 1회 실행 — 결과(삭제 행/파일 수) 반환."""
        if retention is None:
            raise HTTPException(409, "보존 정리 비활성 (sim 모드가 아님)")
        return await asyncio.to_thread(retention.prune)

    @app.post("/api/sim/twilight")
    async def sim_twilight(req: TwilightReq):
        if not getattr(drivers["camera"], "is_sim", False):
            raise HTTPException(400, "황혼 시뮬은 시뮬 카메라에서만 사용 가능")
        await bus.run("sim_twilight", actor="operator",
                      params={"enabled": req.enabled},
                      func=lambda: asyncio.to_thread(twilight.set, req.enabled))
        return {"enabled": twilight.enabled}

    # ---------- WebSocket ----------

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        events.register(ws)
        try:
            while True:
                await ws.receive_text()  # ping 등은 무시
        except WebSocketDisconnect:
            pass
        finally:
            events.unregister(ws)

    # Operation 계층 라우트 (/api/meridian/* 계획, /api/orchestrator/* 실행 제어).
    app.include_router(build_operation_router(meridian, orch, night_runner))
    # Analysis 계층 라우트 (/api/sentinel/* 품질, /api/analysis/frames/* 픽셀,
    # /api/calibration/* 마스터, /api/forge/* 실시간 보정 토글).
    app.include_router(build_analysis_router(sentinel, framedata, calibration, forge))
    # AI 에이전트 라우트 (/api/agent/chat·status·models·model) — 대시보드 채팅 위젯이 호출.
    app.include_router(build_agent_router(agent, cfg))
    # 인증 라우트 (/login·/logout·/api/session/me) — 원격 접속 게이트(Phase A).
    # 로그인 실패 임계 초과는 보안 Alert로 발화(Phase C2, 브루트포스 탐지).
    app.include_router(build_auth_router(
        access_policy,
        on_security_event=lambda title, detail: alert_mgr.fire(
            "login_failures", "warn", title, detail, cooldown_s=300.0)))

    return app
