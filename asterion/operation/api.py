"""Operation 계층 REST 라우터 — app.py footprint를 작게 유지하려 분리.

app.py는 `app.include_router(build_operation_router(meridian, orch))` 한 줄만 추가한다.
계획(`/api/meridian/*`)과 실행 제어(`/api/orchestrator/*`)를 한 라우터에 담는다
(접두사 없이 전체 경로를 명시 — 두 네임스페이스를 한 번에 include하기 위함).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core import ephemeris
from .meridian import Meridian


class PlanCreateReq(BaseModel):
    target_name: str
    ra: str | None = None          # "5.59" | "05:35:17" | "5h35m17s" (선택)
    dec: str | None = None         # "-5.39" | "-05:23:28"
    filters: list[str]
    exposure_s: float = 60.0
    count_per_filter: int = 1
    binning: int = 1
    dither_arcsec: float = 0.0
    priority: int = 0


def build_operation_router(meridian: Meridian, orchestrator: Any = None) -> APIRouter:
    router = APIRouter(tags=["operation"])

    # ---------- 계획 (Meridian) ----------

    @router.post("/api/meridian/plans")
    async def create_plan(req: PlanCreateReq):
        ra_h = dec_d = None
        if req.ra is not None and req.dec is not None:
            try:
                ra_h = ephemeris.parse_ra_hours(req.ra)
                dec_d = ephemeris.parse_dec_degs(req.dec)
            except Exception as exc:
                raise HTTPException(400, f"RA/Dec 해석 실패: {exc}")
        if not req.filters:
            raise HTTPException(400, "filters는 최소 1개 필요")
        if req.count_per_filter < 1:
            raise HTTPException(400, "count_per_filter는 1 이상")
        strategy = {
            "filters": req.filters, "exposure_s": req.exposure_s,
            "count_per_filter": req.count_per_filter, "binning": req.binning,
            "dither_arcsec": req.dither_arcsec, "priority": req.priority,
        }
        return meridian.create_plan(target_name=req.target_name,
                                    ra_hours=ra_h, dec_degs=dec_d,
                                    strategy=strategy)

    @router.get("/api/meridian/plans")
    async def list_plans(limit: int = 50, status: str | None = None):
        return meridian.list_plans(limit=limit, status=status)

    @router.get("/api/meridian/plans/{plan_id}")
    async def get_plan(plan_id: int):
        plan = meridian.get_plan(plan_id)
        if plan is None:
            raise HTTPException(404, f"계획 #{plan_id} 없음")
        return plan

    @router.post("/api/meridian/plans/{plan_id}/approve")
    async def approve_plan(plan_id: int):
        if meridian.get_plan(plan_id) is None:
            raise HTTPException(404, f"계획 #{plan_id} 없음")
        return meridian.approve_plan(plan_id)

    @router.delete("/api/meridian/plans/{plan_id}")
    async def delete_plan(plan_id: int):
        if not meridian.delete_plan(plan_id):
            raise HTTPException(404, f"계획 #{plan_id} 없음")
        return {"deleted": plan_id}

    @router.get("/api/meridian/goal")
    async def get_goal():
        """현재 활성 관측 목표(UserGoal). PLAN 탭 'AI 야간 계획' 헤더 표시용."""
        return meridian.active_goal() or {}

    # ---------- 실행 제어 (Orchestrator) ----------

    @router.post("/api/meridian/plans/{plan_id}/run")
    async def run_plan(plan_id: int):
        """승인된 계획을 Orchestrator로 실행 시작. 사전조건(승인/연결/안전/타 세션
        유휴) 실패 시 ActionError → 409. 진행상황은 /api/status의 orchestrator에."""
        if orchestrator is None:
            raise HTTPException(503, "Orchestrator 미가용")
        if meridian.get_plan(plan_id) is None:
            raise HTTPException(404, f"계획 #{plan_id} 없음")
        await orchestrator.start_plan(plan_id)
        return {"started": True, "plan_id": plan_id}

    @router.post("/api/orchestrator/stop")
    async def orchestrator_stop():
        if orchestrator is None:
            raise HTTPException(503, "Orchestrator 미가용")
        await orchestrator.request_stop()
        return {"stopping": True}

    @router.get("/api/orchestrator/status")
    async def orchestrator_status():
        if orchestrator is None:
            raise HTTPException(503, "Orchestrator 미가용")
        return orchestrator.status_dict()

    return router
