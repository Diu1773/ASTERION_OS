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
from ..watchtower import safety as _safety
from . import meridian as M
from .meridian import Meridian
from .orchestrator import SAFE_TO_OBSERVE, ObservationOrchestrator


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

    # ---------- 슬롯 시각 (KST "HH:MM" → 야간분: 저녁<자정<새벽) ----------

    @staticmethod
    def _slot_min(hhmm) -> int | None:
        if not hhmm or ":" not in str(hhmm):
            return None
        p = str(hhmm).split(":")
        h, m = int(p[0]), int(p[1])
        return (h + 24 if h < 12 else h) * 60 + m

    @staticmethod
    def _now_night_min() -> int:
        from datetime import datetime, timedelta, timezone
        kst = datetime.now(timezone.utc) + timedelta(hours=9)
        return (kst.hour + 24 if kst.hour < 12 else kst.hour) * 60 + kst.minute

    # ---------- 큐 구성 (S2) ----------

    def _build_queue(self, plan_ids: list[int] | None, respect_slots: bool):
        """실행 큐를 slot_start 순으로. respect_slots면 slot_end 지난 계획은 skip.
        반환 (queue, skipped) — 각 항목 {plan_id,target,slot_start,slot_end[,reason]}."""
        if plan_ids:
            plans = [p for p in (self.meridian.get_plan(i) for i in plan_ids) if p]
        else:
            plans = self.meridian.list_plans(status=M.APPROVED)
        deco = []
        for p in plans:
            pr = p.get("params") or {}
            t = p.get("target") or {}
            deco.append((self._slot_min(pr.get("slot_start")), p, pr, t,
                         self._slot_min(pr.get("slot_end"))))
        # slot_start 순(없으면 뒤로), 동률은 id로 안정 정렬
        deco.sort(key=lambda d: (d[0] is None, d[0] or 0, d[1].get("id") or 0))
        now_nm = self._now_night_min()
        queue, skipped = [], []
        for smin, p, pr, t, emin in deco:
            item = {"plan_id": p.get("id"), "target": t.get("name") or "—",
                    "slot_start": pr.get("slot_start"), "slot_end": pr.get("slot_end")}
            if respect_slots and emin is not None and emin <= now_nm:
                skipped.append({**item, "reason": "슬롯 종료 시각 경과"})
            else:
                queue.append(item)
        return queue, skipped

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

    # ---------- 안전 게이트 (S4) ----------

    def _safety_now(self) -> dict:
        try:
            return (self.safety_fn() or {}) if self.safety_fn else {}
        except Exception:
            return {"state": _safety.FAULT, "reasons": ["safety_fn 오류"]}

    async def _safety_hold(self, item: dict) -> bool:
        """슬롯 진입 전 안전 확인. 안전하면 즉시 True. unsafe면 held=True로 회복까지
        대기하다 — 회복→True(진행), hold_skip_seconds 초과→False(skip), 정지요청→False.
        Orchestrator의 fail-closed 게이트와 동형이되, '실패' 대신 '보류/스킵'으로 밤을 잇는다."""
        if self.safety_fn is None:
            return True
        saf = self._safety_now()
        if saf.get("state") in SAFE_TO_OBSERVE:
            return True
        timeout = float(self.cfg.get("nightrunner.hold_skip_seconds", 1800.0)
                        if self.cfg else 1800.0)
        poll = min(2.0, max(0.2, timeout))
        waited = 0.0
        self._set(held=True, reason=f"안전 보류({saf.get('state')})",
                  phase=f"안전 대기 #{item['plan_id']}")
        self.events.log("night_runner", f"안전 보류({saf.get('state')}) — 계획 "
                        f"#{item['plan_id']} 대기. 사유: {saf.get('reasons')}", "warn")
        while not self._stop.is_set():
            await asyncio.sleep(poll)
            waited += poll
            saf = self._safety_now()
            if saf.get("state") in SAFE_TO_OBSERVE:
                self._set(held=False, reason=None)
                self.events.log("night_runner",
                                f"안전 회복({saf.get('state')}) — 계획 #{item['plan_id']} 진행")
                return True
            if waited >= timeout:
                self._set(held=False, reason=None)
                self.events.log("night_runner",
                                f"안전 미회복 {waited:.0f}s — 계획 #{item['plan_id']} 스킵", "warn")
                return False
        self._set(held=False, reason=None)
        return False

    # ---------- 실행 루프 ----------

    async def _loop(self, plan_ids: list[int] | None, respect_slots: bool) -> None:
        """S3 — 큐를 순서대로 실행(start_plan→wait→분류). 한 계획 실패는 _run_one에서
        잡아 밤을 멈추지 않는다. 슬롯 대기(S5)·안전 게이트(S4)는 이후 단계에서 앞에 붙인다."""
        self._set(active=True, phase="큐 구성")
        done, failed = [], []
        try:
            queue, skipped = self._build_queue(plan_ids, respect_slots)
            self._set(queue=list(queue), skipped=skipped, done=done, failed=failed)
            self.events.log("night_runner",
                            f"야간 운영 큐 {len(queue)}개 (스킵 {len(skipped)})")
            for idx, item in enumerate(queue):
                if self._stop.is_set():
                    break
                self._set(current=item, queue=queue[idx + 1:],
                          phase=f"실행 #{item['plan_id']} {item['target']}")
                if not await self._safety_hold(item):   # unsafe 회복 못하면 skip
                    if self._stop.is_set():
                        break
                    skipped.append({**item, "reason": "안전 미회복(타임아웃)"})
                    self._set(skipped=skipped)
                    continue
                result = await self._run_one(item)
                (done if result == "done" else failed).append(item)
            self._set(phase="정지됨" if self._stop.is_set() else "완료")
            self.events.log("night_runner",
                            f"야간 운영 종료 — 완료 {len(done)} / 실패 {len(failed)} / 스킵 {len(skipped)}")
        except Exception as e:   # 루프 자체 보호(개별 계획 오류는 _run_one에서 흡수)
            self.events.log("night_runner", f"야간 운영 루프 오류: {e}", "error")
            self._set(phase="오류")
        finally:
            self._set(active=False, current=None)

    async def _run_one(self, item: dict) -> str:
        """한 계획을 Orchestrator로 실행하고 최종 plan 상태를 'done'/'failed'로 분류한다.
        실패해도 예외를 전파하지 않는다 — 밤은 다음 계획으로 계속된다."""
        pid = item["plan_id"]
        try:
            await self.orch.start_plan(pid)
            await self.orch.wait()
        except ActionError as e:
            self.events.log("night_runner", f"계획 #{pid} 시작 거부: {e}", "warn")
            return "failed"
        except Exception as e:   # noqa: BLE001 — 실행 오류로 밤 전체가 멈추면 안 됨
            self.events.log("night_runner", f"계획 #{pid} 실행 오류: {e}", "warn")
            return "failed"
        plan = self.meridian.get_plan(pid)
        st = (plan or {}).get("approval_status")
        ok = st == M.DONE
        self.events.log("night_runner",
                        f"계획 #{pid} {'완료' if ok else '미완료(' + str(st) + ')'}")
        return "done" if ok else "failed"
