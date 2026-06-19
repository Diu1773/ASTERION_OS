"""AI 스케줄러용 DSO 카탈로그 — 프론트 `web/catalog.js`(SKY_CATALOG)를 단일 소스로
읽어 백엔드에서 쓴다(중복 데이터 없음). messier 110 + ngc 등 전부. 항성(star)은
이미징/캠페인 대상이 아니라 제외. 파일을 못 읽으면 _FALLBACK(핵심 소수)로 동작.

ra=시간(h), dec=도, mag=겉보기등급, t=종류(gx/gc/oc/pn/neb/snr/dbl).
"""

from __future__ import annotations

import os
import re

_CAT_JS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web", "catalog.js")
# catalog.js 항목: { id: "M1", ra: 5.575, dec: 22.017, mag: 8.4, t: "snr", name: "게성운" }
_OBJ = re.compile(
    r'\{\s*id:\s*"([^"]+)",\s*ra:\s*([-\d.]+),\s*dec:\s*([-\d.]+),'
    r'\s*mag:\s*([-\d.]+),\s*t:\s*"([^"]*)",\s*name:\s*"([^"]*)"')

_FALLBACK = [
    {"id": "M31", "name": "안드로메다 은하", "ra": 0.712, "dec": 41.27, "mag": 3.4, "t": "gx"},
    {"id": "M42", "name": "오리온 대성운", "ra": 5.591, "dec": -5.39, "mag": 4.0, "t": "neb"},
    {"id": "M13", "name": "헤르쿨레스 대성단", "ra": 16.695, "dec": 36.46, "mag": 5.8, "t": "gc"},
    {"id": "M27", "name": "아령성운", "ra": 19.994, "dec": 22.72, "mag": 7.4, "t": "pn"},
    {"id": "M45", "name": "플레이아데스", "ra": 3.790, "dec": 24.12, "mag": 1.6, "t": "oc"},
]


def _load() -> list[dict]:
    try:
        txt = open(_CAT_JS, encoding="utf-8").read()
    except OSError:
        return list(_FALLBACK)
    out = []
    for m in _OBJ.finditer(txt):
        gid, ra, dec, mag, t, name = m.groups()
        if t == "star":
            continue   # 항성은 이미징/캠페인 DSO 아님
        out.append({"id": gid, "ra": float(ra), "dec": float(dec),
                    "mag": float(mag), "t": t, "name": name})
    return out or list(_FALLBACK)


DSO: list[dict] = _load()

TYPE_KO = {"gx": "은하", "gc": "구상성단", "oc": "산개성단",
           "pn": "행성상성운", "neb": "성운", "snr": "초신성잔해", "dbl": "이중성"}


def match_type(q: str | None) -> list[str] | None:
    """자연어 종류 질의 → 종류코드 목록. None이면 전체."""
    s = (q or "").strip().lower()
    if not s or s in ("all", "전체", "아무거나", "any", "messier", "메시에"):
        return None
    if "은하" in s or "galaxy" in s:
        return ["gx"]
    if "행성상" in s or "planetary" in s:
        return ["pn"]
    if "성운" in s or "nebula" in s:
        return ["neb", "pn"]
    if "구상" in s or "globular" in s:
        return ["gc"]
    if "산개" in s or "open" in s:
        return ["oc"]
    if "성단" in s or "cluster" in s:
        return ["gc", "oc"]
    return None
