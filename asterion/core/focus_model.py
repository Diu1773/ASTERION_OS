"""온도·고도 기반 포커스 보정 모델 (PWI3식 temperature/altitude focus compensation).

촬영 중 튜브·주경의 열팽창(온도)·미러 플렉서(고도)로 최적 초점이 이동한다. 과거 오토포커스
결과(FocusRun: 위치 + 환경)로부터 선형 모델을 최소제곱 적합한다:

    position = c0 + c_temp · T  (+ c_alt · alt)

현재 온도/고도에서 최적 위치를 예측해 촬영 중 포커서를 미세 보정 — 매 프레임 풀 오토포커스를
돌리지 않고 FWHM을 유지(PWI3의 자동 내삽과 동형). 온도는 보통 지배항(steps/°C), 고도는 미러
플렉서 보정(선택).

적합/예측 함수(fit_focus_model/predict_position)는 *순수 함수*(I/O 없음)라 표본만 있으면
단독 테스트된다. 그 위에 DB 적재·필터 정규화·촬영 중 적용을 담는 FocusModelService와
apply_focus_model을 둔다.
"""

from __future__ import annotations

import asyncio
import json

from .focus_offset import filter_focus_offset
from .ontology import FocusRun


def fit_focus_model(points, *, use_altitude: bool = True,
                    min_points: int = 4, min_temp_span: float = 2.0) -> dict:
    """(position, temp_c, alt_deg) 표본을 선형 최소제곱 적합.

    points: [(pos, temp_c, alt_deg), ...]  — pos는 *필터 정규화된*(기준 필터 공간) 위치 권장.
    온도가 없는(None) 표본은 제외. 데이터가 min_points 미만이거나 온도 변화폭이 min_temp_span
    미만이면 적합 불가(ok=False) — 온도 계수가 의미 없어 fail-safe(모델 미적용).

    반환: {ok, n, c0, c_temp, c_alt, use_altitude, temp_span, rms, reason}
    """
    pts = [(float(p), float(t), (None if a is None else float(a)))
           for (p, t, a) in points if p is not None and t is not None]
    if len(pts) < min_points:
        return {"ok": False, "n": len(pts), "reason": f"데이터 부족 (<{min_points}점)"}
    temps = [t for _, t, _ in pts]
    span = max(temps) - min(temps)
    if span < min_temp_span:
        return {"ok": False, "n": len(pts), "temp_span": span,
                "reason": f"온도 변화폭 부족 (<{min_temp_span}°C) — 온도 계수 신뢰 불가"}

    import numpy as np
    # 고도는 모든 표본에 값이 있고 실제 변화가 있을 때만 항으로 포함(아니면 온도만).
    use_alt = (use_altitude and all(a is not None for _, _, a in pts)
               and (max(a for _, _, a in pts) - min(a for _, _, a in pts)) >= 5.0)
    if use_alt:
        a_mat = np.array([[1.0, t, a] for _, t, a in pts])
    else:
        a_mat = np.array([[1.0, t] for _, t, _ in pts])
    y = np.array([p for p, _, _ in pts])
    coef, *_ = np.linalg.lstsq(a_mat, y, rcond=None)
    rms = float(np.sqrt(np.mean((y - a_mat @ coef) ** 2)))
    return {
        "ok": True, "n": len(pts),
        "c0": float(coef[0]), "c_temp": float(coef[1]),
        "c_alt": float(coef[2]) if use_alt else 0.0,
        "use_altitude": bool(use_alt),
        "temp_span": float(span), "rms": rms,
    }


def predict_position(model: dict, temp_c, alt_deg=None):
    """모델 + 현재 온도(·고도)로 (필터 정규화 공간) 최적 위치 예측. 모델 없음/온도 없음이면 None."""
    if not model or not model.get("ok") or temp_c is None:
        return None
    pos = model["c0"] + model["c_temp"] * float(temp_c)
    if model.get("use_altitude") and alt_deg is not None:
        pos += model["c_alt"] * float(alt_deg)
    return pos


# ----- DB 기반 서비스 + 촬영 중 적용 (상위 계층) -----

# 온도 소스별 폴백 순서 — 선택 소스가 결측이면 다음으로.
_TEMP_FALLBACK = {
    "focuser": ("focuser_temp_c", "ambient_temp_c", "primary_temp_c"),
    "ambient": ("ambient_temp_c", "focuser_temp_c", "primary_temp_c"),
    "primary": ("primary_temp_c", "focuser_temp_c", "ambient_temp_c"),
}


