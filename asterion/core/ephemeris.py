"""저정밀 태양 위치·항성시 계산 (외부 의존성 없음).

대시보드 표시와 박명 판정용 — 정밀도 ~0.1°면 충분하다.
정밀 천체력이 필요해지면 astropy.coordinates로 교체한다.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone

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


# ── 태양 회피(solar exclusion) — OTA가 태양/근방을 향하는 슬루를 막기 위한 각이격 ──────
# 야간엔 태양이 지평 아래라 정상 대상과의 이격이 항상 크므로(보통 90°+) 오탐 0.

def angular_separation_radec(ra1_h: float, dec1_deg: float,
                             ra2_h: float, dec2_deg: float) -> float:
    """두 적도좌표 점의 각이격(deg). 프레임 무관 — 태양도 천체이므로 이게 곧 실제 이각."""
    d1, d2 = dec1_deg * D2R, dec2_deg * D2R
    dra = (ra1_h - ra2_h) * 15.0 * D2R
    cosd = math.sin(d1) * math.sin(d2) + math.cos(d1) * math.cos(d2) * math.cos(dra)
    return math.acos(max(-1.0, min(1.0, cosd))) * R2D


def angular_separation_altaz(alt1: float, az1: float,
                             alt2: float, az2: float) -> float:
    """두 지평좌표 점의 각이격(deg). 구면 코사인법칙."""
    a1, a2 = alt1 * D2R, alt2 * D2R
    cosd = (math.sin(a1) * math.sin(a2)
            + math.cos(a1) * math.cos(a2) * math.cos((az1 - az2) * D2R))
    return math.acos(max(-1.0, min(1.0, cosd))) * R2D


def sun_separation_radec(ra_hours: float, dec_deg: float, dt_utc: datetime) -> float:
    sra, sdec = sun_radec(dt_utc)
    return angular_separation_radec(ra_hours, dec_deg, sra, sdec)


def sun_separation_altaz(alt_deg: float, az_deg: float, dt_utc: datetime,
                         lat_deg: float, lon_deg: float) -> float:
    sa, sz = sun_altaz(dt_utc, lat_deg, lon_deg)
    return angular_separation_altaz(alt_deg, az_deg, sa, sz)


def solar_exclusion_check(*, exclusion_deg: float,
                          ra_hours: float | None = None, dec_deg: float | None = None,
                          alt_deg: float | None = None, az_deg: float | None = None,
                          lat_deg: float | None = None, lon_deg: float | None = None,
                          dt_utc: datetime | None = None) -> tuple[bool, float, str]:
    """슬루 목표가 태양에서 exclusion_deg 이상 떨어졌는지. (ok, 이격deg, 사유).
    ra/dec를 주면 적도좌표 이격(lat/lon 불요), alt/az를 주면 태양 alt/az와의 이격(lat/lon 필요).
    좌표가 부족하면 fail-closed로 거부. 호출부가 이 ok를 ActionBus 사전조건으로 건다."""
    dt = dt_utc or now_utc()
    if ra_hours is not None and dec_deg is not None:
        sep = sun_separation_radec(ra_hours, dec_deg, dt)
    elif (alt_deg is not None and az_deg is not None
          and lat_deg is not None and lon_deg is not None):
        sep = sun_separation_altaz(alt_deg, az_deg, dt, lat_deg, lon_deg)
    else:
        return False, 0.0, "태양 이격 계산 불가 (좌표 부족) — 거부 (fail-closed)"
    ok = sep >= exclusion_deg
    msg = "ok" if ok else (f"태양 이격 {sep:.1f}° < 제외각 {exclusion_deg:.0f}° "
                           "— 태양 근접 슬루 거부")
    return ok, sep, msg


# Sky Panel용 — 달·밝은 행성의 (alt,az) + 달 위상. astropy(번들 ephemeris, 오프라인)로
# 계산하며 느리므로(수백 ms) 호출부(StatusSampler)가 30초 캐시한다. 천체는 분당 ~0.13°만
# 움직여 차트 표시엔 충분. 실패하면 빈 리스트(차트는 태양/마운트만 그림).
_SKY_BODIES = ("moon", "venus", "mars", "jupiter", "saturn")
_astropy_ready = False


def _ensure_astropy() -> None:
    """astropy를 오프라인·견고 모드로 1회만 설정(관측소는 네트워크 의존 금지).
    auto_download=False + auto_max_age=None + degraded_accuracy 무시 → IERS 데이터가
    오래돼도(미래 날짜 포함) 예외 없이 외삽한다. 차트용이라 arcsec 오차는 무관."""
    global _astropy_ready
    if _astropy_ready:
        return
    import warnings
    warnings.filterwarnings("ignore")
    from astropy.utils import iers
    iers.conf.auto_download = False
    iers.conf.auto_max_age = None
    try:
        iers.conf.iers_degraded_accuracy = "ignore"   # astropy 5.1+ — 외삽 시 raise 안 함
    except Exception:
        pass
    from astropy.coordinates import solar_system_ephemeris
    solar_system_ephemeris.set("builtin")             # 번들 — 외부 다운로드 없음
    _astropy_ready = True


def sky_bodies_altaz(dt_utc: datetime, lat_deg: float,
                     lon_deg: float) -> list[dict]:
    _ensure_astropy()
    from astropy.coordinates import AltAz, EarthLocation, get_body
    from astropy.time import Time
    import astropy.units as u
    loc = EarthLocation(lat=lat_deg * u.deg, lon=lon_deg * u.deg, height=0 * u.m)
    t = Time(dt_utc)
    aa = AltAz(obstime=t, location=loc)
    try:                                              # 태양 실패해도 달·행성은 그림
        sun_b = get_body("sun", t, loc)
    except Exception:
        sun_b = None
    out: list[dict] = []
    for name in _SKY_BODIES:
        try:
            b = get_body(name, t, loc)
            h = b.transform_to(aa)
            alt_deg, az_deg = float(h.alt.deg), float(h.az.deg)
            if not (math.isfinite(alt_deg) and math.isfinite(az_deg)):
                continue                              # NaN/inf 방어 — 깨진 좌표 차단
            item = {"name": name,
                    "kind": "moon" if name == "moon" else "planet",
                    "alt": round(alt_deg, 2), "az": round(az_deg, 2)}
            if name == "moon" and sun_b is not None:  # 위상: 태양-달 이각
                sep = float(b.separation(sun_b).deg)
                if math.isfinite(sep):
                    item["illum"] = round((1.0 - math.cos(sep * D2R)) / 2.0, 3)
                    # 달 RA가 태양보다 동쪽(0~12h 앞)이면 차오름(waxing), 아니면 이지러짐
                    item["waxing"] = ((float(b.ra.hour)
                                       - float(sun_b.ra.hour)) % 24.0) < 12.0
            out.append(item)
        except Exception:
            continue
    return out


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


# ---------- RA/Dec 입력 파싱 ----------

def _parse_sexagesimal(text: str) -> tuple[float, float, float, int]:
    s = str(text).strip()
    sign = -1 if s.startswith("-") else 1
    s = s.lstrip("+-")
    parts = [p for p in re.split(r"[:hmsdHMSD°'\"\s]+", s) if p]
    if not parts:
        raise ValueError(f"좌표를 해석할 수 없음: {text!r}")
    nums = [float(p) for p in parts]
    while len(nums) < 3:
        nums.append(0.0)
    return nums[0], nums[1], nums[2], sign


def parse_ra_hours(text: str) -> float:
    """'5.59', '05:35:17', '5h35m17s', '5 35 17' → 시간 단위 RA."""
    h, m, s, sign = _parse_sexagesimal(text)
    return (sign * (h + m / 60.0 + s / 3600.0)) % 24.0


def parse_dec_degs(text: str) -> float:
    """'-5.39', '-05:23:28', '-5d23m28s' → 도 단위 Dec."""
    d, m, s, sign = _parse_sexagesimal(text)
    val = sign * (d + m / 60.0 + s / 3600.0)
    if not -90.0 <= val <= 90.0:
        raise ValueError(f"적위 범위 밖: {val:.4f}°")
    return val


# ---------- 야간 타임라인 ----------

KST = timezone(timedelta(hours=9))


def night_timeline(lat_deg: float, lon_deg: float, *, step_s: int = 120,
                   flat_high: float = -1.0, flat_low: float = -12.0,
                   now: datetime | None = None) -> dict:
    """지역(KST) 정오→다음 정오의 태양고도 곡선 + 박명 이벤트 + 플랫 창."""
    now = now or now_utc()
    local_now = now.astimezone(KST)
    start_local = local_now.replace(hour=12, minute=0, second=0, microsecond=0)
    if local_now.hour < 12:
        start_local -= timedelta(days=1)
    start = start_local.astimezone(timezone.utc)

    n = int(24 * 3600 / step_s)
    ts: list[float] = []
    alts: list[float] = []
    for i in range(n + 1):
        t = start + timedelta(seconds=i * step_s)
        alt, _ = sun_altaz(t, lat_deg, lon_deg)
        ts.append(round(t.timestamp(), 1))
        alts.append(round(alt, 2))

    events: dict[str, float] = {}
    for name, th in (("horizon", -0.833), ("civil", -6.0),
                     ("nautical", -12.0), ("astro", -18.0)):
        for i in range(1, len(alts)):
            a0, a1 = alts[i - 1], alts[i]
            if (a0 - th) * (a1 - th) <= 0 and a0 != a1:
                frac = (th - a0) / (a1 - a0)
                key = f"{name}_{'set' if a1 < a0 else 'rise'}"
                events.setdefault(key, round(ts[i - 1] + frac * step_s, 1))

    windows: list[dict] = []
    in_window = False
    t0 = 0.0
    for i, a in enumerate(alts):
        ok = flat_low <= a <= flat_high
        if ok and not in_window:
            in_window, t0 = True, ts[i]
        elif not ok and in_window:
            windows.append({"start": t0, "end": ts[i]})
            in_window = False
    if in_window:
        windows.append({"start": t0, "end": ts[-1]})

    return {"start": ts[0], "end": ts[-1], "step_s": step_s,
            "t": ts, "sun_alt": alts, "events": events,
            "flat_windows": windows, "now": round(now.timestamp(), 1)}


def target_track(ra_hours: float, dec_degs: float, lat_deg: float,
                 lon_deg: float, t_epochs: list[float]) -> list[float]:
    """타임라인 시각 그리드에서 대상의 고도 곡선."""
    out: list[float] = []
    for te in t_epochs:
        dt = datetime.fromtimestamp(te, tz=timezone.utc)
        alt, _ = radec_to_altaz(ra_hours, dec_degs, lat_deg,
                                lst_hours(dt, lon_deg))
        out.append(round(alt, 2))
    return out
