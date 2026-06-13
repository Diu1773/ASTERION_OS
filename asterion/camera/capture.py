"""수동 캡처 엔진 — 단발/연속 노출, 프레임 타입, 오토세이브.

모든 노출은 ActionBus를 거쳐 감사로그에 남고, 프레임은 Frame +
TelescopeState로 온톨로지에 적재된다. 오토플랫(스카이플랫)과 카메라를
동시에 쓸 수 없으므로 시작 사전조건에서 상호 배제한다.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from ..config import Config
from ..core import fitsio
from ..core.actions import ActionBus, ActionError
from ..core.events import EventHub
from ..core.ontology import (
    Db, Frame, ObservationSession, TelescopeState, row_to_dict,
)

FRAME_TYPES = ("LIGHT", "FLAT", "DARK", "BIAS")


class CaptureService:
    def __init__(self, cfg: Config, drivers: dict[str, Any], bus: ActionBus,
                 db: Db, events: EventHub, frames_dir: Path,
                 blocked_fn: Callable[[], str | None] = lambda: None,
                 preview_cb=None):
        self.cfg = cfg
        self.drivers = drivers
        self.bus = bus
        self.db = db
        self.events = events
        self.frames_dir = frames_dir
        self._blocked_fn = blocked_fn
        self.preview_cb = preview_cb
        self.autosave = bool(cfg.get("capture.autosave", True))
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._state: dict[str, Any] = {
            "active": False, "state": "idle", "seq": 0, "count": 0,
            "frame_type": "LIGHT", "exposure": None,
            "last_median": None, "last_file": "",
        }

    # ---------- 상태 ----------

    def status_dict(self) -> dict[str, Any]:
        d = dict(self._state)
        d["autosave"] = self.autosave
        return d

    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    # ---------- 제어 ----------

    async def start(self, *, exposure_s: float, frame_type: str,
                    count: int, interval_s: float) -> None:
        if self.active():
            raise ActionError("캡처가 이미 실행 중입니다")
        frame_type = str(frame_type).upper()
        if frame_type not in FRAME_TYPES:
            raise ActionError(f"frame_type은 {'/'.join(FRAME_TYPES)} 중 하나")
        exposure_s = max(0.0, float(exposure_s))
        count = max(0, int(count))          # 0 = 무한 (정지 버튼까지)
        interval_s = max(0.0, float(interval_s))
        blocked = self._blocked_fn()
        cam_st = await asyncio.to_thread(self.drivers["camera"].status)

        async def _launch():
            self._stop.clear()
            self._task = asyncio.create_task(
                self._run(exposure_s, frame_type, count, interval_s),
                name="capture")

        await self.bus.run(
            "capture_start", actor="operator",
            params={"exposure_s": exposure_s, "frame_type": frame_type,
                    "count": count, "interval_s": interval_s,
                    "autosave": self.autosave},
            func=_launch,
            preconditions=[
                ("camera_connected", cam_st.connected, "카메라 연결 필요"),
                ("camera_free", blocked is None, blocked or ""),
            ],
        )

    async def stop(self) -> None:
        if not self.active():
            raise ActionError("실행 중인 캡처가 없습니다")
        self._stop.set()
        self.events.log("capture", "정지 요청 — 현재 노출 종료 후 멈춥니다", "warn")

    def set_autosave(self, on: bool) -> None:
        self.autosave = bool(on)

    # ---------- 본체 ----------

    async def _run(self, exposure_s: float, frame_type: str,
                   count: int, interval_s: float) -> None:
        light = frame_type in ("LIGHT", "FLAT")
        if frame_type == "BIAS":
            exposure_s = 0.0  # 바이어스는 최소 노출(셔터 닫힘)
        session = None
        if count != 1:
            session = self.db.add(ObservationSession(kind="capture"))
        self._state.update(active=True, state="running", seq=0, count=count,
                           frame_type=frame_type, exposure=exposure_s)
        n = 0
        try:
            while not self._stop.is_set():
                if count and n >= count:
                    break
                n += 1
                self._state.update(
                    seq=n, state=f"{frame_type} 노출 {exposure_s:.2f}s")
                img = await self.bus.run(
                    "expose_capture", actor="capture",
                    params={"frame_type": frame_type,
                            "exposure_s": round(exposure_s, 3), "seq": n},
                    func=lambda: asyncio.to_thread(
                        self.drivers["camera"].expose, exposure_s, light),
                )
                median = float(np.median(img))
                mount_st = await asyncio.to_thread(self.drivers["mount"].status)
                fw_st = await asyncio.to_thread(
                    self.drivers["filterwheel"].status)
                path = None
                if self.autosave:
                    path = await asyncio.to_thread(
                        fitsio.save_frame, self.frames_dir, self.cfg, img,
                        image_type=frame_type, filter_name=fw_st.name,
                        exposure_s=exposure_s, seq=n, mount_st=mount_st)
                tstate = self.db.add(TelescopeState(
                    ra_hours=mount_st.ra_hours, dec_degs=mount_st.dec_degs,
                    alt_degs=mount_st.alt_degs, az_degs=mount_st.az_degs,
                    tracking=mount_st.tracking, slewing=mount_st.slewing,
                ))
                frame = self.db.add(Frame(
                    session_id=session.id if session else None,
                    telescope_state_id=tstate.id,
                    file_path=str(path) if path else "",
                    image_type=frame_type, filter_name=fw_st.name,
                    exposure_s=round(exposure_s, 3),
                    median_adu=median, mean_adu=float(np.mean(img)),
                    std_adu=float(np.std(img)), flag="ok",
                ))
                self.events.frame(row_to_dict(frame))
                if self.preview_cb:
                    await self.preview_cb(img, {
                        "type": frame_type, "filter": fw_st.name,
                        "exposure_s": round(exposure_s, 3),
                        "median": round(median), "seq": n,
                        "file": path.name if path else ""})
                self._state.update(last_median=round(median),
                                   last_file=path.name if path else "")
                total = f"/{count}" if count else ""
                saved = f" → {path.name}" if path else ""
                self.events.log("capture",
                                f"[{frame_type}] #{n}{total} "
                                f"exp={exposure_s:.2f}s "
                                f"median={median:.0f}{saved}")
                if count == 1:
                    break
                try:
                    await asyncio.wait_for(self._stop.wait(),
                                           timeout=interval_s)
                    break
                except asyncio.TimeoutError:
                    pass
        except ActionError as exc:
            self.events.log("capture", f"캡처 중단: {exc}", "error")
        except Exception:
            self.events.log("capture",
                            f"캡처 오류:\n{traceback.format_exc()}", "error")
        finally:
            if session is not None:
                sid = session.id
                frames_done = n

                def _close(s):
                    row = s.get(ObservationSession, sid)
                    row.ended_utc = datetime.now(timezone.utc).isoformat(
                        timespec="seconds")
                    row.status = "done"
                    row.summary_json = json.dumps({"frames": frames_done})
                self.db.update(_close)
            self._state.update(active=False, state="idle")
            if n and count != 1:
                self.events.log("capture", f"연속 캡처 종료 — 총 {n}장")
