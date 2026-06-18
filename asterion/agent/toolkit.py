"""에이전트 도구 — ASTERION 서비스를 인프로세스로 부른다(앱 커넥터/사이드카 불필요).

각 도구는 OpenAI function 스키마(SPECS)로 LLM에 노출되고, call()이 이름→실제 호출로
디스패치한다. 실행계(goto/run/dome)는 ActionBus를 통과 → 안전게이트가 그대로 적용.
행성 위치는 astropy로 계산(지평선 아래면 사유와 함께 거부).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

MIN_ALT_DEG = 5.0
_BODIES = {"mercury", "venus", "mars", "jupiter", "saturn", "uranus",
           "neptune", "moon", "sun"}
_KO = {"수성": "mercury", "금성": "venus", "화성": "mars", "목성": "jupiter",
       "토성": "saturn", "천왕성": "uranus", "해왕성": "neptune",
       "달": "moon", "태양": "sun"}


class ToolKit:
    def __init__(self, *, cfg, snapshot_fn: Callable[[], dict], meridian,
                 orchestrator, bus, drivers):
        self.cfg = cfg
        self.snapshot = snapshot_fn
        self.meridian = meridian
        self.orch = orchestrator
        self.bus = bus
        self.drivers = drivers
        self.lat = float(cfg.get("site.latitude", 36.64))
        self.lon = float(cfg.get("site.longitude", 127.49))

    # ---------- LLM에 노출할 스키마 ----------

    @property
    def specs(self) -> list[dict]:
        def fn(name, desc, props=None, required=None):
            return {"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props or {},
                               "required": required or []}}}
        return [
            fn("get_status", "관측소 현재 상태(모드·안전상태+사유·마운트·돔·기상·시각). 무엇이든 하기 전에 확인."),
            fn("planet_position", "행성/달/해의 현재 고도·방위·관측가능 여부. 지평선 아래면 observable=false.",
               {"name": {"type": "string", "description": "예: '화성','mars','목성'"}}, ["name"]),
            fn("visible_planets", "지금 지평선 위(관측 가능)인 행성 목록 — 대안 제시용."),
            fn("goto_planet", "망원경을 행성으로 슬루('보여줘'). 지평선/한계 아래면 시도 없이 사유 반환.",
               {"name": {"type": "string"}}, ["name"]),
            fn("list_plans", "관측 계획(ObservationPlan) 목록.",
               {"status": {"type": "string", "description": "draft/approved/done 등(선택)"}}),
            fn("create_plan", "관측 계획 생성(draft). 행성은 ra/dec 비워도 됨.",
               {"target_name": {"type": "string"},
                "filters": {"type": "array", "items": {"type": "string"}},
                "exposure_s": {"type": "number"}, "count_per_filter": {"type": "integer"}},
               ["target_name", "filters"]),
            fn("run_plan", "승인된 계획을 Orchestrator로 실행. 사전조건 실패 시 사유 반환.",
               {"plan_id": {"type": "integer"}}, ["plan_id"]),
            fn("dome_shutter", "돔 셔터 열기/닫기. 수동 셔터면 거부됨.",
               {"open": {"type": "boolean"}}, ["open"]),
        ]

    # ---------- 디스패치 ----------

    async def call(self, name: str, args: dict) -> dict:
        try:
            handler = getattr(self, f"_t_{name}", None)
            if handler is None:
                return {"error": f"알 수 없는 도구: {name}"}
            return await handler(args)
        except Exception as exc:  # noqa: BLE001 — 도구 오류는 LLM에 전달해 설명하게
            return {"error": f"{name} 실행 오류: {exc}"}

    # ---------- 행성 계산 (astropy) ----------

    def _planet_altaz(self, name: str) -> dict:
        key = _KO.get((name or "").strip(), (name or "").strip().lower())
        if key not in _BODIES:
            return {"error": f"'{name}' 미지원. 지원: {', '.join(sorted(_BODIES))}"}
        from astropy.coordinates import EarthLocation, AltAz, get_body
        from astropy.time import Time
        import astropy.units as u
        loc = EarthLocation(lat=self.lat * u.deg, lon=self.lon * u.deg, height=0 * u.m)
        t = Time.now()
        body = get_body(key, t, loc)
        aa = body.transform_to(AltAz(obstime=t, location=loc))
        alt = float(aa.alt.deg)
        return {"body": key, "alt_deg": round(alt, 2), "az_deg": round(float(aa.az.deg), 2),
                "ra_hours": round(float(body.ra.hour), 5),
                "dec_degs": round(float(body.dec.deg), 5),
                "above_horizon": alt > 0, "observable": alt >= MIN_ALT_DEG,
                "note": ("관측 가능" if alt >= MIN_ALT_DEG else
                         f"고도 {alt:.1f}° — 한계({MIN_ALT_DEG}°) 아래라 goto 불가")}

    # ---------- 도구 구현 ----------

    async def _t_get_status(self, _: dict) -> dict:
        s = self.snapshot() or {}
        return {k: s.get(k) for k in ("mode", "time", "sun", "safety", "mount",
                                      "dome", "camera", "weather", "orchestrator")}

    async def _t_planet_position(self, a: dict) -> dict:
        return await asyncio.to_thread(self._planet_altaz, a.get("name", ""))

    async def _t_visible_planets(self, _: dict) -> dict:
        out = []
        for key in ("mercury", "venus", "mars", "jupiter", "saturn", "moon"):
            r = await asyncio.to_thread(self._planet_altaz, key)
            if r.get("observable"):
                out.append({"body": key, "alt_deg": r["alt_deg"], "az_deg": r["az_deg"]})
        return {"observable": out, "count": len(out)}

    async def _t_goto_planet(self, a: dict) -> dict:
        pos = await asyncio.to_thread(self._planet_altaz, a.get("name", ""))
        if "error" in pos:
            return pos
        if not pos["observable"]:
            return {"ok": False, "reason": pos["note"], "alt_deg": pos["alt_deg"]}
        mount = self.drivers["mount"]
        ra, dec = pos["ra_hours"], pos["dec_degs"]
        try:
            await self.bus.run("mount_goto_radec", actor="agent",
                               params={"ra_hours": ra, "dec_degs": dec,
                                       "target": pos["body"]},
                               func=lambda: asyncio.to_thread(mount.goto_radec, ra, dec))
        except Exception as exc:
            return {"ok": False, "reason": f"goto 거부/실패: {exc}", "target": pos["body"]}
        return {"ok": True, "target": pos["body"], "alt_deg": pos["alt_deg"],
                "az_deg": pos["az_deg"]}

    async def _t_list_plans(self, a: dict) -> dict:
        return {"plans": self.meridian.list_plans(status=a.get("status"))}

    async def _t_create_plan(self, a: dict) -> dict:
        return self.meridian.create_plan(
            target_name=a["target_name"], strategy={
                "filters": a.get("filters") or ["L"],
                "exposure_s": float(a.get("exposure_s", 60.0)),
                "count_per_filter": int(a.get("count_per_filter", 10)),
                "binning": 1, "dither_arcsec": 0.0, "priority": 0})

    async def _t_run_plan(self, a: dict) -> dict:
        try:
            await self.orch.start_plan(int(a["plan_id"]))
        except Exception as exc:
            return {"ok": False, "reason": f"실행 거부/실패: {exc}"}
        return {"ok": True, "plan_id": int(a["plan_id"])}

    async def _t_dome_shutter(self, a: dict) -> dict:
        dome = self.drivers["dome"]
        st = await asyncio.to_thread(dome.status)
        if not st.can_command_shutter:
            return {"ok": False, "reason": "수동 셔터 — SW로 개폐 불가"}
        fn = dome.open_shutter if a.get("open") else dome.close_shutter
        try:
            await self.bus.run("dome_shutter", actor="agent",
                               params={"open": bool(a.get("open"))},
                               func=lambda: asyncio.to_thread(fn))
        except Exception as exc:
            return {"ok": False, "reason": f"{exc}"}
        return {"ok": True, "open": bool(a.get("open"))}