def capture_focus_env(drivers: dict, *, include_weather: bool = True) -> dict:
    """현재 환경(포커서/외기 온도·고도)을 best-effort 수집 — 포커스 모델 입력. 예외는 삼킨다.

    include_weather=False면 외기(weather COM 트랜잭션)를 건너뛴다 — 촬영 중 포커서온도만 쓸 때
    프레임마다 기상 폴링 부담을 피한다.
    """
    env: dict = {}
    foc = drivers.get("focuser")
    if foc is not None:
        try:
            st = foc.status()
            if getattr(st, "temperature", None) is not None:
                env["focuser_temp_c"] = float(st.temperature)
        except Exception:
            pass
    mount = drivers.get("mount")
    if mount is not None:
        try:
            st = mount.status()
            if getattr(st, "alt_degs", None) is not None:
                env["altitude_deg"] = float(st.alt_degs)
        except Exception:
            pass
    if include_weather:
        w = drivers.get("weather")
        if w is not None:
            try:
                st = w.read()
                if getattr(st, "temp_c", None) is not None:
                    env["ambient_temp_c"] = float(st.temp_c)
            except Exception:
                pass
    return env


class FocusModelService:
    """FocusRun 이력으로 온도/고도→포커스 위치 모델을 적합하고 현재 환경에서 위치를 예측.

    - 적합은 *기준 필터 공간*에서 한다(각 run의 필터 오프셋 제거) → 예측 시 현재 필터 오프셋을 더함.
      (절대 초점은 모델·오토포커스가, 필터 간 상대 오프셋은 focus_offset이 담당 — 중복 보정 방지.)
    - mode='auto'(기본): 이력 최소제곱 적합. 'manual': 설정 계수(steps/°C)+최근 앵커로 c0 유도
      (PWI3에 알려진 온도계수를 직접 넣는 용법).
    - 데이터/온도폭 부족 시 model.ok=False → 상위가 보정을 건너뛴다(fail-safe, 막 움직이지 않음).
    """

    def __init__(self, cfg, db, *, history: int = 80):
        self.cfg = cfg
        self.db = db
        self.history = history
        self.model: dict = {"ok": False, "reason": "미적합"}
        self.meta: dict = {}

    def _g(self, key, default=None):
        return self.cfg.get(f"focus_model.{key}", default)

    @property
    def enabled(self) -> bool:
        return bool(self._g("enabled", False))

    def env_temp(self, env: dict):
        """설정된 온도 소스(focuser/ambient/primary)를 우선, 결측이면 폴백 순서로 한 값을 고른다."""
        order = _TEMP_FALLBACK.get(str(self._g("temp_source", "focuser")),
                                   _TEMP_FALLBACK["focuser"])
        for k in order:
            v = env.get(k)
            if v is not None:
                return float(v)
        return None

    def filter_index(self, name: str) -> int:
        filters = self.cfg.get("setup.filterwheel.filters", None)
        if isinstance(filters, list):
            for i, f in enumerate(filters):
                if isinstance(f, dict) and f.get("name") == name:
                    return i
        return 0

    def _points(self):
        """FocusRun 이력 → [(필터정규화 위치, 온도, 고도)]. recent()는 최신순 dict 리스트."""
        runs = self.db.recent(FocusRun, self.history)
        pts = []
        for r in runs:
            pos = r.get("focuser_position")
            if pos is None:
                continue
            try:
                env = json.loads(r.get("environment_json") or "{}")
            except (ValueError, TypeError):
                env = {}
            off = filter_focus_offset(self.cfg, self.filter_index(r.get("filter_name", "")))
            pts.append((float(pos) - off, self.env_temp(env), env.get("altitude_deg")))
        return pts, runs

    def refit(self) -> dict:
        mode = str(self._g("mode", "auto"))
        pts, runs = self._points()
        if mode == "manual":
            self.model = self._manual_model(pts)
        else:
            self.model = fit_focus_model(
                pts,
                use_altitude=bool(self._g("use_altitude", True)),
                min_points=int(self._g("min_points", 4)),
                min_temp_span=float(self._g("min_temp_span_c", 2.0)))
            if not self.model.get("ok") and self._g("c_temp_steps_per_c") is not None:
                self.model = self._manual_model(pts)   # auto 실패 → 수동 계수 폴백
        self.meta = {"mode": mode, "n_runs": len(runs),
                     "temp_source": str(self._g("temp_source", "focuser"))}
        return self.model

    def _manual_model(self, pts) -> dict:
        ct = self._g("c_temp_steps_per_c")
        if ct is None:
            return {"ok": False, "reason": "manual 모드인데 c_temp_steps_per_c 미설정"}
        try:
            ct = float(ct)
            ca = float(self._g("c_alt_steps_per_deg", 0.0) or 0.0)
        except (TypeError, ValueError):
            return {"ok": False, "reason": "manual 계수 파싱 실패"}
        anchor = next(((p, t, a) for (p, t, a) in pts if t is not None), None)
        if anchor is None:
            return {"ok": False, "reason": "앵커(온도 있는 최근 오토포커스) 없음"}
        p0, t0, a0 = anchor
        use_alt = ca != 0.0 and a0 is not None
        c0 = p0 - ct * t0 - (ca * a0 if use_alt else 0.0)
        return {"ok": True, "manual": True, "n": 1, "c0": c0, "c_temp": ct,
                "c_alt": ca, "use_altitude": use_alt, "temp_span": 0.0, "rms": 0.0}

    def predict(self, temp_c, alt_deg=None, filter_name: str = ""):
        """현재 온도/고도에서 *현재 필터* 최적 위치(절대 steps). 예측 불가면 None."""
        base = predict_position(self.model, temp_c, alt_deg)
        if base is None:
            return None
        return base + filter_focus_offset(self.cfg, self.filter_index(filter_name))

    def status_dict(self) -> dict:
        return {"enabled": self.enabled, "model": self.model, "meta": self.meta,
                "deadband_steps": int(self._g("deadband_steps", 10)),
                "max_step_steps": int(self._g("max_step_steps", 0) or 0),
                "recompute_every_frames": int(self._g("recompute_every_frames", 5) or 0)}


