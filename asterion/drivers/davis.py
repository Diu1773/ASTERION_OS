"""Davis WeatherLink Live 로컬 HTTP 기상 드라이버.

WeatherLink Live는 LAN에서 `GET /v1/current_conditions` (JSON)를 노출한다 —
클라우드 의존 없이 저지연. 로컬 API는 미국 단위(°F, mph)라 표준 단위로
정규화한다. ISS(실외 통합센서)에는 운량 센서가 없으므로 cloud_score는
None으로 정직 보고한다 (필요하면 올스카이/클라우드센서로 보완).
"""

from __future__ import annotations

from .base import WeatherDriver, WeatherStatus


def _f_to_c(f: float | None) -> float | None:
    return None if f is None else round((f - 32.0) * 5.0 / 9.0, 2)


def _mph_to_ms(mph: float | None) -> float | None:
    return None if mph is None else round(mph * 0.44704, 2)


class DavisWeather(WeatherDriver):
    def __init__(self, base_url: str = "http://127.0.0.1"):
        import httpx  # real 모드에서만 import
        self._url = base_url.rstrip("/") + "/v1/current_conditions"
        self._client = httpx.Client(timeout=httpx.Timeout(4.0, connect=1.5))

    def _fetch(self) -> dict:
        r = self._client.get(self._url)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _iss(data: dict) -> dict:
        """conditions 배열에서 ISS(실외) 레코드(data_structure_type==1)를 찾는다."""
        conds = (data.get("data") or {}).get("conditions") or []
        for c in conds:
            if c.get("data_structure_type") == 1:
                return c
        return {}

    def connect(self) -> None:
        self._fetch()  # 도달 확인 — 실패 시 예외 → 워치독이 백오프로 재시도

    def read(self) -> WeatherStatus:
        try:
            iss = self._iss(self._fetch())
        except Exception as exc:
            return WeatherStatus(connected=False,
                                 detail=f"WeatherLink 응답 없음: {exc}",
                                 device_name="Davis WeatherLink Live")

        def num(key: str) -> float | None:
            try:
                return float(iss[key])
            except (KeyError, TypeError, ValueError):
                return None

        rain_rate = num("rain_rate_last")
        return WeatherStatus(
            connected=True,
            temp_c=_f_to_c(num("temp")),
            humidity=num("hum"),
            dew_point_c=_f_to_c(num("dew_point")),
            wind_ms=_mph_to_ms(num("wind_speed_last")),
            wind_dir_deg=num("wind_dir_last"),
            cloud_score=None,                      # ISS엔 운량 센서 없음
            rain=bool(rain_rate and rain_rate > 0),
            detail="WeatherLink Live", device_name="Davis WeatherLink Live")

    def close(self) -> None:
        self._client.close()
