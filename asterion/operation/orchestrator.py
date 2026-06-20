"""Orchestrator — 관측 실행 지휘자 (로드맵 §8.4).

autoflat(`skyflat/autoflat.py`)이 증명한 패턴을 *범용* 관측 시퀀스로 일반화한다:
승인된 `ObservationPlan`을 받아 표준 과학 시퀀스를 실행한다 —

    (사전조건) → unpark → goto(target) → 슬루 대기 → tracking on
    → plate-solve(후크) → autofocus(후크)
    → [필터별: set filter → (디더/안정화) → expose×N → Frame/QualityMetric/
       TelescopeState 적재] → 세션 종료

모든 장비 명령은 `ActionBus`를 통과한다(감사·사전조건 일관). 장비를 직접
만지지 않는다. 안전 게이트 소비(WEATHER_HOLD 등 → pause/abort)는 Ph7-3,
plate-solve/autofocus 실제 구현은 Ph7-4, HTTP 상태/제어 노출은 Ph7-5에서 붙는다.
"""

from __future__ import annotations

import asyncio
import json
import random
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from ..config import Config
from ..core import fitsio
from ..core.actions import ActionBus, ActionError
from ..core.events import EventHub
from ..core.focus_offset import apply_filter_focus_offset
from ..core.ontology import (
    Db, Decision, FocusRun, Frame, ObservationSession, QualityMetric,
    TelescopeState, row_to_dict,
)
from . import meridian as M
from .meridian import Meridian
from ..watchtower import safety as _safety

# 관측을 진행/시작해도 되는 안전 상태. 이 밖이면(FAULT/EMERGENCY_CLOSE/WEATHER_HOLD/
# SAFE_CLOSED=주간) 시작 거부 또는 진행 중 pause→(미회복 시)abort. OBSERVING은 세션이
# 돌 때의 상태이므로 반드시 포함(아니면 시작 직후 자기 자신을 막는다).
SAFE_TO_OBSERVE = {_safety.OPEN_ALLOWED, _safety.OBSERVING, _safety.READY_CHECK}


