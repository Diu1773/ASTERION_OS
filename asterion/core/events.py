"""WebSocket 브로드캐스트 + 라이브 로그 링버퍼."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from typing import Any


class EventHub:
    def __init__(self):
        self.clients: set[Any] = set()
        self.log_buffer: deque[dict] = deque(maxlen=300)
        self._loop: asyncio.AbstractEventLoop | None = None
        self.narrator: Any = None   # 능동 내레이션 훅(alert dict→한 줄). app.py가 설정, None=무동작.

    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def register(self, ws: Any) -> None:
        self.clients.add(ws)

    def unregister(self, ws: Any) -> None:
        self.clients.discard(ws)

    async def _broadcast(self, payload: dict) -> None:
        if not self.clients:
            return
        msg = json.dumps(payload, ensure_ascii=False, default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)

    def emit(self, payload: dict) -> None:
        """스레드 안전 fire-and-forget 브로드캐스트."""
        loop = self._loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), loop)

    def log(self, source: str, msg: str, level: str = "info") -> None:
        entry = {
            "type": "log",
            "ts": time.strftime("%H:%M:%S"),
            "source": source,
            "level": level,
            "msg": msg,
        }
        self.log_buffer.append(entry)
        self.emit(entry)

    def frame(self, frame_dict: dict) -> None:
        self.emit({"type": "frame", "frame": frame_dict})

    def action(self, action_dict: dict) -> None:
        self.emit({"type": "action", "action": action_dict})

    def status(self, snapshot: dict) -> None:
        self.emit({"type": "status", "status": snapshot})

    def alert(self, alert_dict: dict) -> None:
        """위험 알림 — WebSocket 브로드캐스트 + 로그 버퍼(콘솔에도 남게). CRITICAL=error 레벨."""
        self.log_buffer.append({
            "type": "log", "ts": time.strftime("%H:%M:%S"), "source": "alert",
            "level": "error" if alert_dict.get("level") == "critical" else "warn",
            "msg": "⚠ " + str(alert_dict.get("title", "")),
        })
        self.emit({"type": "alert", "alert": alert_dict})
        # 능동 내레이션 — 경보를 '관측+권고' 한 줄로 먼저 띄운다(설정 시). 로그독에 advisor로도
        # 남겨 즉시 보이고, narration 이벤트로 챗 버블(프론트)에 쓴다. 실패는 무시(경보 흐름 불가침).
        if self.narrator is not None:
            try:
                line = self.narrator(alert_dict)
            except Exception:
                line = None
            if line:
                ts = time.strftime("%H:%M:%S")
                lvl = "error" if alert_dict.get("level") == "critical" else "warn"
                self.log_buffer.append({"type": "log", "ts": ts, "source": "advisor",
                                        "level": lvl, "msg": "💬 " + line})
                self.emit({"type": "narration", "narration": {
                    "text": line, "level": alert_dict.get("level", "warn"),
                    "rule": alert_dict.get("rule_id", ""), "ts": ts}})
