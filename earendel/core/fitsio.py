"""FITS 프레임 저장 — 캡처/스카이플랫 공용.

경로 규칙: data/frames/YYYYMMDD/{IMAGETYP}_{FILTER}_{HHMMSS}_{seq:03d}.fits
FITS 헤더는 ASCII만 허용되므로 비ASCII 문자는 제거한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np

try:
    from astropy.io import fits as _fits
except ImportError:  # astropy 없으면 통계만 기록하고 파일 저장은 생략
    _fits = None


def have_fits() -> bool:
    return _fits is not None


def _ascii(value: str) -> str:
    return value.encode("ascii", errors="ignore").decode("ascii").strip()


def save_frame(frames_dir: Path, cfg, img: np.ndarray, *, image_type: str,
               filter_name: str, exposure_s: float, seq: int,
               mount_st=None, object_name: str = "") -> Path | None:
    if _fits is None:
        return None
    now = datetime.now(timezone.utc)
    day_dir = frames_dir / now.strftime("%Y%m%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    tag = _ascii(filter_name) or "X"
    path = day_dir / f"{image_type}_{tag}_{now.strftime('%H%M%S')}_{seq:03d}.fits"

    hdu = _fits.PrimaryHDU(img)
    h = hdu.header
    h["IMAGETYP"] = image_type
    if object_name:
        h["OBJECT"] = _ascii(object_name)
    h["FILTER"] = _ascii(filter_name)
    h["EXPTIME"] = round(float(exposure_s), 3)
    h["DATE-OBS"] = now.strftime("%Y-%m-%dT%H:%M:%S")
    site = _ascii(str(cfg.get("site.name_ascii", cfg.get("site.name", ""))))
    if site:
        h["SITENAME"] = site
    h["INSTRUME"] = _ascii(str(cfg.get("site.instrument", "")))
    h["FOCALLEN"] = float(cfg.get("site.focal_length_mm", 0.0))
    if mount_st is not None:
        if mount_st.alt_degs is not None:
            h["ALTITUDE"] = round(mount_st.alt_degs, 4)
        if mount_st.az_degs is not None:
            h["AZIMUTH"] = round(mount_st.az_degs, 4)
        if mount_st.ra_hours is not None:
            h["RA"] = round(mount_st.ra_hours * 15.0, 6)
        if mount_st.dec_degs is not None:
            h["DEC"] = round(mount_st.dec_degs, 6)
    h["SWCREATE"] = "Earendel 0.1"
    hdu.writeto(path, overwrite=True)
    return path