class ObservationOrchestrator:
    def __init__(self, cfg: Config, drivers: dict[str, Any], bus: ActionBus,
                 db: Db, events: EventHub, meridian: Meridian, frames_dir: Path,
                 preview_cb: Callable[..., Awaitable[None]] | None = None,
                 safety_fn: Callable[[], dict] | None = None,
                 max_pause_s: float | None = None,
                 platesolve_fn: Callable[[float, float], dict | None] | None = None,
                 autofocus_fn: Callable[[], dict | None] | None = None):
        self.cfg = cfg
        self.drivers = drivers
        self.bus = bus
        self.db = db
        self.events = events
        self.meridian = meridian
        self.frames_dir = frames_dir
        self.preview_cb = preview_cb
        # 주입형 후크 (operation/hooks.py). None이면 ActionLog만 남기는 no-op.
        self.platesolve_fn = platesolve_fn
        self.autofocus_fn = autofocus_fn
        # safety_fn: 현재 안전 스냅샷 dict({"state","reasons",...})를 돌려주는 콜러블.
        # None이면 안전 게이트 비활성(드라이버 직접 테스트용). 운영에선 sampler가 주입.
        self.safety_fn = safety_fn
        self.max_pause_s = float(
            max_pause_s if max_pause_s is not None
            else self.cfg.get("safety.observe_max_pause_seconds", 300.0))
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._state: dict[str, Any] = {"running": False, "phase": "idle",
                                       "plan_id": None, "paused": False}

    # ---------- 안전 게이트 (Ph7-3) ----------

    def _safety_now(self) -> dict:
        try:
            return self.safety_fn() or {} if self.safety_fn else {}
        except Exception:
            return {"state": _safety.FAULT, "reasons": ["safety_fn 오류"]}

    async def _safety_gate(self, what: str) -> None:
        """위험 단계 전 안전 확인. unsafe면 회복까지 pause하고, max_pause_s 내에
        회복 안 되면 ActionError로 시퀀스를 중단한다 (fail-closed: 의심스러우면 멈춘다)."""
        if self.safety_fn is None:
            return
        saf = self._safety_now()
        if saf.get("state") in SAFE_TO_OBSERVE:
            return
        self.events.log("orchestrator",
                        f"안전 보류({saf.get('state')}) — {what} 전 대기. "
                        f"사유: {saf.get('reasons')}", "warn")
        self._set(phase=f"안전 대기 ({saf.get('state')})", paused=True, safety=saf)
        poll = min(2.0, max(0.2, self.max_pause_s))
        waited = 0.0
        while not self._stop.is_set():
            await asyncio.sleep(poll)
            waited += poll
            saf = self._safety_now()
            if saf.get("state") in SAFE_TO_OBSERVE:
                self.events.log("orchestrator", f"안전 회복({saf.get('state')}) — 재개")
                self._set(paused=False, safety=saf)
                return
            if waited >= self.max_pause_s:
                raise ActionError(
                    f"안전 미회복({saf.get('state')}) {waited:.0f}s — 관측 중단")
        raise ActionError("정지 요청 — 안전 대기 취소")

    # ---------- 상태 ----------

    def status_dict(self) -> dict[str, Any]:
        return dict(self._state)

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _set(self, **kw) -> None:
        self._state.update(kw)

    # ---------- 시작/정지 ----------

    async def start_plan(self, plan_id: int,
                         extra_preconditions: list[tuple[str, bool, str]] | None = None
                         ) -> None:
        if self.running():
            raise ActionError("이미 다른 관측이 실행 중입니다")
        plan = self.meridian.get_plan(plan_id)
        cam_st = await asyncio.to_thread(self.drivers["camera"].status)
        mount_st = await asyncio.to_thread(self.drivers["mount"].status)
        target = (plan or {}).get("target") or {}
        has_coords = (target.get("ra_hours") is not None
                      and target.get("dec_degs") is not None)
        approved = bool(plan) and plan["approval_status"] in (M.APPROVED, M.QUEUED)
        saf = self._safety_now()
        safe_now = self.safety_fn is None or saf.get("state") in SAFE_TO_OBSERVE

        async def _launch():
            self._stop.clear()
            self._task = asyncio.create_task(self._run_plan(plan),
                                             name=f"observation-{plan_id}")

        await self.bus.run(
            "observation_start", actor="operator",
            params={"plan_id": plan_id, "target": target.get("name")},
            func=_launch,
            preconditions=[
                ("plan_exists", plan is not None, f"계획 #{plan_id} 없음"),
                ("plan_approved", approved, "승인된(approved) 계획만 실행 가능"),
                ("target_has_coords", has_coords, "대상에 RA/Dec 좌표 필요"),
                ("camera_connected", cam_st.connected, "카메라 연결 필요"),
                ("mount_connected", mount_st.connected, "마운트 연결 필요"),
                ("safety_ok", safe_now,
                 f"안전 상태 불가({saf.get('state')}) — 관측 시작 거부"),
            ] + list(extra_preconditions or []),
        )

    async def request_stop(self) -> None:
        if not self.running():
            raise ActionError("실행 중인 관측이 없습니다")
        self._stop.set()
        self.events.log("orchestrator", "정지 요청 — 현재 단계 종료 후 멈춥니다", "warn")

    async def wait(self) -> None:
        """현재 실행 중인 시퀀스가 끝날 때까지 대기 (테스트/종료용)."""
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---------- ActionBus 래퍼 ----------

    async def _action(self, name: str, params: dict,
                      fn: Callable[[], Awaitable[Any]] | None) -> Any:
        return await self.bus.run(name, actor="orchestrator", params=params,
                                  func=fn)

    # ---------- 본체 ----------

    async def _run_plan(self, plan: dict[str, Any]) -> None:
        pid = plan["id"]
        strat = plan.get("params") or {}
        target = plan.get("target") or {}
        name = target.get("name", "?")
        ra_h = float(target["ra_hours"])
        dec_d = float(target["dec_degs"])
        filters = list(strat.get("filters") or [])
        exposure = float(strat.get("exposure_s", 60.0))
        count = int(strat.get("count_per_filter", 1))
        dither = float(strat.get("dither_arcsec", 0.0))

        session = self.db.add(ObservationSession(kind="science", plan_id=pid))
        self.meridian.set_status(pid, M.RUNNING)
        self._set(running=True, phase="시작", plan_id=pid, session_id=session.id,
                  target=name, filters=filters, filter=None, frame=0,
                  total=count * len(filters), saved=0, last_median=None)
        self.events.log("orchestrator",
                        f"관측 #{pid} 시작 — {name} {filters} "
                        f"{exposure:.1f}s×{count}/필터 (세션 #{session.id})")
        saved = 0
        per_filter: dict[str, int] = {}
        status = "done"
        try:
            await self._action("mount_unpark", {},
                               lambda: asyncio.to_thread(self.drivers["mount"].unpark))
            await self._slew_to_target(name, ra_h, dec_d)
            await self._action("mount_tracking_on", {},
                               lambda: asyncio.to_thread(self.drivers["mount"].set_tracking, True))
            await self._plate_solve(name, ra_h, dec_d)
            await self._autofocus()
            for filt in filters:
                if self._stop.is_set():
                    break
                n = await self._do_filter(session.id, name, filt, exposure,
                                          count, dither)
                per_filter[filt] = n
                saved += n
                self._set(saved=saved)
            if self._stop.is_set():
                status = "stopped"
        except ActionError as exc:
            status = "failed"
            self.events.log("orchestrator", f"관측 중단: {exc}", "error")
        except Exception:
            status = "error"
            self.events.log("orchestrator",
                            f"예기치 못한 오류:\n{traceback.format_exc()}", "error")
        finally:
            summary = json.dumps({"target": name, "saved": saved,
                                  "per_filter": per_filter}, ensure_ascii=False)
            sid = session.id

            def _close(s):
                row = s.get(ObservationSession, sid)
                row.ended_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
                row.status = status
                row.summary_json = summary
            self.db.update(_close)

            plan_status = {"done": M.DONE, "stopped": M.ABORTED,
                           "failed": M.FAILED, "error": M.FAILED}.get(status, M.FAILED)
            self.meridian.set_status(pid, plan_status)
            self.db.add(Decision(
                source="orchestrator",
                recommendation=f"관측 #{pid} {status}: {name} {saved}프레임 저장",
                evidence_json=summary, confidence=1.0,
                approved_by="operator", outcome=status))
            self._set(running=False, phase="idle", filter=None)
            self.events.log(
                "orchestrator",
                f"관측 #{pid} 종료 ({status}) — {name} {saved}프레임",
                "info" if status == "done" else "warn")

    # ---------- 단계 ----------

    async def _slew_to_target(self, name: str, ra_h: float, dec_d: float) -> None:
        await self._safety_gate(f"{name} 슬루")
        self._set(phase=f"{name}로 슬루")
        mount = self.drivers["mount"]
        await self._action("mount_goto_radec",
                           {"ra_hours": round(ra_h, 5), "dec_degs": round(dec_d, 5),
                            "target": name},
                           lambda: asyncio.to_thread(mount.goto_radec, ra_h, dec_d))
        await self._wait_slew_done(timeout=120.0)

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

    async def _plate_solve(self, name: str, ra_h: float, dec_d: float) -> None:
        """플레이트 솔브 후크. `platesolve_fn` 주입 시 호출(결과를 상태에 보관),
        없으면 ActionLog만 남기는 no-op. 솔브 결과로 sync/recenter할 자리."""
        self._set(phase="plate-solve")
        params = {"target": name, "ra_hours": round(ra_h, 5),
                  "dec_degs": round(dec_d, 5)}
        if self.platesolve_fn is None:
            await self._action("plate_solve", {**params, "stub": True}, None)
            return
        result = await self._action(
            "plate_solve", params,
            lambda: asyncio.to_thread(self.platesolve_fn, ra_h, dec_d))
        self._set(platesolve=result)
        if result:
            self.events.log("orchestrator",
                            f"plate-solve 성공 — 오차 {result.get('error_arcsec')}\"")

    async def _autofocus(self, filt: str = "") -> None:
        """오토포커스 후크. `autofocus_fn` 주입 시 호출하고 결과를 FocusRun으로
        적재, 없으면 ActionLog만 남기는 no-op."""
        self._set(phase="autofocus")
        if self.autofocus_fn is None:
            await self._action("autofocus", {"stub": True}, None)
            return
        result = await self._action("autofocus", {},
                                    lambda: asyncio.to_thread(self.autofocus_fn))
        if result:
            self.db.add(FocusRun(
                filter_name=filt,
                focuser_position=result.get("best_position"),
                best_fwhm=result.get("best_fwhm"),
                confidence=result.get("confidence"),
                environment_json=json.dumps(result, ensure_ascii=False)))
            self.events.log("orchestrator",
                            f"autofocus 완료 — pos={result.get('best_position')} "
                            f"FWHM={result.get('best_fwhm')}")

    async def _do_filter(self, session_id: int, target_name: str, filt: str,
                         exposure: float, count: int, dither: float) -> int:
        fw = self.drivers["filterwheel"]
        fw_st = await asyncio.to_thread(fw.status)
        names = fw_st.names
        if filt not in names:
            self.events.log("orchestrator", f"[{filt}] 필터휠에 없음 — 건너뜀", "warn")
            return 0
        idx = names.index(filt)
        prev = fw_st.position   # 교체 직전 필터 — 포커스 오프셋 델타 기준
        self._set(phase=f"{filt} 필터 이동", filter=filt, frame=0)
        await self._action("filter_set", {"filter": filt, "position": idx},
                           lambda: asyncio.to_thread(fw.set_position, idx))
        # 필터별 포커스 오프셋 자동 보정 (best-effort)
        try:
            await apply_filter_focus_offset(self.cfg, self.drivers,
                                            self._action, prev, idx)
        except Exception:
            self.events.log("orchestrator", "포커스 오프셋 적용 실패 — 건너뜀", "warn")

        mount = self.drivers["mount"]
        cam = self.drivers["camera"]
        n_ok = 0
        for seq in range(1, count + 1):
            if self._stop.is_set():
                break
            await self._safety_gate(f"{filt} #{seq} 노출")
            if dither > 0.0:
                dra = random.uniform(-dither, dither)
                ddec = random.uniform(-dither, dither)
                self._set(phase=f"{filt} 디더/안정화", frame=seq)
                await self._action("dither",
                                   {"dra_arcsec": round(dra, 1),
                                    "ddec_arcsec": round(ddec, 1)},
                                   lambda: asyncio.to_thread(mount.offset_arcsec,
                                                             dra, ddec))
                await self._wait_slew_done(timeout=60.0)
            self._set(phase=f"{filt} 노출 {exposure:.2f}s", frame=seq,
                      exposure=round(exposure, 2))
            img = await self._action(
                "expose_light",
                {"filter": filt, "exposure_s": round(exposure, 3), "seq": seq},
                lambda: asyncio.to_thread(cam.expose, exposure, True))
            stats = self._stats(img)
            mount_st = await asyncio.to_thread(mount.status)
            tstate = self.db.add(TelescopeState(
                ra_hours=mount_st.ra_hours, dec_degs=mount_st.dec_degs,
                alt_degs=mount_st.alt_degs, az_degs=mount_st.az_degs,
                tracking=mount_st.tracking, slewing=mount_st.slewing))
            path = fitsio.save_frame(
                self.frames_dir, self.cfg, img, image_type="LIGHT",
                filter_name=filt, exposure_s=exposure, seq=seq,
                mount_st=mount_st, object_name=target_name)
            frame = self.db.add(Frame(
                session_id=session_id, telescope_state_id=tstate.id,
                file_path=str(path) if path else "", image_type="LIGHT",
                filter_name=filt, exposure_s=round(exposure, 3),
                median_adu=stats["median"], mean_adu=stats["mean"],
                std_adu=stats["std"], flag="ok"))
            self.db.add(QualityMetric(
                frame_id=frame.id, median_adu=stats["median"],
                std_adu=stats["std"], min_adu=stats["min"], max_adu=stats["max"],
                saturation_frac=stats["sat_frac"], verdict="ok"))
            self.events.frame(row_to_dict(frame))
            if self.preview_cb:
                await self.preview_cb(img, {
                    "type": "LIGHT", "filter": filt, "target": target_name,
                    "exposure_s": round(exposure, 3),
                    "median": round(stats["median"]), "seq": seq,
                    "file": path.name if path else ""})
            n_ok += 1
            self._set(last_median=round(stats["median"]), frame=seq)
            self.events.log(
                "orchestrator",
                f"[LIGHT][{filt}] #{seq}/{count} exp={exposure:.2f}s "
                f"ADU={stats['median']:.0f}")
        self.events.log("orchestrator", f"[{filt}] 완료 — {n_ok}/{count}프레임")
        return n_ok

    # ---------- 유틸 ----------

    @staticmethod
    def _stats(img: np.ndarray) -> dict[str, float]:
        return {
            "median": float(np.median(img)), "mean": float(np.mean(img)),
            "std": float(np.std(img)), "min": float(np.min(img)),
            "max": float(np.max(img)), "sat_frac": float(np.mean(img >= 60000)),
        }
