"""오토플랫 세션 엔진.

청람천문대 수동 절차의 자동화:
  일몰 후 + 고도 40°↑ (미달 시 반태양 방위·고도 75°로 슬루)
  → 필터 순서대로:
      테스트 노출로 목표 ADU(20,000~25,000) 노출 탐색
      → [디더 → 가대 안정화 대기 → 촬영 → 통계/플래그/기록] × N장
  → 다음 필터.
하늘이 너무 어두워지면(최대 노출에서도 ADU 미달) 종료.
모든 행동은 ActionBus를 거쳐 ActionLog에 남고, 프레임은 Frame +
QualityMetric + TelescopeState로 온톨로지에 적재된다.
"""

from __future__ import annotations

import asyncio
import json
import random
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from ..config import Config
from ..core import ephemeris, fitsio
from ..core.actions import ActionBus, ActionError
from ..core.events import EventHub
from ..core.ontology import (
    Db, Decision, Frame, ObservationSession, QualityMetric,
    TelescopeState, row_to_dict,
)
from ..drivers.sim import TwilightSim
from ..watchtower import safety as _safety

# 오토플랫(박명 작업)을 시작/진행해도 되는 안전 상태. 이 밖이면(FAULT/EMERGENCY_CLOSE/
# WEATHER_HOLD/SAFE_CLOSED=주간) 시작 거부, 진행 중이면 pause→(미회복 시)abort.
# orchestrator.SAFE_TO_OBSERVE와 동일 집합 — 위험 액추에이션은 안전 스냅샷 신선도에 의존.
SAFE_TO_OBSERVE = {_safety.OPEN_ALLOWED, _safety.OBSERVING, _safety.READY_CHECK}


@dataclass
class AutoFlatParams:
    filters: list[str] = field(default_factory=lambda: ["B", "V", "R", "I"])
    frames_per_filter: int = 7
    adu_min: float = 20000.0
    adu_max: float = 25000.0
    min_alt_deg: float = 40.0
    flat_alt_deg: float = 75.0
    dither_arcsec: float = 30.0
    settle_seconds: float = 4.0
    initial_exposure: float = 1.0
    min_exposure: float = 0.1
    max_exposure: float = 60.0
    # 스카이플랫 박명 창: 태양 고도가 이 사이일 때만 (일몰/일출 전후).
    flat_sun_alt_high: float = -1.0   # 이보다 높으면 너무 밝음 (아직 시작 전)
    flat_sun_alt_low: float = -12.0   # 이보다 낮으면 하늘이 너무 어두움 (종료)

    @property
    def adu_target(self) -> float:
        return 0.5 * (self.adu_min + self.adu_max)

    def in_twilight_window(self, sun_alt: float) -> bool:
        return self.flat_sun_alt_low <= sun_alt <= self.flat_sun_alt_high

    @classmethod
    def from_config(cls, cfg: Config, override: dict | None = None) -> "AutoFlatParams":
        base = dict(cfg.get("autoflat", {}) or {})
        base.update({k: v for k, v in (override or {}).items() if v is not None})
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in base.items() if k in known})


