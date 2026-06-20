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
