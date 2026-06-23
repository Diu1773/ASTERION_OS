"""ForecastWatch — 강수 예보 조기경보(선제 '경고'만, 물리행동 0).

설계 원칙(대화로 합의): 예보는 확률이라 그걸로 돔을 닫거나 파킹하지 않는다 — 틀리면
멀쩡한 밤을 날린다(강수확률 50%면 절반은 헛고생). 예보의 역할은 '사람을 미리 깨워'
(특히 자동으로 못 닫는 수동 셔터의) 닫기·마무리 시간을 벌어주는 것뿐. **실제 닫기는
센서 감지가 한다**(safety.evaluate → EMERGENCY_CLOSE → DomeGuard). 이 모듈은 alert만.

샘플러 틱마다 호출돼도 무해하다 — 예보는 정시 버킷 캐시(ForecastService)라 재계산이
없고, AlertManager.fire 쿨다운이 스팸을 막는다. config [weather.forecast_alert]로 끈다.
추가형 — 안전 판정/액추에이터는 건드리지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


class ForecastWatch:
    def __init__(self, forecast: Any, alert_mgr: Any, cfg: Any):
        self.forecast = forecast        # ForecastService (upcoming/risk_at)
        self.alert_mgr = alert_mgr      # AlertManager (fire)
        self.cfg = cfg

    def _enabled(self) -> bool:
        return bool(self.cfg.get("weather.forecast_alert.enabled", True))

    def peak_risk(self, lead_hours: float) -> tuple[float, str | None]:
        """앞으로 lead_hours 내 시간별 예보의 강수확률 최대치와 그 시각(ISO).
        예보가 없거나 오류면 (0.0, None). 약간의 과거 그레이스(30분)를 포함해
        '바로 지금부터 임박한' 강수도 잡는다."""
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=lead_hours)
        floor = now - timedelta(minutes=30)
        try:
            fc = self.forecast.upcoming(int(lead_hours) + 1)   # 정시 버킷 캐시
        except Exception:
            return 0.0, None
        peak, at = 0.0, None
        for f in fc or []:
            try:
                t = datetime.fromisoformat(f.time_utc)
            except (ValueError, TypeError, AttributeError):
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            if floor <= t <= horizon and float(f.precip_prob) > peak:
                peak, at = float(f.precip_prob), f.time_utc
        return peak, at

    def check(self) -> dict | None:
        """예보 강수확률 최대치가 임계 이상이면 경보 발화(쿨다운 통과 시 rec, 아니면 None).
        **물리행동 없음** — 사람을 미리 깨우는 alert 한 건. 실제 닫기는 센서가 한다."""
        if not self._enabled():
            return None
        lead = float(self.cfg.get("weather.forecast_alert.lead_hours", 2.0))
        thr = float(self.cfg.get("weather.forecast_alert.precip_threshold", 0.5))
        cd = float(self.cfg.get("weather.forecast_alert.cooldown_seconds", 1800.0))
        peak, _at = self.peak_risk(lead)
        if peak < thr:
            return None
        return self.alert_mgr.fire(
            "weather_forecast_rain", "warn",
            "강수 예보 — 사전 대비 권고",
            f"향후 {lead:.0f}h 내 강수확률 최대 {peak * 100:.0f}% (예보). "
            "닫기 준비·관측 마무리 권고 — 실제 닫기는 센서 감지 시 자동.",
            cooldown_s=cd)
