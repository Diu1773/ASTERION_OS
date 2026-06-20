"""멀티나잇 캠페인 — 대상군을 여러 밤에 걸쳐 완주. 정의 영속 + 진행률/예상 소요밤 계산.

캠페인은 '무엇을 찍을지'(set/종류/전략)를 한 번 정의해 두면, 매일 밤 plan_night이 관측
이력에서 '안 찍은 것'만 골라 비겹침 시간표로 배분한다. 여기서는 정의(Campaign)를 저장하고
카탈로그 멤버 대비 완료/잔여/퍼센트/예상 소요밤을 집계한다(스케줄러는 기존 _night_plan 재사용)."""
from __future__ import annotations

import json
import math

from .dso_catalog import DSO, TYPE_KO, match_type
from .ontology import Campaign, utc_iso

_MESSIER = ("messier", "메시에")
_ALL = ("all", "전체", "")
# 복수형/별칭 → match_type이 아는 키워드(부분일치). 'galaxies'엔 'galaxy'가 없어 정규화 필요.
_SET_ALIAS = {"galaxies": "galaxy", "갤럭시": "galaxy", "nebulae": "nebula",
              "nebulas": "nebula", "globulars": "globular", "globular clusters": "globular",
              "planetaries": "planetary", "planetary nebulae": "planetary"}


def _label(o: dict) -> str:
    return (o["name"] or o["id"]) + (f" ({o['id']})" if o["name"] else "")


def _resolve(target_set: str, type_filter: str = ""):
    """(idpred, types) — set/종류필터를 카탈로그 술어로. 메시에=M*, 그 외=match_type(별칭 정규화)."""
    s = (target_set or "all").strip().lower()
    idpred = (lambda o: o["id"].startswith("M")) if s in _MESSIER else None
    if type_filter:
        types = match_type(type_filter)
    elif s in (_MESSIER + _ALL):
        types = None
    else:
        types = match_type(_SET_ALIAS.get(s, target_set))
    return idpred, types


def members(target_set: str, type_filter: str = "") -> list[dict]:
    """카탈로그에서 캠페인 대상군 멤버. set=messier→M*, 그 외/종류필터→match_type(별칭 정규화)."""
    idpred, types = _resolve(target_set, type_filter)
    return [o for o in DSO
            if (not idpred or idpred(o)) and (not types or o["t"] in types)]


class CampaignManager:
    """Campaign CRUD + 진행률. 스케줄러 입력(types/idpred/exclude/strategy)도 만들어 준다."""

    def __init__(self, db, meridian):
        self.db = db
        self.meridian = meridian

    # ---------- CRUD ----------
    def create(self, *, name, goal="", target_set="all", type_filter="",
               profile="", strategy=None, per_night=6, deadline_utc=None) -> dict:
        c = self.db.add(Campaign(
            name=str(name)[:128], goal=str(goal or ""), target_set=str(target_set or "all"),
            type_filter=str(type_filter or ""), profile=str(profile or ""),
            strategy_json=json.dumps(strategy or {}, ensure_ascii=False),
            per_night=max(1, int(per_night or 6)),
            deadline_utc=(str(deadline_utc) if deadline_utc else None)))
        return self.progress(c.id)

    def get(self, cid: int) -> dict | None:
        return self.db.get(Campaign, cid)   # Db.get은 detached dict 반환

    def list(self) -> list[dict]:
        rows = self.db.recent(Campaign, 100)
        return [self.progress(r["id"]) for r in rows]

    def set_status(self, cid: int, status: str) -> dict | None:
        if status not in ("active", "paused", "done"):
            raise ValueError("status는 active/paused/done")

        def _u(s):
            row = s.get(Campaign, cid)
            if row:
                row.status = status
                row.updated_utc = utc_iso()
        self.db.update(_u)
        return self.progress(cid)

    # ---------- 진행률 ----------
    def _done_set(self) -> set[str]:
        try:
            return self.meridian.done_target_names() or set()
        except Exception:
            return set()

    def progress(self, cid: int) -> dict | None:
        row = self.db.get(Campaign, cid)   # detached dict
        if row is None:
            return None
        mem = members(row["target_set"], row["type_filter"])
        done = self._done_set()
        done_n, remaining = 0, []
        for o in mem:
            # 이력 매칭은 라벨/이름/ID 어느 형태든 허용(견고하게)
            if _label(o) in done or (o["name"] and o["name"] in done) or o["id"] in done:
                done_n += 1
            else:
                remaining.append({"id": o["id"], "label": _label(o),
                                  "type": TYPE_KO.get(o["t"], o["t"]), "mag": o["mag"]})
        total = len(mem)
        per = max(1, row["per_night"])
        est = math.ceil(len(remaining) / per) if remaining else 0
        return {
            "id": row["id"], "name": row["name"], "goal": row["goal"],
            "target_set": row["target_set"], "type_filter": row["type_filter"],
            "profile": row["profile"], "status": row["status"],
            "per_night": row["per_night"], "deadline_utc": row["deadline_utc"],
            "total": total, "done": done_n, "remaining": len(remaining),
            "percent": round(100.0 * done_n / total, 1) if total else 0.0,
            "est_nights": est,
            "remaining_sample": remaining[:20],
            "created_utc": row["created_utc"], "updated_utc": row["updated_utc"],
        }

    # ---------- 스케줄러 입력 ----------
    def plan_inputs(self, cid: int) -> dict | None:
        """오늘 밤 plan_night에 넘길 입력 — 대상군 멤버십(idpred)·종류·완료제외·전략."""
        row = self.db.get(Campaign, cid)   # detached dict
        if row is None:
            return None
        idpred, types = _resolve(row["target_set"], row["type_filter"])
        try:
            strat = json.loads(row["strategy_json"] or "{}")
        except (ValueError, TypeError):
            strat = {}
        strategy = {
            "filters": strat.get("filters") or ["L"],
            "exposure_s": float(strat.get("exposure_s", 120.0)),
            "count_per_filter": int(strat.get("count_per_filter", 10)),
            "binning": 1, "dither_arcsec": 0.0, "priority": 0,
        }
        return {"types": types, "idpred": idpred,
                "exclude": self._done_set(), "strategy": strategy,
                "profile": row["profile"] or None}
