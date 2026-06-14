"""연결 워치독 — 끊긴 장비를 자동으로 다시 붙인다 (자율형 복구).

상태 샘플러가 1 Hz로 적재하는 스냅샷을 읽어, "연결돼 있어야 하는데
(desired=True) 끊긴" 장비만 지수 백오프로 재연결한다. 운영자가 일부러
해제한 장비(desired=False)는 건드리지 않는다. 복구도 ConnectionManager의
같은 경로(reconnect)를 타므로 감사·안전 규칙을 우회하지 않는다.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any

from ..config import Config
from ..core.events import EventHub
from ..drivers import REGISTRY, ConnectionManager


class ConnectionWatchdog:
    def __init__(self, cfg: Config, conn: ConnectionManager, sampler: Any,
                 events: EventHub):
        self.conn = conn
        self.sampler = sampler          # snapshot 제공 (StatusSampler)
        self.events = events
        self.enabled = bool(cfg.get("drivers.auto_reconnect", True))
        self.interval = float(cfg.get("drivers.watchdog_interval_s", 5.0))
        self.base = float(cfg.get("drivers.reconnect_base_s", 5.0))
        self.cap = float(cfg.get("drivers.reconnect_max_s", 60.0))
        self._attempts: dict[str, int] = {}   # key -> 연속 실패 횟수
        self._next: dict[str, float] = {}      # key -> 다음 시도 가능 시각
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.enabled:
            self._task = asyncio.create_task(self._loop(), name="conn-watchdog")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def _backoff(self, attempts: int) -> float:
        return min(self.base * (2 ** attempts), self.cap)

    def due_for_recovery(self, snapshot: dict, now: float) -> list[str]:
        """재연결을 시도해야 하는 장비 키 목록 (순수 함수 — 테스트 용이)."""
        out: list[str] = []
        for key, spec in REGISTRY.items():
            if not self.conn.desired.get(key, False):
                continue                                  # 운영자가 끈 장비
            if spec.snap_key not in snapshot:
                continue                                  # 아직 관측 안 됨 → 판단 보류
            if snapshot[spec.snap_key].get("connected"):
                continue                                  # 정상
            if now < self._next.get(key, 0.0):
                continue                                  # 백오프 대기 중
            out.append(key)
        return out

    def note_connected(self, key: str) -> bool:
        """연결 회복을 인지하고 백오프를 리셋. 직전에 시도가 있었으면 True."""
        recovered = self._attempts.get(key, 0) > 0
        self._attempts[key] = 0
        self._next[key] = 0.0
        return recovered

    async def _tick(self) -> None:
        snap = self.sampler.snapshot or {}
        if not snap:
            return
        now = time.time()
        # 회복된 장비 백오프 리셋 + 회복 로그
        for key, spec in REGISTRY.items():
            dev = snap.get(spec.snap_key) or {}
            if dev.get("connected") and self.note_connected(key):
                self.events.log("system", f"{spec.label} 자동 재연결 성공")
        # 끊긴 장비 복구 시도
        for key in self.due_for_recovery(snap, now):
            attempts = self._attempts.get(key, 0)
            if attempts == 0:   # 끊김 첫 감지에만 경고 (재시도 로그 도배 방지)
                self.events.log("system",
                                f"{REGISTRY[key].label} 연결 끊김 — 자동 재연결 시작",
                                "warn")
            await self.conn.reconnect(key)
            self._attempts[key] = attempts + 1
            self._next[key] = now + self._backoff(attempts)

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.events.log("watchdog",
                                f"워치독 오류:\n{traceback.format_exc()}", "error")
            await asyncio.sleep(self.interval)
