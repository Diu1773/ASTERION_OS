"""PlaneWave PWI4 HTTP 마운트 드라이버.

PWI4는 포트 8220에서 HTTP API를 노출한다 (PWI4가 켜져 있어야 함).
/status 응답은 PWI4 버전에 따라 `key=value` 줄 목록 또는 JSON —
둘 다 파싱한다. 명령 엔드포인트 파라미터 이름은 PlaneWave가 배포하는
pwi4_client.py 기준이며, 설치된 PWI4 버전에서 한 번 확인할 것.
"""

from __future__ import annotations

import json

from .base import MountDriver, MountStatus


class Pwi4Mount(MountDriver):
    def __init__(self, base_url: str = "http://127.0.0.1:8220"):
        import httpx  # real 모드에서만 import
        self._client = httpx.Client(base_url=base_url, timeout=5.0)

    def _get(self, path: str, **params) -> str:
        r = self._client.get(path, params={k: v for k, v in params.items() if v is not None})
        r.raise_for_status()
        return r.text

    @staticmethod
    def _parse(text: str) -> dict:
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, ValueError):
            pass
        out: dict[str, str] = {}
        for line in text.splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
        return out

    @staticmethod
    def _f(d: dict, key: str) -> float | None:
        v = d.get(key)
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _b(d: dict, key: str) -> bool:
        return str(d.get(key, "")).lower() == "true"

    def connect(self) -> None:
        st = self._parse(self._get("/status"))
        if not self._b(st, "mount.is_connected"):
            self._get("/mount/connect")

    def status(self) -> MountStatus:
        try:
            d = self._parse(self._get("/status"))
        except Exception as exc:
            return MountStatus(connected=False, detail=f"PWI4 응답 없음: {exc}")
        return MountStatus(
            connected=self._b(d, "mount.is_connected"),
            ra_hours=self._f(d, "mount.ra_j2000_hours"),
            dec_degs=self._f(d, "mount.dec_j2000_degs"),
            alt_degs=self._f(d, "mount.altitude_degs"),
            az_degs=self._f(d, "mount.azimuth_degs"),
            slewing=self._b(d, "mount.is_slewing"),
            tracking=self._b(d, "mount.is_tracking"),
            detail="PWI4",
        )

    def goto_altaz(self, alt_deg: float, az_deg: float) -> None:
        self._get("/mount/goto_alt_az", alt_degs=alt_deg, az_degs=az_deg)

    def goto_radec(self, ra_hours: float, dec_degs: float) -> None:
        self._get("/mount/goto_ra_dec_j2000", ra_hours=ra_hours,
                  dec_degs=dec_degs)

    def offset_arcsec(self, dra_arcsec: float, ddec_arcsec: float) -> None:
        # pwi4_client.mount_offset 기준 파라미터명 — PWI4 버전에서 확인 필요
        self._get("/mount/offset", ra_add_arcsec=dra_arcsec,
                  dec_add_arcsec=ddec_arcsec)

    def set_tracking(self, on: bool) -> None:
        self._get("/mount/tracking_on" if on else "/mount/tracking_off")

    def stop(self) -> None:
        self._get("/mount/stop")

    def close(self) -> None:
        self._client.close()
