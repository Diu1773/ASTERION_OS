"""Earendel — 통합 관측 플랫폼 (FastAPI: REST + WebSocket + 대시보드).

플랫폼 본체. core(데이터·액션 백본)와 drivers(하드웨어 브리지) 위에
named system들(watchtower 환경·안전, skyflat 오토플랫)을 조립해 단일
웹으로 노출한다.
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .config import Config
from .core import ephemeris
from .core.actions import ActionBus, ActionError
from .core.events import EventHub
from .core.ontology import ActionLog, Db, Frame, ObservationSession, WeatherRecord
from .drivers import build_drivers
from .drivers.sim import TwilightSim
from .skyflat.autoflat import AutoFlatParams, AutoFlatRunner
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


class TwilightReq(BaseModel):
    enabled: bool


def create_app() -> FastAPI:
    cfg = Config.load(os.environ.get("EARENDEL_CONFIG"))
    lat = float(cfg.get("site.latitude", 36.6))
    lon = float(cfg.get("site.longitude", 127.5))

    def sun_alt_now() -> float:
        return ephemeris.sun_altaz(ephemeris.now_utc(), lat, lon)[0]

    def lst_now() -> float:
        return ephemeris.lst_hours(ephemeris.now_utc(), lon)

    twilight = TwilightSim(efold_s=float(cfg.get("sim.twilight_efold_s", 240.0)))
    drivers = build_drivers(cfg, twilight, sun_alt_now, lst_now)
    events = EventHub()
    db = Db(cfg.data_dir / "earendel.db")
    sampler = StatusSampler(cfg, drivers, twilight, events, db)
    bus = ActionBus(db, events, lambda: sampler.snapshot)
    runner = AutoFlatRunner(cfg, drivers, bus, db, events, twilight,
                            sun_alt_now, cfg.data_dir / "frames")
    sampler.autoflat_status = runner.status_dict

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        events.attach_loop(asyncio.get_running_loop())
        for name in ("mount", "camera", "filterwheel", "weather"):
            drv = drivers[name]
            try:
                await asyncio.to_thread(drv.connect)
                events.log("system", f"{name} 드라이버 연결 "
                           f"({'SIM' if getattr(drv, 'is_sim', False) else type(drv).__name__})")
            except Exception as exc:
                events.log("system", f"{name} 연결 실패: {exc}", "error")
        sampler.start()
        events.log("system",
                   f"Earendel v{__version__} 가동 — 모드 {drivers['mode'].upper()}")
        yield
        await sampler.stop()
        for name in ("mount", "camera", "filterwheel", "weather"):
            try:
                drivers[name].close()
            except Exception:
                pass

    app = FastAPI(title="Earendel", version=__version__, lifespan=lifespan)

    @app.exception_handler(ActionError)
    async def _action_error(_, exc: ActionError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    # ---------- 페이지/정적 ----------

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    # ---------- 조회 ----------

    @app.get("/api/status")
    async def api_status():
        return sampler.snapshot or {"mode": "starting"}

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

    # ---------- 액션 ----------

    @app.post("/api/actions/autoflat/start")
    async def autoflat_start(req: AutoFlatStartReq):
        params = AutoFlatParams.from_config(cfg, req.model_dump())
        await runner.start(params)
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
                                                     req.alt, req.az % 360.0))
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

    @app.post("/api/actions/filter")
    async def filter_set(req: FilterReq):
        fw = drivers["filterwheel"]
        names = (await asyncio.to_thread(fw.status)).names
        if not 0 <= req.position < len(names):
            raise HTTPException(400, f"필터 위치 0~{len(names) - 1} 범위")
        await bus.run("filter_set", actor="operator",
                      params={"position": req.position,
                              "filter": names[req.position]},
                      func=lambda: asyncio.to_thread(fw.set_position, req.position))
        return {"ok": True}

    @app.post("/api/actions/camera/cooler")
    async def camera_cooler(req: CoolerReq):
        cam = drivers["camera"]
        await bus.run("camera_cooler", actor="operator",
                      params={"on": req.on},
                      func=lambda: asyncio.to_thread(cam.set_cooler, req.on))
        return {"ok": True}

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

    return app
