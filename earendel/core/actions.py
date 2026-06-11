"""액션 버스 — 세계를 바꾸는 모든 행동은 여기를 통과한다.

팔란티어식 원칙: AI/규칙이 직접 장비를 건드리지 않는다.
정의된 액션이 사전조건 검사 → 실행 → 입출력 상태와 함께
ActionLog에 기록된다. 실패한 사전조건도 기록된다 (감사 가능성).
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from .events import EventHub
from .ontology import ActionLog, Db, row_to_dict


class ActionError(Exception):
    pass


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


class ActionBus:
    def __init__(self, db: Db, events: EventHub,
                 snapshot_fn: Callable[[], dict]):
        self._db = db
        self._events = events
        self._snapshot_fn = snapshot_fn

    def _state(self) -> dict:
        """ActionLog에 넣을 축약 상태 (전체 스냅샷은 너무 큼)."""
        try:
            s = self._snapshot_fn() or {}
        except Exception:
            return {}
        keep = {}
        for key in ("mount", "camera", "filter", "weather", "sun", "safety"):
            if key in s:
                keep[key] = s[key]
        return keep

    def _record(self, action_type: str, actor: str, params: dict,
                input_state: dict, output_state: dict,
                success: bool, message: str) -> None:
        row = self._db.add(ActionLog(
            action_type=action_type, actor=actor,
            params_json=_dumps(params),
            input_state_json=_dumps(input_state),
            output_state_json=_dumps(output_state),
            success=success, message=message,
        ))
        self._events.action(row_to_dict(row))
        level = "info" if success else "error"
        detail = f" — {message}" if message and message != "ok" else ""
        self._events.log("action", f"{action_type} [{actor}] "
                         f"{'OK' if success else 'FAILED'}{detail}", level)

    async def run(self, action_type: str, actor: str,
                  params: dict | None = None,
                  func: Callable[[], Awaitable[Any]] | None = None,
                  preconditions: list[tuple[str, bool, str]] | None = None) -> Any:
        """preconditions: (이름, 통과 여부, 설명) 목록."""
        params = params or {}
        input_state = self._state()

        failed = [f"{name}: {detail}" for name, ok, detail in
                  (preconditions or []) if not ok]
        if failed:
            msg = "사전조건 실패 — " + "; ".join(failed)
            self._record(action_type, actor, params, input_state,
                         input_state, False, msg)
            raise ActionError(msg)

        try:
            data = await func() if func is not None else None
            success, message = True, "ok"
        except Exception as exc:
            data = None
            success, message = False, f"{type(exc).__name__}: {exc}"

        output_state = self._state()
        self._record(action_type, actor, params, input_state,
                     output_state, success, message)
        if not success:
            raise ActionError(message)
        return data
