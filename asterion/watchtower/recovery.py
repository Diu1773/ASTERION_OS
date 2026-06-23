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
                 events: EventHub, *, alert_fn: Any = None):
        self.conn = conn
        self.sampler = sampler          # snapshot 제공 (StatusSampler)
        self.events = events
        self._alert = alert_fn          # 샘플러 스톨 시 보안 Alert(rank8) — 없으면 로그만
        self.enabled = bool(cfg.get("drivers.auto_reconnect", True))
        self.interval = float(cfg.get("drivers.watchdog_interval_s", 5.0))
        # 샘플러 루프 생존 감시(rank8) — 스냅샷이 이 시간 이상 갱신 없으면 루프 정지로 보고 경보.
        # 폐루프 안전(돔/태양/데드맨)이 샘플러 루프에 얹혀 있어, 루프가 죽으면 함께 죽는 SPOF를
        # *독립 루프*인 이 워치독이 가시화한다. 1Hz 샘플러 기준 넉넉히(기본 15s).
        self._stall_threshold = float(cfg.get("drivers.sampler_stall_seconds", 15.0))
        self._stall_alarmed = False
        self.base = float(cfg.get("drivers.reconnect_base_s", 5.0))
        self.cap = float(cfg.get("drivers.reconnect_max_s", 60.0))
        # 연결 직후 유예 — 방금 (재)연결한 장비는 이 시간 동안 재연결하지 않는다.
        # ZWO EFW 등은 연결되면 호밍/캘리브레이션을 도는데 그동안 status가 잠깐
        # '끊김'으로 보일 수 있다. 유예가 없으면 '재연결→재호밍'을 무한 반복한다.
        self.grace = float(cfg.get("drivers.reconnect_grace_s", 30.0))
        # 끊김을 몇 번 연속 관측해야 '진짜 끊김'으로 보고 재연결할지 (디바운스).
        # 1회 오탐(예: ZWO EFW가 호밍 중 잠깐 Connected=False/속성 오류)으로
        # 재연결→슬롯 재생성→Connected=True 재설정→재호밍 무한반복을 막는다.
        self.fail_threshold = max(1, int(cfg.get("drivers.reconnect_fail_threshold", 2)))
        self.max_attempts = max(1, int(cfg.get("drivers.reconnect_max_attempts", 3)))
        self._attempts: dict[str, int] = {}   # key -> 연속 실패 횟수
        self._next: dict[str, float] = {}      # key -> 다음 시도 가능 시각
        self._fails: dict[str, int] = {}       # key -> 연속 끊김 관측 횟수 (디바운스)
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

    def _update_fail_counts(self, snapshot: dict) -> None:
        """스냅샷을 보고 장비별 '연속 끊김 관측' 카운터를 갱신 (디바운스용).
        연결됨/운영자 해제/미관측이면 0으로 리셋, 끊김이면 +1."""
        for key, spec in REGISTRY.items():
            if not self.conn.desired.get(key, False):
                self._fails[key] = 0                       # 운영자가 끈 장비
            elif spec.snap_key not in snapshot:
                continue                                   # 미관측 → 카운트 보류
            elif snapshot[spec.snap_key].get("connected"):
                self._fails[key] = 0                       # 정상
            else:
                self._fails[key] = self._fails.get(key, 0) + 1

    def due_for_recovery(self, snapshot: dict, now: float) -> list[str]:
        """재연결을 시도해야 하는 장비 키 목록 (순수 함수 — 테스트 용이)."""
        out: list[str] = []
        for key, spec in REGISTRY.items():
            if not self.conn.desired.get(key, False):
                continue                                  # 운영자가 끈 장비
            if getattr(self.conn.drivers.get(key),
                       "reconnect_blocked", False):
                continue                                  # 장비 점검이 필요한 고착
            if spec.snap_key not in snapshot:
                continue                                  # 아직 관측 안 됨 → 판단 보류
            if snapshot[spec.snap_key].get("connected"):
                continue                                  # 정상
            if now - self.conn.connected_at.get(key, 0.0) < self.grace:
                continue                                  # 방금 (재)연결 — 호밍 끝낼 시간을 준다
            if self._fails.get(key, 0) < self.fail_threshold:
                continue                                  # 디바운스 — 일시 끊김(호밍 등)은 무시
            if now < self._next.get(key, 0.0):
                continue                                  # 백오프 대기 중
            if self._attempts.get(key, 0) >= self.max_attempts:
                continue                                  # 최대 재시도 횟수 초과 — 수동 연결 필요
            out.append(key)
        return out

    def note_connected(self, key: str) -> bool:
        """연결 회복을 인지하고 백오프를 리셋. 직전에 시도가 있었으면 True."""
        recovered = self._attempts.get(key, 0) > 0
        self._attempts[key] = 0
        self._next[key] = 0.0
        return recovered

    def _check_sampler_stall(self, snap: dict) -> None:
        """샘플러 루프 생존 감시 — 스냅샷 ts_mono가 _stall_threshold 이상 안 갱신되면 루프 정지로
        보고 1회 경보(폐루프 안전이 샘플러 루프에 종속된 SPOF를 독립 루프에서 가시화)."""
        ts = snap.get("ts_mono")
        if ts is None:
            return
        age = time.monotonic() - ts
        if age > self._stall_threshold:
            if not self._stall_alarmed:
                self._stall_alarmed = True
                msg = (f"⚠ 상태 샘플러 루프 정지 의심 — 스냅샷 {age:.0f}s 갱신 없음. 폐루프 안전"
                       "(돔 비상닫힘·태양·세션 데드맨)이 함께 멈췄을 수 있음 — 즉시 점검 필요")
                self.events.log("watchdog", msg, "error")
                if self._alert is not None:
                    try:
                        self._alert("샘플러 루프 정지 의심", msg)
                    except Exception:
                        pass
        else:
            self._stall_alarmed = False

    async def _tick(self) -> None:
        snap = self.sampler.snapshot or {}
        if not snap:
            return
        self._check_sampler_stall(snap)        # 샘플러 루프 생존 감시(rank8)
        now = time.time()
        self._update_fail_counts(snap)        # 디바운스 카운터 갱신
        # 회복된 장비 백오프 리셋 + 회복 로그
        for key, spec in REGISTRY.items():
            dev = snap.get(spec.snap_key) or {}
            if dev.get("connected") and self.note_connected(key):
                self.events.log("system", f"{spec.label} 자동 재연결 성공")
        # 끊긴 장비 복구 시도 (디바운스 통과한 것만)
        for key in self.due_for_recovery(snap, now):
            attempts = self._attempts.get(key, 0)
            if attempts == 0:
                self.events.log("system",
                                f"{REGISTRY[key].label} 연결 끊김 — 자동 재연결 시작",
                                "warn")
            await self.conn.reconnect(key)
            self._attempts[key] = attempts + 1
            if self._attempts[key] >= self.max_attempts:
                self.events.log("system",
                                f"{REGISTRY[key].label} 자동 재연결 {self._attempts[key]}회 실패 "
                                "— 수동으로 연결하세요",
                                "error")
            self._next[key] = now + self._backoff(attempts)
            self._fails[key] = 0   # 디바운스 리셋 — 재연결 직후 호밍/초기화 시간 확보

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