async def apply_focus_model(cfg, drivers, run_action, service, *,
                            filter_name: str = "") -> dict | None:
    """모델이 예측한 위치로 포커서를 미세 보정(촬영 중). best-effort.

    deadband 미만 이동은 생략, max_step 초과 이동은 클램프(센서/모델 이상 시 폭주 방지).
    비활성/미적합/온도 결측/포커서 미연결이면 None을 돌려준다(아무 것도 안 함).
    """
    if not service.enabled or not service.model.get("ok"):
        return None
    source = str(cfg.get("focus_model.temp_source", "focuser"))
    env = await asyncio.to_thread(capture_focus_env, drivers,
                                  include_weather=(source != "focuser"))
    temp = service.env_temp(env)
    if temp is None:
        return None
    predicted = service.predict(temp, env.get("altitude_deg"), filter_name)
    if predicted is None:
        return None
    foc = drivers.get("focuser")
    if foc is None:
        return None
    st = await asyncio.to_thread(foc.status)
    if not getattr(st, "connected", False) or st.position is None:
        return None
    cur = int(st.position)
    max_pos = int(getattr(st, "max_position", 60000) or 60000)
    target = max(0, min(max_pos, int(round(predicted))))
    delta = target - cur
    alt = env.get("altitude_deg")
    deadband = int(cfg.get("focus_model.deadband_steps", 10))
    if abs(delta) < deadband:
        return {"applied": False, "delta": delta, "temp_c": temp, "alt_deg": alt}
    max_step = int(cfg.get("focus_model.max_step_steps", 0) or 0)
    clamped = False
    if max_step > 0 and abs(delta) > max_step:
        target = max(0, min(max_pos, cur + (max_step if delta > 0 else -max_step)))
        delta = target - cur
        clamped = True
    await run_action(
        "focus_model_apply",
        {"target": target, "delta": delta, "temp_c": round(temp, 2),
         "alt_deg": (round(alt, 1) if alt is not None else None),
         "filter": filter_name, "clamped": clamped},
        lambda: asyncio.to_thread(foc.move_to, target))
    return {"applied": True, "target": target, "delta": delta,
            "temp_c": temp, "alt_deg": alt, "clamped": clamped}
