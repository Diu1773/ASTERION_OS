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
from ..drivers import REGISTRY
from ..drivers.base import (
    CameraStatus, FilterStatus, FocuserStatus, MountStatus, WeatherStatus,
)
from ..drivers.sim import TwilightSim

KST = timezone(timedelta(hours=9))

TELEMETRY_MAXLEN = 3600  # 1 Hz × 1시간
STATUS_TIMEOUT_S = 3.0   # 장비 status()가 이 시간 안에 안 오면 응답없음 처리

# 응답 없는 장비용 오프라인 상태 (멈춘 COM 호출이 대시보드를 얼리지 않게)
_OFFLINE = {
    "mount": lambda: MountStatus(connected=False, detail="응답 없음 (시간 초과)"),
    "camera": lambda: CameraStatus(connected=False, detail="응답 없음 (시간 초과)"),
    "filterwheel": lambda: FilterStatus(connected=False),
    "focuser": lambda: FocuserStatus(connected=False, detail="응답 없음 (시간 초과)"),
    "weather": lambda: WeatherStatus(connected=False, detail="응답 없음 (시간 초과)"),
}


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
        self._stuck: dict[str, Any] = {}   # key → 응답없어 폴링 보류 중인 드라이버 인스턴스
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
        # 레지스트리 순회 — 각 장비의 status()/read()를 호출하고, Status가
        # 스스로 서술하는 snapshot()/telemetry()를 모은다. 새 장비를 REGISTRY에
        # 등록만 하면 대시보드 상태·1Hz 시계열에 자동 등장한다 (코드 변경 0).
        statuses: dict[str, Any] = {}
        device_snaps: dict[str, dict] = {}
        flat_telemetry: dict[str, Any] = {}
        for key, spec in REGISTRY.items():
            drv = self.drivers[key]
            if self._stuck.get(key) is drv:
                st = _OFFLINE[key]()        # 멈춘 인스턴스 → 폴링 스킵 (재연결 전까지)
            else:
                try:
                    st = await asyncio.wait_for(
                        asyncio.to_thread(getattr(drv, spec.status_attr)),
                        timeout=STATUS_TIMEOUT_S)
                    self._stuck.pop(key, None)
                except asyncio.TimeoutError:
                    # 죽은 하드웨어의 COM 호출이 멈춤 → 그 장비만 보류, 대시보드는 계속.
                    # 재연결로 새 인스턴스가 들어오면 자동 재개된다.
                    self._stuck[key] = drv
                    self.events.log("status", f"{spec.label} 응답 없음 — 폴링 보류 "
                                    "(재연결하면 재개)", "warn")
                    st = _OFFLINE[key]()
                except Exception:
                    st = _OFFLINE[key]()
            statuses[key] = st
            device_snaps[spec.snap_key] = st.snapshot()
            flat_telemetry.update(st.telemetry())

        mount, camera, weather = (statuses["mount"], statuses["camera"],
                                  statuses["weather"])

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

        # 장비 텔레메트리 + 샘플러 레벨 수치 (1 Hz 링버퍼)
        flat_telemetry.update({
            "sun.alt": round(sun_alt, 2),
            "skyflat.last_adu": autoflat.get("last_adu"),
            "capture.last_median": capture.get("last_median"),
        })
        self.telemetry.append((time.time(), flat_telemetry))

        snapshot = {
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
            "safety": saf,
            "autoflat": autoflat,
            "capture": capture,
            "telemetry_last": flat_telemetry,
            "defaults": {
                "autoflat": self.cfg.get("autoflat", {}) or {},
                "capture": self.cfg.get("capture", {}) or {},
            },
        }
        snapshot.update(device_snaps)  # mount/camera/filter/focuser/weather
        return snapshot
