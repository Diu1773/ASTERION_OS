"""Orchestrator 후크 — plate-solve / autofocus (로드맵 §8.3 단계 5·6).

후크는 *주입형 콜러블*이다. Orchestrator는 인터페이스만 알고, 실제 알고리즘은
밖에서 주입한다 — SIM(아래 즉시성공 stub), 추후 ASTAP/astrometry.net 솔버나
HFR 기반 V-curve 오토포커스로 교체. 주입 안 하면 Orchestrator는 ActionLog만
남기는 no-op으로 동작한다(Ph7-2/3 동작 보존).

규약:
  platesolve_fn(ra_hours, dec_degs) -> dict | None
      성공 시 {"solved": True, "ra_hours", "dec_degs", "error_arcsec"} 류.
  autofocus_fn() -> dict | None
      성공 시 {"best_position", "best_fwhm", "confidence"} 류 (Orchestrator가
      이 결과로 FocusRun 1행을 적재한다).
동기 함수다 — Orchestrator가 asyncio.to_thread로 감싸 호출한다.
"""

from __future__ import annotations


def sim_platesolve(ra_hours: float, dec_degs: float) -> dict:
    """SIM 플레이트 솔브 — 입력 좌표를 그대로 '해결됨'으로 즉시 반환(작은 오차)."""
    return {"solved": True, "ra_hours": round(ra_hours, 6),
            "dec_degs": round(dec_degs, 6), "error_arcsec": 3.2, "stub": False}


def sim_autofocus() -> dict:
    """SIM 오토포커스 — 즉시 성공, 그럴듯한 초점 결과 반환."""
    return {"best_position": 30000, "best_fwhm": 2.4, "confidence": 0.9,
            "stub": False}
