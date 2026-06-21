"""Prometheus 노출 — 샘플러 스냅샷을 /metrics 텍스트로 변환(Grafana 연동).

Grafana는 Prometheus 데이터소스로 이 엔드포인트를 스크레이프해 시계열로 그린다. 여기선
'현재값'만 게이지로 내보내고(표준), 추세는 Prometheus가 시간축으로 쌓는다. 추가형 —
기존 텔레메트리(인메모리 링 / DB 다운샘플)는 그대로 두고 읽기만 한다.

이름 규칙: asterion_<채널>  (예: telemetry_last 'mount.alt' → asterion_mount_alt).
상태/모드처럼 범주형은 라벨 게이지로(asterion_safety_state{state="OBSERVING"} 1).
"""

from __future__ import annotations

import re
from typing import Any

_BAD = re.compile(r"[^a-zA-Z0-9_:]")
_MULTI = re.compile(r"_+")


def _metric_name(raw: str) -> str:
    name = _MULTI.sub("_", _BAD.sub("_", raw.lower())).strip("_")
    return name or "unknown"


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return "NaN" if v != v else repr(v)
    return str(v)


class _Doc:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self._typed: set[str] = set()

    def gauge(self, name: str, value: Any, labels: dict | None = None,
              help: str | None = None) -> None:
        if name not in self._typed:
            if help:
                self.lines.append(f"# HELP {name} {help}")
            self.lines.append(f"# TYPE {name} gauge")
            self._typed.add(name)
        lbl = ""
        if labels:
            inner = ",".join(f'{k}="{_esc(str(v))}"' for k, v in labels.items())
            lbl = "{" + inner + "}"
        self.lines.append(f"{name}{lbl} {_fmt(value)}")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def render_metrics(snapshot: dict | None, extra: dict | None = None) -> str:
    """스냅샷(+옵션 extra 게이지) → Prometheus 노출 텍스트."""
    d = _Doc()
    if not snapshot:
        d.gauge("asterion_up", 0, help="1 if a sampler snapshot is available")
        return d.text()

    d.gauge("asterion_up", 1, help="1 if a sampler snapshot is available")

    mode = snapshot.get("mode")
    if mode:
        d.gauge("asterion_mode_info", 1, labels={"mode": str(mode)},
                help="active driver mode (label)")
    state = (snapshot.get("safety") or {}).get("state")
    if state:
        d.gauge("asterion_safety_state", 1, labels={"state": str(state)},
                help="current safety state (label)")

    # 세션/운영 플래그 (0/1)
    flags = {
        "asterion_capture_active": (snapshot.get("capture") or {}).get("active"),
        "asterion_orchestrator_running": (snapshot.get("orchestrator") or {}).get("running"),
        "asterion_nightrunner_active": (snapshot.get("night_runner") or {}).get("active"),
        "asterion_autoflat_running": (snapshot.get("autoflat") or {}).get("running"),
        "asterion_forge_enabled": (snapshot.get("forge") or {}).get("enabled"),
    }
    for name, val in flags.items():
        d.gauge(name, bool(val), help="operational flag (0/1)")

    # 수치 텔레메트리 — 평탄 dict(이미 'mount.alt' 등 채널 키). bool은 0/1, None/문자열 제외.
    for k, v in (snapshot.get("telemetry_last") or {}).items():
        if v is None:
            continue
        if isinstance(v, bool):
            d.gauge(f"asterion_{_metric_name(k)}", v, help="telemetry channel")
            continue
        if not isinstance(v, (int, float)):
            continue
        d.gauge(f"asterion_{_metric_name(k)}", v, help="telemetry channel")

    # 호출부가 주는 추가 게이지(업타임·디스크 등) — 이미 asterion_ 접두면 그대로.
    for k, v in (extra or {}).items():
        if v is None:
            continue
        name = k if k.startswith("asterion_") else f"asterion_{_metric_name(k)}"
        d.gauge(name, v)

    return d.text()
