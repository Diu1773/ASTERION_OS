"""상태 샘플러 — 1 Hz로 세계 상태를 모아 스냅샷을 유지·브로드캐스트.

스냅샷은 대시보드(WebSocket), REST(/api/status), 액션 감사로그가
공유하는 단일 진실이다. 기상은 30초마다 WeatherRecord로 적재된다.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from . import safety
from ..config import Config
from ..core import ephemeris
from ..core.events import EventHub
from ..core.ontology import Db, WeatherRecord
from ..drivers.sim import TwilightSim

KST = timezone(timedelta(hours=9))


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
                self.events.log("status", f"샘플러 오류:\n{traceback.format_exc()}",
                                "error")
            await asyncio.sleep(1.0)

    async def _sample(self) -> dict[str, Any]:
        mount = await asyncio.to_thread(self.drivers["mount"].status)
        camera = await asyncio.to_thread(self.drivers["camera"].status)
        filt = await asyncio.to_thread(self.drivers["filterwheel"].status)
        weather = await asyncio.to_thread(self.drivers["weather"].read)

        now = datetime.now(timezone.utc)
        sun_alt, sun_az = ephemeris.sun_altaz(now, self._lat, self._lon)
        lst = ephemeris.lst_hours(now, self._lon)
        phase_code, phase_label = ephemeris.twilight_phase(sun_alt)

        autoflat = self.autoflat_status()
        saf = safety.evaluate(
            mount_connected=mount.connected,
            camera_connected=camera.connected,
            weather={"rain": weather.rain, "wind": weather.wind_ms,
                     "humidity": weather.humidity, "cloud": weather.cloud_score},
            sun_alt=sun_alt,
            session_running=bool(autoflat.get("running")),
        )

        return {
            "mode": self.drivers.get("mode", "sim"),
            "site": str(self.cfg.get("site.name", "")),
            "time": {
                "utc": now.strftime("%Y-%m-%d %H:%M:%S"),
                "kst": now.astimezone(KST).strftime("%H:%M:%S"),
                "kst_date": now.astimezone(KST).strftime("%Y-%m-%d"),
                "lst": ephemeris.fmt_ra_hours(lst)[:8],
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
                "alt": None if mount.alt_degs is None else round(mount.alt_degs, 3),
                "az": None if mount.az_degs is None else round(mount.az_degs, 3),
                "ra_str": ephemeris.fmt_ra_hours(mount.ra_hours),
                "dec_str": ephemeris.fmt_dec_degs(mount.dec_degs),
                "slewing": mount.slewing, "tracking": mount.tracking,
                "detail": mount.detail,
            },
            "camera": {
                "connected": camera.connected,
                "ccd_temp": camera.ccd_temp_c,
                "cooler_on": camera.cooler_on,
                "state": camera.state, "detail": camera.detail,
            },
            "filter": {
                "connected": filt.connected, "position": filt.position,
                "name": filt.name, "names": filt.names,
            },
            "weather": {
                "temp": weather.temp_c, "humidity": weather.humidity,
                "dew_point": weather.dew_point_c, "wind": weather.wind_ms,
                "wind_dir": weather.wind_dir_deg, "cloud": weather.cloud_score,
                "rain": weather.rain,
            },
            "safety": saf,
            "autoflat": autoflat,
            "defaults": {"autoflat": self.cfg.get("autoflat", {}) or {}},
        }