class AutoFlatRunner:
    def __init__(self, cfg: Config, drivers: dict[str, Any], bus: ActionBus,
                 db: Db, events: EventHub, twilight: TwilightSim,
                 sun_alt_fn, frames_dir: Path, preview_cb=None,
                 safety_fn=None):
        self.cfg = cfg
        self.drivers = drivers
        self.bus = bus
        self.db = db
        self.events = events
        self.twilight = twilight
        self.sun_alt_fn = sun_alt_fn
        self.frames_dir = frames_dir
        self.preview_cb = preview_cb
        # safety_fn: 현재 안전 스냅샷 dict({"state","reasons",...})를 돌려주는 콜러블.
        # 운영에선 sampler.current_safety 주입(ts_mono 신선도 게이트 포함) — orchestrator/
        # night_runner와 동형. None이면 안전 게이트 비활성(드라이버 직접 테스트용).
        self.safety_fn = safety_fn
        self._max_pause_s = float(cfg.get("safety.observe_max_pause_seconds", 300.0))
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._state: dict[str, Any] = {"running": False, "phase": "idle"}

    # ---------- 상태 ----------

    def status_dict(self) -> dict[str, Any]:
        return dict(self._state)

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _set(self, **kw) -> None:
        self._state.update(kw)

    # ---------- 안전 게이트 (orchestrator와 동형) ----------

    def _safety_now(self) -> dict:
        try:
            return self.safety_fn() or {} if self.safety_fn else {}
        except Exception:
            return {"state": _safety.FAULT, "reasons": ["safety_fn 오류"]}

    async def _safety_gate(self, what: str) -> None:
        """위험 단계(슬루/디더/노출) 전 안전 확인. unsafe면 회복까지 pause하고,
        _max_pause_s 내 미회복이면 ActionError로 세션 중단(fail-closed: 의심스러우면 멈춘다).
        safety_fn 미주입(None)이면 게이트 비활성 — 드라이버 직접 테스트 거동 보존."""
        if self.safety_fn is None:
            return
        saf = self._safety_now()
        if saf.get("state") in SAFE_TO_OBSERVE:
            return
        self.events.log("autoflat",
                        f"안전 보류({saf.get('state')}) — {what} 전 대기. "
                        f"사유: {saf.get('reasons')}", "warn")
        self._set(phase=f"안전 대기 ({saf.get('state')})", safety=saf)
        poll = min(2.0, max(0.2, self._max_pause_s))
        waited = 0.0
        while not self._stop.is_set():
            await asyncio.sleep(poll)
            waited += poll
            saf = self._safety_now()
            if saf.get("state") in SAFE_TO_OBSERVE:
                self.events.log("autoflat", f"안전 회복({saf.get('state')}) — 재개")
                self._set(safety=saf)
                return
            if waited >= self._max_pause_s:
                raise ActionError(
                    f"안전 미회복({saf.get('state')}) {waited:.0f}s — 오토플랫 중단")
        raise ActionError("정지 요청 — 안전 대기 취소")

    # ---------- 시작/정지 ----------

    async def start(self, params: AutoFlatParams,
                    extra_preconditions: list[tuple[str, bool, str]] | None = None,
                    ) -> None:
        if self.running():
            raise ActionError("오토플랫이 이미 실행 중입니다")

        sun_alt = self.sun_alt_fn()
        mount_st = await asyncio.to_thread(self.drivers["mount"].status)
        cam_st = await asyncio.to_thread(self.drivers["camera"].status)
        # 스카이플랫은 박명 창(일몰/일출 전후)에서만. 시뮬 황혼이 켜져 있으면 우회.
        sun_ok = params.in_twilight_window(sun_alt) or self.twilight.enabled
        # 안전 게이트 — WEATHER_HOLD/EMERGENCY_CLOSE/FAULT/주간(SAFE_CLOSED) 중엔 시작 거부.
        # safety_fn 미주입이면 통과(테스트). orchestrator.start_plan과 동형.
        saf = self._safety_now()
        safe_now = self.safety_fn is None or saf.get("state") in SAFE_TO_OBSERVE

        async def _launch():
            self._stop.clear()
            self._task = asyncio.create_task(self._run(params), name="autoflat")

        await self.bus.run(
            "autoflat_session_start", actor="operator",
            params={"filters": params.filters,
                    "frames_per_filter": params.frames_per_filter,
                    "adu_range": [params.adu_min, params.adu_max]},
            func=_launch,
            preconditions=[
                ("camera_connected", cam_st.connected, "카메라 연결 필요"),
                ("mount_connected", mount_st.connected, "마운트 연결 필요"),
                ("safety_ok", safe_now,
                 f"안전 상태 불가({saf.get('state')}) — 오토플랫 시작 거부"),
                ("twilight_window", sun_ok,
                 f"태양 고도 {sun_alt:+.1f}°가 박명 창 "
                 f"({params.flat_sun_alt_high:.0f}°~{params.flat_sun_alt_low:.0f}°) 밖 — "
                 f"스카이플랫은 일몰/일출 전후에만. (시뮬은 황혼 토글로 테스트)"),
            ] + list(extra_preconditions or []),
        )

    async def request_stop(self) -> None:
        if not self.running():
            raise ActionError("실행 중인 오토플랫이 없습니다")
        self._stop.set()
        self.events.log("autoflat", "정지 요청 — 현재 단계 종료 후 멈춥니다", "warn")

    # ---------- 본체 ----------

    async def _run(self, p: AutoFlatParams) -> None:
        session = self.db.add(ObservationSession(kind="autoflat"))
        self._set(running=True, phase="시작", session_id=session.id,
                  filter=None, frame=0, total=p.frames_per_filter,
                  exposure=None, last_adu=None, results={})
        self.events.log("autoflat",
                        f"세션 #{session.id} 시작 — 필터 {p.filters}, "
                        f"{p.frames_per_filter}장/필터, "
                        f"목표 ADU {p.adu_min:.0f}~{p.adu_max:.0f}")
        results: dict[str, int] = {}
        status = "done"
        try:
            await self._ensure_flat_position(p)
            for filt in p.filters:
                if self._stop.is_set():
                    break
                # 박명 창을 벗어나면(저녁엔 너무 어두워짐 / 아침엔 너무 밝아짐) 종료.
                if not self.twilight.enabled:
                    sa = self.sun_alt_fn()
                    if not p.in_twilight_window(sa):
                        self.events.log(
                            "autoflat",
                            f"태양 고도 {sa:+.1f}°가 박명 창 "
                            f"({p.flat_sun_alt_high:.0f}°~{p.flat_sun_alt_low:.0f}°) 밖 — 세션 종료",
                            "warn")
                        break
                results[filt] = await self._do_filter(session.id, filt, p)
                self._state["results"] = dict(results)
            if self._stop.is_set():
                status = "stopped"
        except ActionError as exc:
            status = "failed"
            self.events.log("autoflat", f"세션 중단: {exc}", "error")
        except Exception:
            status = "error"
            self.events.log("autoflat",
                            f"예기치 못한 오류:\n{traceback.format_exc()}", "error")
        finally:
            summary = json.dumps(results, ensure_ascii=False)
            sid = session.id

            def _close(s):
                row = s.get(ObservationSession, sid)
                row.ended_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
                row.status = status
                row.summary_json = summary
            self.db.update(_close)

            rec = ", ".join(f"{f} {n}장" for f, n in results.items()) or "프레임 없음"
            self.db.add(Decision(
                source="autoflat",
                recommendation=f"플랫 세션 {status}: {rec}",
                evidence_json=summary, confidence=1.0,
                approved_by="rule", outcome=status,
            ))
            self._set(running=False, phase="idle", filter=None,
                      exposure=None)
            self.events.log("autoflat", f"세션 #{sid} 종료 ({status}) — {rec}",
                            "info" if status == "done" else "warn")

    async def _ensure_flat_position(self, p: AutoFlatParams) -> None:
        mount = self.drivers["mount"]
        st = await asyncio.to_thread(mount.status)
        if st.alt_degs is not None and st.alt_degs >= p.min_alt_deg:
            return
        snap_sun_az = (self.bus._snapshot_fn() or {}).get("sun", {}).get("antisolar_az")
        if snap_sun_az is not None:
            target_az = float(snap_sun_az)
        else:
            # 스냅샷에 반태양 방위가 없으면(초기 등) ephemeris로 직접 계산한다. 정북(0°) 폴백은
            # 계절·시각에 따라 저고도 태양과 충돌할 수 있어 금지 — 플랫은 항상 반태양(태양 회피).
            lat = float(self.cfg.get("site.latitude", 36.6))
            lon = float(self.cfg.get("site.longitude", 127.5))
            _sa, _sun_az = ephemeris.sun_altaz(ephemeris.now_utc(), lat, lon)
            target_az = (_sun_az + 180.0) % 360.0
        await self._safety_gate("플랫 위치 슬루")
        self._set(phase="플랫 위치로 슬루")
        self.events.log("autoflat",
                        f"고도 {st.alt_degs:.1f}° < {p.min_alt_deg}° — "
                        f"플랫 위치(고도 {p.flat_alt_deg}°, 방위 {target_az:.0f}°)로 이동")
        await self.bus.run(
            "mount_goto_flat_field", actor="autoflat",
            params={"alt": p.flat_alt_deg, "az": target_az},
            func=lambda: asyncio.to_thread(mount.goto_altaz, p.flat_alt_deg, target_az),
        )
        await self._wait_slew_done(timeout=120.0)
        await self.bus.run(
            "mount_tracking_on", actor="autoflat", params={},
            func=lambda: asyncio.to_thread(mount.set_tracking, True),
        )

    async def _wait_slew_done(self, timeout: float) -> None:
        mount = self.drivers["mount"]
        deadline = asyncio.get_event_loop().time() + timeout
        while not self._stop.is_set():
            st = await asyncio.to_thread(mount.status)
            if not st.slewing:
                return
            if asyncio.get_event_loop().time() > deadline:
                raise ActionError("슬루 완료 대기 시간 초과")
            await asyncio.sleep(0.3)

    # ---------- 필터 단위 ----------

    async def _do_filter(self, session_id: int, filt: str,
                         p: AutoFlatParams) -> int:
        fw = self.drivers["filterwheel"]
        names = (await asyncio.to_thread(fw.status)).names
        if filt not in names:
            self.events.log("autoflat", f"[{filt}] 필터휠에 없음 — 건너뜀", "warn")
            return 0
        idx = names.index(filt)
        self._set(phase=f"{filt} 필터 이동", filter=filt, frame=0)
        await self.bus.run(
            "filter_set", actor="autoflat",
            params={"filter": filt, "position": idx},
            func=lambda: asyncio.to_thread(fw.set_position, idx),
        )

        exposure = await self._acquire_exposure(filt, p)
        if exposure is None:
            self.events.log("autoflat",
                            f"[{filt}] 적정 노출 확보 실패 — 필터 건너뜀", "warn")
            return 0

        n_ok = 0
        mount = self.drivers["mount"]
        for seq in range(1, p.frames_per_filter + 1):
            if self._stop.is_set():
                break
            await self._safety_gate(f"{filt} #{seq} 플랫 노출")
            # 1) 디더 (별이 같은 픽셀에 찍히지 않게)
            dra = random.uniform(-p.dither_arcsec, p.dither_arcsec)
            ddec = random.uniform(-p.dither_arcsec, p.dither_arcsec)
            self._set(phase=f"{filt} 디더/안정화", frame=seq)
            await self.bus.run(
                "dither", actor="autoflat",
                params={"dra_arcsec": round(dra, 1), "ddec_arcsec": round(ddec, 1)},
                func=lambda: asyncio.to_thread(mount.offset_arcsec, dra, ddec),
            )
            # 2) 가대 안정화 대기
            await self._wait_slew_done(timeout=60.0)
            await asyncio.sleep(p.settle_seconds)
            if self._stop.is_set():
                break
            # 3) 촬영
            self._set(phase=f"{filt} 노출 {exposure:.2f}s", exposure=round(exposure, 2))
            img = await self.bus.run(
                "expose_flat", actor="autoflat",
                params={"filter": filt, "exposure_s": round(exposure, 3), "seq": seq},
                func=lambda: asyncio.to_thread(
                    self.drivers["camera"].expose, exposure, True),
            )
            # 4) 통계 → 플래그/기록
            stats = self._stats(img)
            in_range = p.adu_min <= stats["median"] <= p.adu_max
            flag = "ok" if in_range else "out_of_range"
            mount_st = await asyncio.to_thread(mount.status)
            tstate = self.db.add(TelescopeState(
                ra_hours=mount_st.ra_hours, dec_degs=mount_st.dec_degs,
                alt_degs=mount_st.alt_degs, az_degs=mount_st.az_degs,
                tracking=mount_st.tracking, slewing=mount_st.slewing,
            ))
            path = self._save_fits(img, filt, exposure, seq, mount_st)
            frame = self.db.add(Frame(
                session_id=session_id, telescope_state_id=tstate.id,
                file_path=str(path) if path else "",
                image_type="FLAT", filter_name=filt,
                exposure_s=round(exposure, 3),
                median_adu=stats["median"], mean_adu=stats["mean"],
                std_adu=stats["std"], flag=flag,
            ))
            self.db.add(QualityMetric(
                frame_id=frame.id, median_adu=stats["median"],
                std_adu=stats["std"], min_adu=stats["min"],
                max_adu=stats["max"], saturation_frac=stats["sat_frac"],
                verdict=flag,
                reason="" if in_range else
                       f"중앙값 {stats['median']:.0f} ADU가 목표 범위 밖",
            ))
            self.events.frame(row_to_dict(frame))
            if self.preview_cb:
                await self.preview_cb(img, {
                    "type": "FLAT", "filter": filt,
                    "exposure_s": round(exposure, 3),
                    "median": round(stats["median"]), "seq": seq,
                    "file": path.name if path else ""})
            self._set(last_adu=round(stats["median"]), frame=seq)
            self.events.log(
                "autoflat",
                f"[FLAT][{filt}] #{seq}/{p.frames_per_filter} "
                f"exp={exposure:.2f}s ADU={stats['median']:.0f} "
                f"{'ok' if in_range else 'OUT'} "
                f"(dither {dra:+.0f},{ddec:+.0f}\")",
                "info" if in_range else "warn",
            )
            if in_range:
                n_ok += 1
            # 5) 다음 프레임 노출 보정 (하늘 밝기 변화 추적)
            ratio = p.adu_target / max(stats["median"], 1.0)
            exposure = float(np.clip(exposure * np.clip(ratio, 0.5, 2.0),
                                     p.min_exposure, p.max_exposure))
            if stats["median"] < p.adu_min and exposure >= p.max_exposure - 1e-9:
                self.events.log("autoflat",
                                f"[{filt}] 최대 노출에서도 ADU 미달 — "
                                f"하늘이 너무 어두움, 필터 종료", "warn")
                break
        self.events.log("autoflat",
                        f"[{filt}] 완료 — 정상 범위 {n_ok}장 / 시도 "
                        f"{min(seq, p.frames_per_filter)}장")
        return n_ok

    # ---------- 노출 탐색 ----------

    async def _acquire_exposure(self, filt: str, p: AutoFlatParams) -> float | None:
        exposure = p.initial_exposure
        adjusts = 0
        waits = 0
        while not self._stop.is_set():
            await self._safety_gate(f"{filt} 노출 탐색")
            self._set(phase=f"{filt} 노출 탐색 ({exposure:.2f}s)",
                      exposure=round(exposure, 2))
            img = await self.bus.run(
                "expose_test", actor="autoflat",
                params={"filter": filt, "exposure_s": round(exposure, 3)},
                func=lambda: asyncio.to_thread(
                    self.drivers["camera"].expose, exposure, True),
            )
            adu = float(np.median(img))
            self._set(last_adu=round(adu))
            self.events.log("autoflat",
                            f"[{filt}] 테스트 exp={exposure:.2f}s → ADU {adu:.0f}")
            if p.adu_min <= adu <= p.adu_max:
                return exposure
            # 너무 밝은데 최소 노출이면 → 하늘 어두워질 때까지 대기 (저녁)
            if adu > p.adu_max and exposure <= p.min_exposure + 1e-9:
                waits += 1
                if waits > 40:  # 약 13분 대기 후 포기
                    return None
                self.events.log("autoflat",
                                f"[{filt}] 하늘이 아직 밝음 (ADU {adu:.0f}) — "
                                f"20초 대기", "warn")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=20.0)
                    return None  # 대기 중 정지 요청
                except asyncio.TimeoutError:
                    continue
            # 너무 어두운데 최대 노출이면 → 포기
            if adu < p.adu_min and exposure >= p.max_exposure - 1e-9:
                self.events.log("autoflat",
                                f"[{filt}] 최대 노출 {p.max_exposure:.0f}s에서도 "
                                f"ADU {adu:.0f} < {p.adu_min:.0f}", "warn")
                return None
            ratio = float(np.clip(p.adu_target / max(adu, 1.0), 0.2, 5.0))
            exposure = float(np.clip(exposure * ratio,
                                     p.min_exposure, p.max_exposure))
            adjusts += 1
            if adjusts > 10:
                return None
        return None

    # ---------- 유틸 ----------

    @staticmethod
    def _stats(img: np.ndarray) -> dict[str, float]:
        return {
            "median": float(np.median(img)),
            "mean": float(np.mean(img)),
            "std": float(np.std(img)),
            "min": float(np.min(img)),
            "max": float(np.max(img)),
            "sat_frac": float(np.mean(img >= 60000)),
        }

    def _save_fits(self, img: np.ndarray, filt: str, exposure: float,
                   seq: int, mount_st) -> Path | None:
        return fitsio.save_frame(
            self.frames_dir, self.cfg, img, image_type="FLAT",
            filter_name=filt, exposure_s=exposure, seq=seq,
            mount_st=mount_st, object_name="SKYFLAT")
