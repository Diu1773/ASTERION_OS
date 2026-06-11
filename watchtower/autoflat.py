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

from .actions import ActionBus, ActionError
from .config import Config
from .drivers.sim import TwilightSim
from .events import EventHub
from .ontology import (
    Db, Decision, Frame, ObservationSession, QualityMetric,
    TelescopeState, row_to_dict,
)

try:
    from astropy.io import fits as _fits
except ImportError:  # astropy 없으면 통계만 기록하고 파일 저장은 생략
    _fits = None


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
    sun_alt_start: float = -0.5

    @property
    def adu_target(self) -> float:
        return 0.5 * (self.adu_min + self.adu_max)

    @classmethod
    def from_config(cls, cfg: Config, override: dict | None = None) -> "AutoFlatParams":
        base = dict(cfg.get("autoflat", {}) or {})
        base.update({k: v for k, v in (override or {}).items() if v is not None})
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in base.items() if k in known})


class AutoFlatRunner:
    def __init__(self, cfg: Config, drivers: dict[str, Any], bus: ActionBus,
                 db: Db, events: EventHub, twilight: TwilightSim,
                 sun_alt_fn, frames_dir: Path):
        self.cfg = cfg
        self.drivers = drivers
        self.bus = bus
        self.db = db
        self.events = events
        self.twilight = twilight
        self.sun_alt_fn = sun_alt_fn
        self.frames_dir = frames_dir
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

    # ---------- 시작/정지 ----------

    async def start(self, params: AutoFlatParams) -> None:
        if self.running():
            raise ActionError("오토플랫이 이미 실행 중입니다")

        sun_alt = self.sun_alt_fn()
        mount_st = await asyncio.to_thread(self.drivers["mount"].status)
        cam_st = await asyncio.to_thread(self.drivers["camera"].status)
        sun_ok = (sun_alt <= params.sun_alt_start) or self.twilight.enabled

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
                ("after_sunset", sun_ok,
                 f"태양 고도 {sun_alt:+.1f}° > {params.sun_alt_start}° "
                 f"(일몰 전 — 황혼 시뮬을 켜면 테스트 가능)"),
            ],
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
        target_az = float(snap_sun_az) if snap_sun_az is not None else 0.0
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

    @staticmethod
    def _ascii(value: str) -> str:
        """FITS 헤더는 ASCII만 허용 — 비ASCII 문자는 제거."""
        return value.encode("ascii", errors="ignore").decode("ascii").strip()

    def _save_fits(self, img: np.ndarray, filt: str, exposure: float,
                   seq: int, mount_st) -> Path | None:
        if _fits is None:
            return None
        now = datetime.now(timezone.utc)
        day_dir = self.frames_dir / now.strftime("%Y%m%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        path = day_dir / f"FLAT_{filt}_{now.strftime('%H%M%S')}_{seq:03d}.fits"
        hdu = _fits.PrimaryHDU(img)
        h = hdu.header
        h["IMAGETYP"] = "FLAT"
        h["OBJECT"] = "SKYFLAT"
        h["FILTER"] = filt
        h["EXPTIME"] = round(exposure, 3)
        h["DATE-OBS"] = now.strftime("%Y-%m-%dT%H:%M:%S")
        site = self._ascii(str(self.cfg.get("site.name_ascii",
                                            self.cfg.get("site.name", ""))))
        if site:
            h["SITENAME"] = site
        h["INSTRUME"] = self._ascii(str(self.cfg.get("site.instrument", "")))
        h["FOCALLEN"] = float(self.cfg.get("site.focal_length_mm", 0.0))
        if mount_st.alt_degs is not None:
            h["ALTITUDE"] = round(mount_st.alt_degs, 4)
        if mount_st.az_degs is not None:
            h["AZIMUTH"] = round(mount_st.az_degs, 4)
        if mount_st.ra_hours is not None:
            h["RA"] = round(mount_st.ra_hours * 15.0, 6)
        if mount_st.dec_degs is not None:
            h["DEC"] = round(mount_st.dec_degs, 6)
        h["SWCREATE"] = "Watchtower 0.1"
        hdu.writeto(path, overwrite=True)
        return path
