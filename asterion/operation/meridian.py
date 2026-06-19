"""Meridian — 관측 계획 계층 (로드맵 §8.2: '무엇을 관측할지' 결정).

`ObservationPlan`(온톨로지에 이미 스키마 존재)을 CRUD·승인한다. 실제 실행은
Orchestrator가 *승인된(approved)* 계획을 집어 수행한다 — Meridian은 의도만
기록하고 장비를 건드리지 않는다. Target은 이름으로 upsert해 같은 대상의 여러
계획이 한 Target에 묶이게 한다(파일 아님, 대상 중심 — 원칙 #6).

계획 상태 흐름:
    draft → approved → (Orchestrator가) queued → running → done|failed|aborted
"""

from __future__ import annotations

import json
from typing import Any

from ..core.events import EventHub
from ..core.ontology import Db, ObservationPlan, Target, UserGoal

# 계획 승인/실행 상태 — Orchestrator와 공유.
DRAFT = "draft"
APPROVED = "approved"
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
ABORTED = "aborted"
CANCELLED = "cancelled"
VALID_STATUS = {DRAFT, APPROVED, QUEUED, RUNNING, DONE, FAILED, ABORTED, CANCELLED}


class Meridian:
    def __init__(self, db: Db, events: EventHub):
        self.db = db
        self.events = events

    # ---------- Target upsert ----------

    def _get_or_create_target(self, name: str, ra_hours: float | None,
                              dec_degs: float | None) -> int:
        name = name.strip()

        def _find(s):
            row = s.query(Target).filter(Target.name == name).first()
            return row.id if row else None

        tid = self.db.query(_find)
        if tid is not None:
            if ra_hours is not None or dec_degs is not None:
                def _upd(s):
                    row = s.get(Target, tid)
                    if ra_hours is not None:
                        row.ra_hours = ra_hours
                    if dec_degs is not None:
                        row.dec_degs = dec_degs
                self.db.update(_upd)
            return tid
        t = self.db.add(Target(name=name, ra_hours=ra_hours, dec_degs=dec_degs))
        return t.id

    # ---------- 직렬화 ----------

    @staticmethod
    def _plan_dict(p: ObservationPlan, t: Target | None) -> dict[str, Any]:
        try:
            params = json.loads(p.params_json or "{}")
        except Exception:
            params = {}
        return {
            "id": p.id, "kind": p.kind, "approval_status": p.approval_status,
            "created_utc": p.created_utc, "params": params,
            "target": ({"id": t.id, "name": t.name, "ra_hours": t.ra_hours,
                        "dec_degs": t.dec_degs} if t else None),
        }

    # ---------- 목표(UserGoal) — 스케줄러가 읽는 고수준 의도 ----------

    def set_goal(self, goal_type: str, params: dict | None = None,
                 required_filters: str = "", priority: int = 0) -> dict[str, Any]:
        """관측 목표 설정. params는 quality_thresholds_json에 보관(set·필터·노출 등).
        최신 row가 활성 목표(active_goal)."""
        g = self.db.add(UserGoal(
            goal_type=goal_type, required_filters=required_filters,
            quality_thresholds_json=json.dumps(params or {}, ensure_ascii=False),
            priority=priority))
        self.events.log("meridian", f"목표 설정 — {goal_type} {params or ''}")
        return {"id": g.id, "goal_type": goal_type, "params": params or {}}

    def active_goal(self) -> dict[str, Any] | None:
        def _q(s):
            row = s.query(UserGoal).order_by(UserGoal.id.desc()).first()
            if not row:
                return None
            try:
                params = json.loads(row.quality_thresholds_json or "{}")
            except Exception:
                params = {}
            return {"id": row.id, "goal_type": row.goal_type, "params": params,
                    "priority": row.priority}
        return self.db.query(_q)

    def done_target_names(self) -> set[str]:
        """이미 관측 완료(done 계획)된 대상 이름 집합 — 캠페인 '안 찍은 것' 판정용."""
        def _q(s):
            rows = (s.query(Target.name)
                    .join(ObservationPlan, ObservationPlan.target_id == Target.id)
                    .filter(ObservationPlan.approval_status == DONE).distinct().all())
            return {r[0] for r in rows}
        return self.db.query(_q)

    # ---------- CRUD ----------

    def create_plan(self, *, target_name: str, ra_hours: float | None = None,
                    dec_degs: float | None = None, strategy: dict,
                    kind: str = "science") -> dict[str, Any]:
        """관측 계획 생성 (status=draft). strategy 예:
        {filters:["g","r","i"], exposure_s:60, count_per_filter:10, binning:1,
         dither_arcsec:0, priority:0}."""
        tid = self._get_or_create_target(target_name, ra_hours, dec_degs)
        plan = self.db.add(ObservationPlan(
            target_id=tid, kind=kind,
            params_json=json.dumps(strategy, ensure_ascii=False),
            approval_status=DRAFT))
        self.events.log("meridian",
                        f"관측계획 #{plan.id} 생성 — {target_name} "
                        f"{strategy.get('filters')} {strategy.get('exposure_s')}s"
                        f"×{strategy.get('count_per_filter')}")
        return self.get_plan(plan.id)  # type: ignore[return-value]

    def get_plan(self, plan_id: int) -> dict[str, Any] | None:
        def _q(s):
            p = s.get(ObservationPlan, plan_id)
            if p is None:
                return None
            t = s.get(Target, p.target_id) if p.target_id else None
            return self._plan_dict(p, t)
        return self.db.query(_q)

    def list_plans(self, limit: int = 50,
                   status: str | None = None) -> list[dict[str, Any]]:
        def _q(s):
            q = s.query(ObservationPlan, Target).outerjoin(
                Target, ObservationPlan.target_id == Target.id)
            if status:
                q = q.filter(ObservationPlan.approval_status == status)
            rows = q.order_by(ObservationPlan.id.desc()).limit(limit).all()
            return [self._plan_dict(p, t) for p, t in rows]
        return self.db.query(_q)

    def set_status(self, plan_id: int, status: str) -> dict[str, Any]:
        if status not in VALID_STATUS:
            raise ValueError(f"알 수 없는 상태: {status}")

        def _upd(s):
            p = s.get(ObservationPlan, plan_id)
            if p is None:
                raise KeyError(plan_id)
            p.approval_status = status
        self.db.update(_upd)
        self.events.log("meridian", f"관측계획 #{plan_id} 상태 → {status}")
        return self.get_plan(plan_id)  # type: ignore[return-value]

    def approve_plan(self, plan_id: int) -> dict[str, Any]:
        return self.set_status(plan_id, APPROVED)

    def delete_plan(self, plan_id: int) -> bool:
        existed = {"v": False}

        def _del(s):
            p = s.get(ObservationPlan, plan_id)
            if p is not None:
                existed["v"] = True
                s.delete(p)
        self.db.update(_del)
        if existed["v"]:
            self.events.log("meridian", f"관측계획 #{plan_id} 삭제")
        return existed["v"]
