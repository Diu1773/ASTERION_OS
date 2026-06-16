"""위성영상 프록시 — KMA 천리안2A(GK-2A) 최신 프레임을 고정 로컬 URL로 서빙.

KMA 국가기상위성센터(nmsc.kma.go.kr)의 GK-2A 영상은 **타임스탬프 URL**이라
(`.../{YYYYMM}/{DD}/{HH}/gk2a_..._{YYYYMMDDhhmm}.png`) 브라우저가 "항상 최신"으로
직접 폴링할 수 없다. 게다가 존재하지 않는(미래) 시각도 HTTP 200 + HTML 에러
페이지를 돌려주므로 content-type 검증이 필수다.

그래서 서버가:
  1) 현재 UTC에서 제품 주기(기본 2분) 격자로 시각을 내려가며 역탐색,
  2) content-type이 image/* 이고 본문이 충분히 큰 첫 프레임을 채택(=HTML 함정 회피),
  3) 그 PNG를 메모리에 캐시하고 `/api/satellite/latest.png` 고정 URL로 서빙.

이로써 (a) 클라이언트가 몇 개든 원본은 주기당 한 번만 받고(=받는 주기를 서버가
제어), (b) 핫링크/CORS/타임스탬프 문제를 전부 서버에서 흡수한다. 갱신 주기·제품·
역탐색 범위는 모두 config [satellite]로 조정한다.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx

# PNG 매직(참고용). 실제 함정 회피는 content-type + 최소 크기로 한다(jpg 제품도 허용).
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class SatelliteProxy:
    """타임스탬프 위성영상 소스에서 최신 프레임을 찾아 캐시·서빙."""

    def __init__(self, cfg) -> None:
        g = cfg.get
        self.enabled: bool = bool(g("satellite.enabled", True))
        self.base: str = str(g("satellite.base_url", "https://nmsc.kma.go.kr")).rstrip("/")
        # 시각 토큰: {YYYYMM} {DD} {HH} (경로) + {YYYYMMDDhhmm} (파일명). UTC 기준.
        # 기본 = GK-2A IR 10.5µm 한반도(KO) — 적외라 주야 24h, 2분 주기.
        self.template: str = str(g(
            "satellite.path_template",
            "/IMG/GK2A/AMI/PRIMARY/L1B/COMPLETE/KO/{YYYYMM}/{DD}/{HH}/"
            "gk2a_ami_le1b_ir105_ko020lc_{YYYYMMDDhhmm}.png"))
        self.media_type: str = str(g("satellite.media_type", "image/png"))
        # 후보 시각 격자(초). 제품 주기와 같거나 그 약수여야 한다(KO=120s).
        self.grid_s: int = int(g("satellite.grid_seconds", 120))
        # 가장 최근(아직 미발행일 수 있는) 프레임을 건너뛰는 여유(초).
        self.latency_s: int = int(g("satellite.latency_seconds", 180))
        # 역탐색 최대 스텝 — grid_s * lookback_steps 만큼 과거까지 본다.
        self.lookback: int = max(1, int(g("satellite.lookback_steps", 30)))
        # 캐시 갱신 주기(초) — 이보다 오래되면 다음 요청 때 새 프레임을 찾는다.
        self.refresh_s: int = int(g("satellite.refresh_seconds", 120))
        self.timeout_s: float = float(g("satellite.timeout_seconds", 12.0))
        self.min_bytes: int = int(g("satellite.min_bytes", 4096))

        self._lock = asyncio.Lock()
        self._png: bytes = b""
        self._stamp: str = ""          # 채택된 프레임의 YYYYMMDDhhmm (UTC)
        self._fetched_at: float = 0.0  # 마지막 성공 (monotonic)
        self._tried_at: float = 0.0    # 마지막 시도 (monotonic) — 실패 폭주 방지

    # ---- URL 빌드 ----

    def _url_for(self, dt: datetime) -> str:
        path = (self.template
                .replace("{YYYYMM}", dt.strftime("%Y%m"))
                .replace("{DD}", dt.strftime("%d"))
                .replace("{HH}", dt.strftime("%H"))
                .replace("{YYYYMMDDhhmm}", dt.strftime("%Y%m%d%H%M")))
        return self.base + path

    # ---- 단일 프레임 검증 fetch ----

    async def _probe(self, client: httpx.AsyncClient, dt: datetime) -> bytes | None:
        """해당 시각 프레임을 받아 유효한 이미지면 bytes, 아니면 None.

        미래/결측 시각은 200 + text/html 에러페이지가 오므로 content-type과
        최소 크기로 거른다.
        """
        try:
            r = await client.get(self._url_for(dt))
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        ctype = r.headers.get("content-type", "").lower()
        body = r.content
        if not ctype.startswith("image/") or len(body) < self.min_bytes:
            return None
        return body

    # ---- 역탐색 → 캐시 ----

    async def _refresh(self) -> None:
        now = datetime.now(timezone.utc) - timedelta(seconds=self.latency_s)
        epoch = int(now.timestamp())
        floored = epoch - (epoch % self.grid_s)
        async with httpx.AsyncClient(
                timeout=self.timeout_s, follow_redirects=True,
                headers={"User-Agent": "ASTERION-satellite/1.0"}) as client:
            for i in range(self.lookback):
                dt = datetime.fromtimestamp(floored - i * self.grid_s, timezone.utc)
                body = await self._probe(client, dt)
                if body is not None:
                    self._png = body
                    self._stamp = dt.strftime("%Y%m%d%H%M")
                    self._fetched_at = time.monotonic()
                    return
        # 전부 실패 — 기존 캐시(있으면)를 그대로 둔다.

    # ---- 외부 진입점 ----

    async def get(self) -> tuple[bytes, str]:
        """최신 프레임 (png_bytes, 'YYYYMMDDhhmm'). 없으면 (b'', '')."""
        if not self.enabled:
            return b"", ""
        now = time.monotonic()
        if self._png and (now - self._fetched_at) < self.refresh_s:
            return self._png, self._stamp
        async with self._lock:
            now = time.monotonic()
            if self._png and (now - self._fetched_at) < self.refresh_s:
                return self._png, self._stamp
            # 캐시가 비었는데 방금(30s내) 실패했으면 재시도하지 않고 빈 값 반환.
            if not self._png and (now - self._tried_at) < 30.0:
                return self._png, self._stamp
            self._tried_at = now
            await self._refresh()
            return self._png, self._stamp

    def meta(self) -> dict:
        age = (time.monotonic() - self._fetched_at) if self._png else None
        return {
            "enabled": self.enabled,
            "have_frame": bool(self._png),
            "frame_utc": self._stamp or None,     # YYYYMMDDhhmm
            "age_seconds": round(age, 1) if age is not None else None,
            "refresh_seconds": self.refresh_s,
            "grid_seconds": self.grid_s,
        }
