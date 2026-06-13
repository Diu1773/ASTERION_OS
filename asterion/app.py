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
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .camera.capture import CaptureService
from .config import Config
from .core import ephemeris
from .core.actions import ActionBus, ActionError
from .core.events import EventHub
from .core.ontology import ActionLog, Db, Frame, ObservationSession, WeatherRecord
from .core.preview import stretch_to_png
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
    setpoint: float | None = None


class TwilightReq(BaseModel):
    enabled: bool


class DevModeReq(BaseModel):
    mode: str  # "sim" | "real"


class GotoRaDecReq(BaseModel):
    ra: str   # "5.59" | "05:35:17" | "5h35m17s"
    dec: str  # "-5.39" | "-05:23:28"


class JogReq(BaseModel):
    direction: str       # N | S | E | W
    arcsec: float = 10.0


class FocuserMoveReq(BaseModel):
    position: int


class FocuserNudgeReq(BaseModel):
    delta: int


class CaptureStartReq(BaseModel):
    exposure_s: float = 2.0
    frame_type: str = "LIGHT"   # LIGHT | FLAT | DARK | BIAS
    count: int = 1              # 0 = 무한 (정지 버튼까지)
    interval_s: float = 1.0


class AutosaveReq(BaseModel):
    on: bool


def create_app() -> FastAPI:
    cfg = Config.load(os.environ.get("ASTERION_CONFIG"))
    lat = float(cfg.get("site.latitude", 36.6))
    lon = float(cfg.get("site.longitude", 127.5))

    def sun_alt_now() -> float:
        return ephemeris.sun_altaz(ephemeris.now_utc(), lat, lon)[0]

    def lst_now() -> float:
        return ephemeris.lst_hours(ephemeris.now_utc(), lon)

    twilight = TwilightSim(efold_s=float(cfg.get("sim.twilight_efold_s", 240.0)))
    drivers = build_drivers(cfg, twilight, sun_alt_now, lst_now)
    events = EventHub()
    db = Db(cfg.data_dir / "asterion.db")
    sampler = StatusSampler(cfg, drivers, twilight, events, db)
    bus = ActionBus(db, events, lambda: sampler.snapshot)
    # 프레임 미리보기 홀더 (캡처/플랫 직후 스트레치 PNG)
    frame_preview = {"png": b"", "token": 0, "meta": {}}

    async def publish_preview(img, meta):
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
                            preview_cb=publish_preview)
    sampler.autoflat_status = runner.status_dict
    capture = CaptureService(
        cfg, drivers, bus, db, events, cfg.data_dir / "frames",
        blocked_fn=lambda: ("오토플랫 실행 중 — 카메라 사용 불가"
                            if runner.running() else None),
        preview_cb=publish_preview)
    sampler.capture_status = capture.status_dict

    DEVICES = ("mount", "camera", "filterwheel", "focuser", "weather")

    async def rebuild_drivers(target_mode: str) -> None:
        """런타임 SIM↔REAL 전환. 새 드라이버를 먼저 만들고(실패 시 기존 유지),
        연결 후 원자적으로 교체한다. real인데 ASCOM ProgID 미설정 등은 여기서
        예외가 나며 전환이 취소된다(기존 드라이버 그대로)."""
        new = build_drivers(cfg, twilight, sun_alt_now, lst_now, mode=target_mode)
        for name in DEVICES:
            try:
                await asyncio.to_thread(new[name].connect)
            except Exception as exc:
                events.log("system", f"{name} 연결 경고({target_mode}): {exc}", "warn")
        old = {name: drivers[name] for name in DEVICES}
        drivers.clear()
        drivers.update(new)  # 같은 dict 객체를 in-place 교체 → sampler/runner 즉시 반영
        for drv in old.values():
            try:
                drv.close()
            except Exception:
                pass
        events.log("system", f"드라이버 모드 전환 → {drivers['mode'].upper()}")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        events.attach_loop(asyncio.get_running_loop())
        for name in DEVICES:
            drv = drivers[name]
            try:
                await asyncio.to_thread(drv.connect)
                events.log("system", f"{name} 드라이버 연결 "
                           f"({'SIM' if getattr(drv, 'is_sim', False) else type(drv).__name__})")
            except Exception as exc:
                events.log("system", f"{name} 연결 실패: {exc}", "error")
        sampler.start()
        events.log("system",
                   f"Asterion v{__version__} 가동 — 모드 {drivers['mode'].upper()}")
        yield
        await sampler.stop()
        for name in DEVICES:
            try:
                drivers[name].close()
            except Exception:
                pass

    app = FastAPI(title="Asterion", version=__version__, lifespan=lifespan)

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
        await runner.start(params, extra_preconditions=[
            ("capture_idle", not capture.active(),
             "수동 캡처 실행 중 — 정지 후 시작"),
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
                                                     req.alt, req.az % 360.0))
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
                      func=lambda: asyncio.to_thread(mount.goto_radec, ra, dec))
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
                                                     dra, ddec))
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
                      params={"on": req.on, "setpoint": req.setpoint},
                      func=lambda: asyncio.to_thread(cam.set_cooler, req.on,
                                                     req.setpoint))
        return {"ok": True}

    @app.post("/api/actions/camera/capture")
    async def camera_capture(req: CaptureStartReq):
        await capture.start(exposure_s=req.exposure_s,
                            frame_type=req.frame_type,
                            count=req.count, interval_s=req.interval_s)
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
                      func=lambda: rebuild_drivers(req.mode),
                      preconditions=[
                          ("autoflat_idle", not runner.running(),
                           "오토플랫 실행 중에는 모드 전환 불가"),
                          ("capture_idle", not capture.active(),
                           "캡처 실행 중에는 모드 전환 불가"),
                      ])
        return {"mode": drivers["mode"]}

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
