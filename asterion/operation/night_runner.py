"""Night Runner — 무인 야간 운영기 (자율 루프 닫기 / NIGHT_RUNNER_PLAN.md).

스케줄러(`agent/toolkit.py` plan_night)가 만든 비겹침 ObservationPlan 시간표를,
단일 계획 실행기 `ObservationOrchestrator` 위에서 **슬롯 순서대로 무인 실행**한다.
장비를 직접 만지지 않는다 — 큐 관리 + 슬롯 타이밍 + 안전 홀드 + Orchestrator 호출만.

    승인 계획(slot_start순) → [슬롯 대기 → 안전 게이트 → orchestrator.start_plan
    → wait → 결과 분류] → 다음. 실패해도 밤은 계속, stop 요청 시 중단.

증분(NIGHT_RUNNER_PLAN.md): S1 스켈레톤(start/stop/status_dict + 빈 루프) ← 현재.
S2 큐구성(slot_start순), S3 실행시퀀스, S4 안전게이트/홀드, S5 슬롯타이밍, S6 REST.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..core.actions import ActionError
from ..core.events import EventHub
from .meridian import Meridian
from .orchestrator import ObservationOrchestrator


class NightRunner:
    def __init__(self, meridian: Meridian, orchestrator: ObservationOrchestrator,
                 events: EventHub, cfg: Any = None, safety_fn=None):
        self.meridian = meridian
        self.orch = orchestrator
        self.events = events
        self.cfg = cfg
        # safety_fn: 현재 안전 스냅샷 dict를 돌려주는 콜러블(없으면 게이트 비활성). S4에서 소비.
        self.safety_fn = safety_fn
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._state: dict[str, Any] = self._blank_state()

    @staticmethod
    def _blank_state() -> dict[str, Any]:
        return {"active": False, "phase": "idle", "held": False, "reason": None,
                "current": None, "queue": [], "done": [], "failed": [], "skipped": []}

    # ---------- 상태 ----------

    def status_dict(self) -> dict[str, Any]:
        return dict(self._state)

    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _set(self, **kw) -> None:
        self._state.update(kw)

    # ---------- 시작/정지 ----------

    async def start(self, plan_ids: list[int] | None = None,
                    respect_slots: bool = True) -> None:
        """승인된 계획 시간표를 무인 실행. plan_ids=None이면 승인 전체(slot_start순).
        이미 실행 중이거나 Orchestrator가 단일 관측 중이면 거부(교차배제)."""
        if self.running():
            raise ActionError("야간 운영기가 이미 실행 중입니다")
        if self.orch.running():
            raise ActionError("관측(Orchestrator) 실행 중 — 야간 운영 시작 거부")
        self._stop.clear()
        self._state = self._blank_state()
        self._task = asyncio.create_task(
            self._loop(plan_ids, respect_slots), name="night-runner")

    async def request_stop(self) -> None:
        if not self.running():
            raise ActionError("실행 중인 야간 운영이 없습니다")
        self._stop.set()
        try:
            await self.orch.request_stop()
        except ActionError:
            pass   # 진행 중 계획이 없으면 무시
        self.events.log("night_runner", "정지 요청 — 현재 계획 종료 후 멈춥니다", "warn")

    async def wait(self) -> None:
        """현재 야간 운영 루프가 끝날 때까지 대기 (테스트/종료용)."""
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ---------- 실행 루프 ----------

    async def _loop(self, plan_ids: list[int] | None, respect_slots: bool) -> None:
        """S1 스켈레톤 — 빈 루프. 큐구성(S2)·실행시퀀스(S3)·안전(S4)·타이밍(S5)에서 채운다."""
        self._set(active=True, phase="시작")
        try:
            self.events.log("night_runner",
                            "야간 운영 시작 (S1 스켈레톤 — 시퀀싱 미구현)")
            self._set(phase="idle")
        finally:
            self._set(active=False, current=None)
