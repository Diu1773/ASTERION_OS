"""저정밀 태양 위치·항성시 계산 (외부 의존성 없음).

대시보드 표시와 박명 판정용 — 정밀도 ~0.1°면 충분하다.
정밀 천체력이 필요해지면 astropy.coordinates로 교체한다.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

D2R = math.pi / 180.0
R2D = 180.0 / math.pi


def julian_day(dt_utc: datetime) -> float:
    return dt_utc.timestamp() / 86400.0 + 2440587.5


def gmst_hours(dt_utc: datetime) -> float:
    jd = julian_day(dt_utc)
    return (18.697374558 + 24.06570982441908 * (jd - 2451545.0)) % 24.0


def lst_hours(dt_utc: datetime, longitude_deg: float) -> float:
    return (gmst_hours(dt_utc) + longitude_deg / 15.0) % 24.0


def sun_radec(dt_utc: datetime) -> tuple[float, float]:
    """태양의 (RA hours, Dec deg). 저정밀(±0.01° 수준)."""
    n = julian_day(dt_utc) - 2451545.0
    mean_lon = (280.460 + 0.9856474 * n) % 360.0
    mean_anom = ((357.528 + 0.9856003 * n) % 360.0) * D2R
    ecl_lon = (mean_lon + 1.915 * math.sin(mean_anom)
               + 0.020 * math.sin(2 * mean_anom)) * D2R
    obliq = (23.439 - 0.0000004 * n) * D2R
    ra = math.atan2(math.cos(obliq) * math.sin(ecl_lon), math.cos(ecl_lon))
    dec = math.asin(math.sin(obliq) * math.sin(ecl_lon))
    ra_hours = (ra * R2D / 15.0) % 24.0
    return ra_hours, dec * R2D


def radec_to_altaz(ra_hours: float, dec_deg: float, lat_deg: float,
                   lst_h: float) -> tuple[float, float]:
    """(alt deg, az deg) — 방위각은 북에서 동으로 증가."""
    ha = (lst_h - ra_hours) * 15.0
    ha = (ha + 180.0) % 360.0 - 180.0
    ha_r, dec_r, lat_r = ha * D2R, dec_deg * D2R, lat_deg * D2R
    sin_alt = (math.sin(dec_r) * math.sin(lat_r)
               + math.cos(dec_r) * math.cos(lat_r) * math.cos(ha_r))
    alt = math.asin(max(-1.0, min(1.0, sin_alt)))
    cos_az = ((math.sin(dec_r) - math.sin(alt) * math.sin(lat_r))
              / max(1e-9, math.cos(alt) * math.cos(lat_r)))
    az = math.acos(max(-1.0, min(1.0, cos_az)))
    if math.sin(ha_r) > 0:
        az = 2 * math.pi - az
    return alt * R2D, az * R2D


def altaz_to_radec(alt_deg: float, az_deg: float, lat_deg: float,
                   lst_h: float) -> tuple[float, float]:
    """(ra hours, dec deg) — 시뮬 마운트의 좌표 표시용."""
    alt_r, az_r, lat_r = alt_deg * D2R, az_deg * D2R, lat_deg * D2R
    sin_dec = (math.sin(alt_r) * math.sin(lat_r)
               + math.cos(alt_r) * math.cos(lat_r) * math.cos(az_r))
    dec = math.asin(max(-1.0, min(1.0, sin_dec)))
    cos_ha = ((math.sin(alt_r) - math.sin(lat_r) * math.sin(dec))
              / max(1e-9, math.cos(lat_r) * math.cos(dec)))
    ha = math.acos(max(-1.0, min(1.0, cos_ha))) * R2D
    if math.sin(az_r) > 0:  # 동쪽 하늘 → 시간각 음수 (떠오르는 중)
        ha = -ha
    ra_hours = (lst_h - ha / 15.0) % 24.0
    return ra_hours, dec * R2D


def sun_altaz(dt_utc: datetime, lat_deg: float, lon_deg: float) -> tuple[float, float]:
    ra_h, dec = sun_radec(dt_utc)
    return radec_to_altaz(ra_h, dec, lat_deg, lst_hours(dt_utc, lon_deg))


def twilight_phase(sun_alt_deg: float) -> tuple[str, str]:
    """(코드, 한국어 라벨)"""
    if sun_alt_deg >= -0.833:
        return "day", "주간"
    if sun_alt_deg >= -6.0:
        return "civil", "시민박명"
    if sun_alt_deg >= -12.0:
        return "nautical", "항해박명"
    if sun_alt_deg >= -18.0:
        return "astro", "천문박명"
    return "night", "야간"


def fmt_ra_hours(ra_hours: float | None) -> str:
    if ra_hours is None:
        return "—"
    total = ra_hours % 24.0
    h = int(total)
    m = int((total - h) * 60)
    s = ((total - h) * 60 - m) * 60
    return f"{h:02d}:{m:02d}:{s:04.1f}"


def fmt_dec_degs(dec: float | None) -> str:
    if dec is None:
        return "—"
    sign = "+" if dec >= 0 else "-"
    a = abs(dec)
    d = int(a)
    m = int((a - d) * 60)
    s = ((a - d) * 60 - m) * 60
    return f"{sign}{d:02d}:{m:02d}:{s:04.1f}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
