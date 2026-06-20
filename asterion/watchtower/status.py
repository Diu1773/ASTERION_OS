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
from ..core.ontology import Db, TelemetrySample, WeatherRecord
from ..drivers import REGISTRY
from ..drivers.sim import TwilightSim

KST = timezone(timedelta(hours=9))

TELEMETRY_MAXLEN = 3600  # 1 Hz × 1시간
STATUS_TIMEOUT_S = 3.0   # 장비 status()가 이 시간 안에 안 오면 응답없음 처리
STUCK_RETRY_S = 4.0      # 멈춘 장비를 백그라운드로 다시 떠보는 최소 간격

# 응답 없는 장비의 오프라인 상태는 각 DeviceSpec.offline_factory가 제공한다
# (멈춘 COM 호출이 대시보드를 얼리지 않게). 키 하드코딩 제거 — 새 장비는 REGISTRY만.


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
        self.orchestrator_status = lambda: {"running": False, "phase": "idle"}
        self.night_runner_status = lambda: {"active": False, "phase": "idle"}
        self.forge_status = lambda: {"enabled": False}
        self.cooler_status = lambda: {"mode": "idle", "ramping": False}
        # 쿨러 램프 틱(주입형) — 매 스냅샷마다 호출(safety_actuator와 동형). max ΔT 거버너.
        self.cooler_tick: Any = None
        # 안전 액추에이터(주입형) — 매 스냅샷마다 호출. 샘플러는 '판정'만 하고
        # 실제 '행동'(EMERGENCY_CLOSE→돔 닫기/경보, 돔 슬레이빙)은 여기 위임.
        self.safety_actuator: Any = None
        self.telemetry: deque[tuple[float, dict]] = deque(maxlen=TELEMETRY_MAXLEN)
        self._task: asyncio.Task | None = None
        self._stuck: dict[str, Any] = {}   # key → 응답없어 폴링 보류 중인 드라이버 인스턴스
        # 멈춘 인스턴스를 재연결 없이 회복시키기 위한 논블로킹 재탐색 상태.
        self._recover: dict[str, asyncio.Task] = {}   # key → 진행 중 회복 probe (1개만)
        self._recover_next: dict[str, float] = {}     # key → 다음 probe 가능 시각
        self._last_weather_db = 0.0
        # 마지막으로 연결+유효값 기상을 받은 시각 (monotonic) — fail-closed stale 판정용
        self._last_weather_ok: float | None = None
        # 원격 ingestion 폴백(분산 §7) — 로컬 기상 장치 없을 때 호출. 신선 dict(+age_s) 또는 None.
        self.weather_ingest_fn = None
        self._wx_warn_s = float(cfg.get("safety.weather_warn_seconds",
                                        safety.WEATHER_WARN_AGE_S))
        self._wx_unsafe_s = float(cfg.get("safety.weather_unsafe_seconds",
                                          safety.WEATHER_UNSAFE_AGE_S))
        self._lat = float(cfg.get("site.latitude", 36.6))
        self._lon = float(cfg.get("site.longitude", 127.5))
        # Sky Panel용 달·행성 (astropy, 느림) — 30초 캐시. 천체는 분당 ~0.13°만 움직임.
        self._sky_bodies: list = []
        self._sky_next = 0.0   # monotonic — 다음 재계산 시각
        self._sky_failed = False   # 실패 경고 1회만(전환 시) — 30초마다 스팸 방지
        # 1분 다운샘플 텔레메트리 영속 (채널 → [min, sum, max, count]) + 보존기간.
        # 1Hz 라이브는 위 self.telemetry 링, 이건 재시작 후에도 남는 추세를 DB에.
        self._telem_accum: dict[str, list[float]] = {}
        self._last_telem_flush = 0.0
        self._telem_retention_days = float(cfg.get("db.telemetry_retention_days", 30.0))

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
                if self.safety_actuator is not None:
                    try:
                        await self.safety_actuator(snap)
                    except Exception:
                        self.events.log("status", "안전 액추에이터 오류:\n"
                                        f"{traceback.format_exc()}", "error")
                if self.cooler_tick is not None:
                    try:
                        await self.cooler_tick(snap)
                    except Exception:
                        self.events.log("status", "쿨러 램프 틱 오류:\n"
                                        f"{traceback.format_exc()}", "error")
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
                self._accumulate_telemetry(snap.get("telemetry_last"))
                self._flush_telemetry(now)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.events.log("status",
                                f"샘플러 오류:\n{traceback.format_exc()}",
                                "error")
            await asyncio.sleep(1.0)

    # ---------- 텔레메트리 다운샘플 영속 ----------

    def _accumulate_telemetry(self, flat: dict | None) -> None:
        """수치 채널을 1분 버킷에 누적 (채널 → [min, sum, max, count]). bool/None 제외."""
        for k, v in (flat or {}).items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            a = self._telem_accum.get(k)
            if a is None:
                self._telem_accum[k] = [v, v, v, 1]
            else:
                a[0] = min(a[0], v); a[1] += v; a[2] = max(a[2], v); a[3] += 1

    def _flush_telemetry(self, now: float) -> None:
        """60초마다 누적 버킷을 다운샘플 행으로 DB에 적재 + 보존기간 prune."""
        if now - self._last_telem_flush < 60.0 or not self._telem_accum:
            return
        self._last_telem_flush = now
        samples = [TelemetrySample(channel=k, vmin=mn, vmean=sm / c, vmax=mx,
                                   n=int(c))
                   for k, (mn, sm, mx, c) in self._telem_accum.items()]
        self._telem_accum = {}
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(days=self._telem_retention_days)
                  ).isoformat(timespec="milliseconds")
        self.db.add_telemetry(samples, prune_before_utc=cutoff)

    def _clear_recover(self, key: str) -> None:
        """진행 중인 회복 probe와 보류 상태를 폐기 (재연결로 인스턴스가 교체될 때).
        남은 task는 결과를 회수해 'never retrieved' 경고가 안 뜨게 한다."""
        self._stuck.pop(key, None)
        self._recover_next.pop(key, None)
        task = self._recover.pop(key, None)
        if task is not None:
            task.add_done_callback(lambda t: t.cancelled() or t.exception())

    def _recover_stuck(self, key: str, spec: Any, drv: Any, now: float):
        """멈춘 인스턴스를 논블로킹으로 다시 떠본다. 호밍/캘리브레이션처럼 '잠깐'
        멈췄던 장비가 응답을 회복하면 stuck을 풀고 그 status를 돌려준다 (재연결 불필요).
        매달린 COM 호출이 워커 스레드를 잡으므로 probe는 드라이버당 항상 1개만 띄운다.
        아직 회복 전이면 None (→ 호출부가 OFFLINE 보고)."""
        task = self._recover.get(key)
        if task is not None:
            if not task.done():
                return None             # 직전 probe가 아직 안 끝남(여전히 멈춤) → 추가 안 띄움
            self._recover.pop(key, None)
            try:
                st = task.result()
            except Exception:
                st = None
            if st is not None:          # COM이 응답함 → 회복 (끊김으로 와도 폴링은 재개)
                self._stuck.pop(key, None)
                self._recover_next.pop(key, None)
                self.events.log("status", f"{spec.label} 응답 회복 — 폴링 재개")
                return st
        if now >= self._recover_next.get(key, 0.0):
            self._recover_next[key] = now + STUCK_RETRY_S
            self._recover[key] = asyncio.create_task(
                asyncio.to_thread(getattr(drv, spec.status_attr)))
        return None

    async def _sample(self) -> dict[str, Any]:
        # 레지스트리 순회 — 각 장비의 status()/read()를 호출하고, Status가
        # 스스로 서술하는 snapshot()/telemetry()를 모은다. 새 장비를 REGISTRY에
        # 등록만 하면 대시보드 상태·1Hz 시계열에 자동 등장한다 (코드 변경 0).
        statuses: dict[str, Any] = {}
        device_snaps: dict[str, dict] = {}
        flat_telemetry: dict[str, Any] = {}
        now_mono = time.time()
        for key, spec in REGISTRY.items():
            drv = self.drivers[key]
            stuck = self._stuck.get(key)
            if stuck is not None and stuck is not drv:
                self._clear_recover(key)    # 재연결로 인스턴스 교체됨 → 옛 보류 상태 폐기
                stuck = None
            if stuck is drv:
                # 멈춘 인스턴스 → 폴링은 건너뛰되, 호밍 등으로 '잠깐' 멈춘 거라면
                # 재연결 없이 스스로 회복하도록 백그라운드로 다시 떠본다.
                st = self._recover_stuck(key, spec, drv, now_mono) or spec.offline_factory()
            else:
                try:
                    st = await asyncio.wait_for(
                        asyncio.to_thread(getattr(drv, spec.status_attr)),
                        timeout=STATUS_TIMEOUT_S)
                    self._stuck.pop(key, None)
                except asyncio.TimeoutError:
                    # COM 호출이 멈춤(죽은 하드웨어 또는 연결 직후 호밍 중) → 그 장비만
                    # 보류, 대시보드는 계속. 회복되면 _recover_stuck이 자동 재개한다.
                    self._stuck[key] = drv
                    self.events.log("status", f"{spec.label} 응답 없음 — 폴링 보류 "
                                    "(회복 시 자동 재개)", "warn")
                    st = spec.offline_factory()
                except Exception:
                    st = spec.offline_factory()
            statuses[key] = st

        # 가대 좌표 보완은 가대 고유라 직접 참조. 안전/기상은 아래 safety_role 기반.
        mount = statuses["mount"]
        weather = next((statuses[k] for k, s in REGISTRY.items()
                        if s.safety_role == "weather"), None)

        now = datetime.now(timezone.utc)
        sun_alt, sun_az = ephemeris.sun_altaz(now, self._lat, self._lon)
        lst = ephemeris.lst_hours(now, self._lon)
        phase_code, phase_label = ephemeris.twilight_phase(sun_alt)
        # 달·행성 — 30초마다 off-thread 재계산(astropy 무거움), 그 외엔 캐시 사용.
        mono = time.monotonic()
        if mono >= self._sky_next:
            self._sky_next = mono + 30.0
            try:
                self._sky_bodies = await asyncio.to_thread(
                    ephemeris.sky_bodies_altaz, now, self._lat, self._lon)
                self._sky_failed = False
            except Exception as exc:
                if not self._sky_failed:   # 지속 실패해도 경고는 1회(차트는 태양/마운트만)
                    self._sky_failed = True
                    self.events.log("status", f"천체 ephemeris 실패 "
                                    f"({type(exc).__name__}) — 차트는 태양/마운트만", "warn")

        # 일부 ASCOM 가대는 RA/Dec는 제공하지만 Alt/Az 속성을 구현하지 않는다.
        # 이때만 사이트/LST로 수평좌표를 보완한다. 장비가 준 0/270 같은 실제 값은
        # 덮어쓰지 않으며, 슬루 중 고정된 값은 드라이버의 stale 판정으로 구분한다.
        if (mount.connected and not mount.stale
                and (mount.alt_degs is None or mount.az_degs is None)
                and mount.ra_hours is not None and mount.dec_degs is not None):
            mount.alt_degs, mount.az_degs = ephemeris.radec_to_altaz(
                mount.ra_hours, mount.dec_degs, self._lat, lst)
            mount.coord_source = "derived-radec"

        for key, spec in REGISTRY.items():
            st = statuses[key]
            device_snaps[spec.snap_key] = st.snapshot()
            flat_telemetry.update(st.telemetry())

        autoflat = self.autoflat_status()
        capture = self.capture_status()
        orchestrator = self.orchestrator_status()
        session_running = (bool(autoflat.get("running"))
                           or bool(capture.get("active"))
                           or bool(orchestrator.get("running")))
        # 기상 텔레메트리 신선도 — 연결+유효값을 받은 마지막 시각을 기억한다.
        # 끊기거나 멈추면 경과시간이 늘어 fail-closed stale 판정이 작동한다(§6.5).
        # 로컬 기상 장치가 유효값을 주면 그것을, 없으면 원격 ingestion(§7) 폴백.
        local_ok = (weather is not None and weather.connected and any(
            v is not None for v in (weather.humidity, weather.wind_ms,
                                    weather.cloud_score, weather.temp_c,
                                    weather.dew_point_c)))
        weather_data = None
        if local_ok:
            self._last_weather_ok = time.monotonic()
            weather_data = {"rain": weather.rain, "wind": weather.wind_ms,
                            "humidity": weather.humidity, "cloud": weather.cloud_score}
        elif self.weather_ingest_fn is not None:   # 원격 PC 수신값으로 폴백(분산)
            rem = self.weather_ingest_fn()          # 신선하면 dict(+age_s), 아니면 None
            if rem is not None:
                self._last_weather_ok = time.monotonic() - float(rem.get("age_s", 0))
                weather_data = {"rain": rem.get("rain"), "wind": rem.get("wind_ms"),
                                "humidity": rem.get("humidity"), "cloud": rem.get("cloud_score")}
            # rem이 None이면 weather_data=None → _last_weather_ok 미갱신 → stale=unsafe(fail-closed)
        weather_age = (None if self._last_weather_ok is None
                       else time.monotonic() - self._last_weather_ok)
        # 안전 관련 장비는 REGISTRY의 safety_role로 찾는다 (키 하드코딩 제거).
        missing_required = [s.label for k, s in REGISTRY.items()
                            if s.safety_role == "required"
                            and not statuses[k].connected]
        saf = safety.evaluate(
            missing_required=missing_required,
            weather=weather_data,
            sun_alt=sun_alt,
            session_running=session_running,
            weather_age_s=weather_age,
            warn_age_s=self._wx_warn_s,
            unsafe_age_s=self._wx_unsafe_s,
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
            "orchestrator": orchestrator,
            "night_runner": self.night_runner_status(),
            "forge": self.forge_status(),
            "cooler": self.cooler_status(),
            "sky_bodies": self._sky_bodies,
            "telemetry_last": flat_telemetry,
            "defaults": {
                "autoflat": self.cfg.get("autoflat", {}) or {},
                "capture": self.cfg.get("capture", {}) or {},
            },
        }
        snapshot.update(device_snaps)  # mount/camera/filter/focuser/weather
        return snapshot
