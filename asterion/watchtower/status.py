"""상태 샘플러 — 1 Hz 스냅샷 + 텔레메트리 링버퍼.

스냅샷은 대시보드(WebSocket), REST(/api/status), 액션 감사로그가
공유하는 단일 진실이다. 기상은 30초마다 WeatherRecord로 적재되고,
수치 텔레메트리는 1 Hz로 최근 1시간 링버퍼에 쌓여 시계열 플롯
빌더(/api/telemetry/*)가 읽는다.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from . import safety
from ..config import Config
from ..core import ephemeris
from ..core.events import EventHub
from ..core.ontology import Db, WeatherRecord
from ..drivers.sim import TwilightSim

KST = timezone(timedelta(hours=9))

TELEMETRY_MAXLEN = 3600  # 1 Hz × 1시간


class StatusSampler:
    def __init__(self, cfg: Config, drivers: dict[str, Any],
                 twilight: TwilightSim, events: EventHub, db: Db):
        self.cfg = cfg
        self.drivers = drivers
        self.twilight = twilight
        self.events = events
        self.db = db
        self.snapshot: dict[str, Any] = {}
        self.autoflat_status = lambda: {"running": False, "phase": "idle"}
        self.capture_status = lambda: {"active": False, "state": "idle"}
        self.telemetry: deque[tuple[float, dict]] = deque(maxlen=TELEMETRY_MAXLEN)
        self._task: asyncio.Task | None = None
        self._last_weather_db = 0.0
        self._lat = float(cfg.get("site.latitude", 36.6))
        self._lon = float(cfg.get("site.longitude", 127.5))

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="status-sampler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---------- 텔레메트리 ----------

    def telemetry_keys(self) -> list[str]:
        if not self.telemetry:
            return []
        return sorted(self.telemetry[-1][1].keys())

    def telemetry_history(self, keys: list[str], seconds: int = 900) -> dict:
        cutoff = time.time() - max(10, seconds)
        t: list[float] = []
        series: dict[str, list] = {k: [] for k in keys}
        for ts, flat in self.telemetry:
            if ts < cutoff:
                continue
            t.append(round(ts, 1))
            for k in keys:
                series[k].append(flat.get(k))
        return {"t": t, "series": series}

    # ---------- 루프 ----------

    async def _loop(self) -> None:
        while True:
            try:
                snap = await self._sample()
                self.snapshot = snap
                self.events.status(snap)
                now = time.time()
                if now - self._last_weather_db >= 30.0:
                    self._last_weather_db = now
                    w = snap["weather"]
                    self.db.add(WeatherRecord(
                        temp_c=w["temp"], humidity=w["humidity"],
                        dew_point_c=w["dew_point"], wind_ms=w["wind"],
                        wind_dir_deg=w["wind_dir"], cloud_score=w["cloud"],
                        rain=w["rain"],
                        safe=snap["safety"]["state"] in
                             (safety.OPEN_ALLOWED, safety.OBSERVING,
                              safety.READY_CHECK),
                    ))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.events.log("status",
                                f"샘플러 오류:\n{traceback.format_exc()}",
                                "error")
            await asyncio.sleep(1.0)

    async def _sample(self) -> dict[str, Any]:
        mount = await asyncio.to_thread(self.drivers["mount"].status)
        camera = await asyncio.to_thread(self.drivers["camera"].status)
        filt = await asyncio.to_thread(self.drivers["filterwheel"].status)
        focuser = await asyncio.to_thread(self.drivers["focuser"].status)
        weather = await asyncio.to_thread(self.drivers["weather"].read)

        now = datetime.now(timezone.utc)
        sun_alt, sun_az = ephemeris.sun_altaz(now, self._lat, self._lon)
        lst = ephemeris.lst_hours(now, self._lon)
        phase_code, phase_label = ephemeris.twilight_phase(sun_alt)

        autoflat = self.autoflat_status()
        capture = self.capture_status()
        session_running = bool(autoflat.get("running")) or bool(
            capture.get("active"))
        saf = safety.evaluate(
            mount_connected=mount.connected,
            camera_connected=camera.connected,
            weather={"rain": weather.rain, "wind": weather.wind_ms,
                     "humidity": weather.humidity,
                     "cloud": weather.cloud_score},
            sun_alt=sun_alt,
            session_running=session_running,
        )

        # 시계열 플롯 빌더용 수치 텔레메트리 (1 Hz 링버퍼)
        flat_telemetry = {
            "mount.alt": mount.alt_degs,
            "mount.az": mount.az_degs,
            "camera.ccd_temp": camera.ccd_temp_c,
            "focuser.position": focuser.position,
            "focuser.temp": focuser.temperature,
            "weather.temp": weather.temp_c,
            "weather.humidity": weather.humidity,
            "weather.dew_point": weather.dew_point_c,
            "weather.wind": weather.wind_ms,
            "weather.cloud": weather.cloud_score,
            "sun.alt": round(sun_alt, 2),
            "skyflat.last_adu": autoflat.get("last_adu"),
            "capture.last_median": capture.get("last_median"),
        }
        self.telemetry.append((time.time(), flat_telemetry))

        return {
            "mode": self.drivers.get("mode", "sim"),
            "site": str(self.cfg.get("site.name", "")),
            "geo": {"lat": self._lat, "lon": self._lon},
            "time": {
                "utc": now.strftime("%Y-%m-%d %H:%M:%S"),
                "kst": now.astimezone(KST).strftime("%H:%M:%S"),
                "kst_date": now.astimezone(KST).strftime("%Y-%m-%d"),
                "lst": ephemeris.fmt_ra_hours(lst)[:8],
                "lst_hours": round(lst, 5),
            },
            "sun": {
                "alt": round(sun_alt, 2), "az": round(sun_az, 1),
                "phase": phase_code, "phase_label": phase_label,
                "antisolar_az": round((sun_az + 180.0) % 360.0, 1),
            },
            "twilight_sim": {
                "enabled": self.twilight.enabled,
                "factor": round(self.twilight.sky_factor(sun_alt), 5),
            },
            "mount": {
                "connected": mount.connected,
                "name": mount.device_name,
                "alt": None if mount.alt_degs is None else round(mount.alt_degs, 3),
                "az": None if mount.az_degs is None else round(mount.az_degs, 3),
                "ra_hours": mount.ra_hours,
                "dec_degs": mount.dec_degs,
                "ra_str": ephemeris.fmt_ra_hours(mount.ra_hours),
                "dec_str": ephemeris.fmt_dec_degs(mount.dec_degs),
                "slewing": mount.slewing, "tracking": mount.tracking,
                "detail": mount.detail,
            },
            "camera": {
                "connected": camera.connected,
                "name": camera.device_name,
                "ccd_temp": camera.ccd_temp_c,
                "cooler_on": camera.cooler_on,
                "state": camera.state, "detail": camera.detail,
            },
            "filter": {
                "connected": filt.connected, "position": filt.position,
                "name": filt.name, "names": filt.names,
                "device_name": filt.device_name,
            },
            "focuser": {
                "connected": focuser.connected,
                "name": focuser.device_name,
                "position": focuser.position,
                "moving": focuser.moving,
                "temperature": focuser.temperature,
                "max_position": focuser.max_position,
                "detail": focuser.detail,
            },
            "weather": {
                "connected": weather.connected,
                "name": weather.device_name,
                "temp": weather.temp_c, "humidity": weather.humidity,
                "dew_point": weather.dew_point_c, "wind": weather.wind_ms,
                "wind_dir": weather.wind_dir_deg,
                "cloud": weather.cloud_score, "rain": weather.rain,
            },
            "safety": saf,
            "autoflat": autoflat,
            "capture": capture,
            "telemetry_last": flat_telemetry,
            "defaults": {
                "autoflat": self.cfg.get("autoflat", {}) or {},
                "capture": self.cfg.get("capture", {}) or {},
            },
        }
