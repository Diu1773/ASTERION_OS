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
    def _now_kst():
        from datetime import datetime, timedelta, timezone
        return datetime.now(timezone.utc) + timedelta(hours=9)

    def _slot_dt(self, hhmm, now_kst, grace_h: float = 6.0):
        """슬롯 'HH:MM'을 'now-grace 이후 가장 이른 발생'에 앵커한 KST datetime(없으면 None).
        한낮 실행도 견고 — 저녁 슬롯이 12h+ 과거로 잡히던 야간분 wrap을 회피한다."""
        from datetime import timedelta
        if self._slot_min(hhmm) is None:
            return None
        p = str(hhmm).split(":")
        base = now_kst.replace(hour=int(p[0]), minute=int(p[1]), second=0, microsecond=0)
        cutoff = now_kst - timedelta(hours=grace_h)
        cands = sorted(base + timedelta(days=d) for d in (-1, 0, 1))
        for c in cands:
            if c >= cutoff:
                return c
        return cands[-1]

    # ---------- 큐 구성 (S2) ----------

    def _build_queue(self, plan_ids: list[int] | None):
        """승인 계획(또는 plan_ids)을 slot_start(야간분) 순으로 정렬한 실행 큐. 슬롯 시각
        없는 계획은 뒤로. slot_end 경과 skip은 S5 타이밍(_await_slot)에서 datetime으로 판정."""
        if plan_ids:
            plans = [p for p in (self.meridian.get_plan(i) for i in plan_ids) if p]
        else:
            plans = self.meridian.list_plans(status=M.APPROVED)
        deco = []
        for p in plans:
            pr = p.get("params") or {}
            t = p.get("target") or {}
            deco.append((self._slot_min(pr.get("slot_start")), p, pr, t))
        # slot_start 순(없으면 뒤로), 동률은 id로 안정 정렬
        deco.sort(key=lambda d: (d[0] is None, d[0] or 0, d[1].get("id") or 0))
        return [{"plan_id": p.get("id"), "target": t.get("name") or "—",
                 "slot_start": pr.get("slot_start"), "slot_end": pr.get("slot_end")}
                for _, p, pr, t in deco]

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
        queue = self._build_queue(plan_ids)   # 동기 — 큐를 즉시 노출(start 직후 status/응답에 보임)
        self._set(queue=list(queue), active=True, phase="시작")
        self._task = asyncio.create_task(
            self._loop(queue, respect_slots), name="night-runner")

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

    # ---------- 슬롯 타이밍 (S5) ----------

    async def _await_slot(self, item: dict) -> str:
        """슬롯 진입 타이밍. slot_start까지 대기(과거(grace내)면 즉시 'run'), slot_end가 이미
        지났으면 'skip', 정지요청이면 'stop'. 슬롯 시각 없는 계획은 즉시 'run'."""
        from datetime import timedelta
        s, e = item.get("slot_start"), item.get("slot_end")
        sm = self._slot_min(s)
        if sm is None:
            return "run"
        now = self._now_kst()
        start_dt = self._slot_dt(s, now)
        em = self._slot_min(e)
        dur = (em - sm) if (em is not None and em >= sm) else None
        end_dt = (start_dt + timedelta(minutes=dur)) if (dur is not None and start_dt) else None
        if end_dt is not None and end_dt <= now:
            return "skip"
        poll = float(self.cfg.get("nightrunner.poll_seconds", 5.0) if self.cfg else 5.0)
        while not self._stop.is_set():
            now = self._now_kst()
            remain = (start_dt - now).total_seconds()
            if remain <= 0:
                return "run"
            self._set(phase=f"슬롯 대기 #{item['plan_id']} {s} (남은 {int(remain)}s)")
            await asyncio.sleep(min(remain, poll))
        return "stop"

    # ---------- 실행 루프 ----------

    async def _loop(self, queue: list[dict], respect_slots: bool) -> None:
        """큐(start에서 동기 구성)를 순서대로: (respect_slots면) 슬롯 대기 → 안전 게이트 →
        실행 → 분류. 한 계획 실패/스킵은 흡수해 밤을 멈추지 않는다. _stop이면 잔여 중단."""
        done, failed, skipped = [], [], []
        try:
            self._set(skipped=skipped, done=done, failed=failed)
            self.events.log("night_runner", f"야간 운영 큐 {len(queue)}개")
            for idx, item in enumerate(queue):
                if self._stop.is_set():
                    break
                self._set(current=item, queue=queue[idx + 1:],
                          phase=f"준비 #{item['plan_id']} {item['target']}")
                if respect_slots:   # 슬롯 시각까지 대기 / 종료경과면 skip
                    action = await self._await_slot(item)
                    if action == "stop":
                        break
                    if action == "skip":
                        skipped.append({**item, "reason": "슬롯 종료 시각 경과"})
                        self._set(skipped=skipped)
                        continue
                if not await self._safety_hold(item):   # unsafe 회복 못하면 skip
                    if self._stop.is_set():
                        break
                    skipped.append({**item, "reason": "안전 미회복(타임아웃)"})
                    self._set(skipped=skipped)
                    continue
                self._set(phase=f"실행 #{item['plan_id']} {item['target']}")
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
