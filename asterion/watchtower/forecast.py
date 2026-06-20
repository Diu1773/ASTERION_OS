"""기상 예보 — 스케줄러 게이팅·대시보드용. provider 인터페이스 + Sim(합성) + KMA 어댑터(config-gated).

실 기상청 단기예보(동네예보) API는 키/좌표가 config에 있을 때만 활성(위성 프록시와 동일 패턴).
없으면 SimForecastProvider(합성 일주 곡선)로 동작 — 무인 운영 파이프라인을 막지 않는다.
스케줄러는 관측 시간대 강수확률을 merit 페널티(+ 고위험 하드스킵)로 반영한다."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone


class NotConfigured(RuntimeError):
    """실 예보 제공자가 설정(키/좌표)되지 않음 — Sim으로 폴백."""


def _parse(iso: str) -> datetime:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class HourlyForecast:
    time_utc: str       # ISO8601 (정시)
    cloud_frac: float   # 0..1
    precip_prob: float  # 0..1
    wind_ms: float
    temp_c: float

    def as_dict(self) -> dict:
        return asdict(self)


class ForecastProvider:
    name = "base"

    def upcoming(self, start: datetime, hours: int) -> list[HourlyForecast]:
        raise NotImplementedError


class SimForecastProvider(ForecastProvider):
    """합성 예보 — 결정적(시각 기반). 낮 구름↑·새벽 맑음의 단순 일주 곡선 + 완만한 변동.
    외부 API 없이도 게이팅 로직·대시보드를 검증·운용할 수 있다."""
    name = "sim"

    def upcoming(self, start: datetime, hours: int) -> list[HourlyForecast]:
        out = []
        for h in range(hours + 1):
            t = start + timedelta(hours=h)
            hod = t.hour + t.minute / 60.0
            cloud = min(1.0, max(0.0, 0.35 + 0.30 * math.cos((hod - 14) / 24 * 2 * math.pi)))
            precip = max(0.0, (cloud - 0.6) * 1.5)   # 구름 0.6↑부터 강수확률 상승
            out.append(HourlyForecast(
                time_utc=t.replace(microsecond=0).isoformat(),
                cloud_frac=round(cloud, 3),
                precip_prob=round(min(1.0, precip), 3),
                wind_ms=round(2.0 + 1.5 * math.sin(hod / 24 * 2 * math.pi), 1),
                temp_c=round(10 + 6 * math.sin((hod - 9) / 24 * 2 * math.pi), 1)))
        return out


class KmaForecastProvider(ForecastProvider):
    """기상청 단기예보 어댑터 — config [weather.kma_forecast] 키/격자가 있을 때만 활성.
    실 HTTP 호출은 키 확보 후 구현(위성 프록시 패턴). 미설정이면 NotConfigured."""
    name = "kma"

    def __init__(self, cfg):
        self.cfg = cfg
        self.key = str(cfg.get("weather.kma_forecast.service_key", "") or "")

    def configured(self) -> bool:
        return bool(self.key)

    def upcoming(self, start: datetime, hours: int) -> list[HourlyForecast]:
        raise NotConfigured("KMA 단기예보 키 미설정 (weather.kma_forecast.service_key)")


class ForecastService:
    """제공자 선택(기본 Sim, config 있으면 KMA) + 정시 버킷 캐시 + 스케줄러용 위험도 조회.
    캐시 덕에 스케줄러 스레드에서 반복 호출해도 재계산하지 않는다."""

    def __init__(self, cfg, provider: ForecastProvider | None = None):
        self.cfg = cfg
        if provider is not None:
            self.provider = provider
        else:
            kma = KmaForecastProvider(cfg)
            self.provider = kma if kma.configured() else SimForecastProvider()
        self._cache: list[HourlyForecast] = []
        self._cache_key = None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc).replace(microsecond=0)

    def upcoming(self, hours: int = 24) -> list[HourlyForecast]:
        start = self._now().replace(minute=0, second=0)
        key = (self.provider.name, start.isoformat(), hours)
        if key != self._cache_key:
            try:
                self._cache = self.provider.upcoming(start, hours)
            except NotConfigured:
                self._cache = SimForecastProvider().upcoming(start, hours)
            self._cache_key = key
        return self._cache

    def risk_at(self, dt: datetime) -> float:
        """주어진 시각(UTC)의 강수확률 0..1 — 가장 가까운 정시 예보. 스케줄러 게이팅용."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        fc = self.upcoming(36)
        if not fc:
            return 0.0
        best = min(fc, key=lambda f: abs((_parse(f.time_utc) - dt).total_seconds()))
        return best.precip_prob
