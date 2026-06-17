"""필터 교체 시 포커서 자동 오프셋 보정 — per-filter focus offset.

필터마다 유리 두께가 달라 초점면이 이동한다. 운영자 Setup 표
(setup.filterwheel.filters[].focus_offset, steps)에 기준 필터(보통 L=0) 대비
오프셋을 적어두면, 필터 교체 시 (off(new) - off(prev))만큼 포커서를 자동으로 옮겨
재포커싱 없이 핀트를 유지한다. 표준 기능 (MaximDL/NINA, ASCOM FocusOffsets).

상태 없는 델타 방식: '직전 필터'는 교체 직전의 현재 필터 인덱스로 잡으므로 별도
영속 상태가 필요 없다 — 모든 필터 교체가 이 경로를 지나는 한 상대 오프셋이 일관되게
유지된다(절대 초점은 오토포커스가 잡고, 오프셋은 그 위의 상대값).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


def filter_focus_offset(cfg: Any, index: int) -> int:
    """필터 index의 포커스 오프셋(steps). 운영자 Setup 표 기준, 없으면 0.
    (Setup 표는 연결 시 드라이버 FocusOffsets로 시드되므로 이게 단일 소스다.)"""
    filters = cfg.get("setup.filterwheel.filters", None)
    if isinstance(filters, list) and 0 <= index < len(filters):
        f = filters[index]
        if isinstance(f, dict) and f.get("focus_offset") is not None:
            try:
                return int(f["focus_offset"])
            except (TypeError, ValueError):
                pass
    return 0


async def apply_filter_focus_offset(
    cfg: Any,
    drivers: dict,
    run_action: Callable[[str, dict, Callable[[], Awaitable[Any]]], Awaitable[Any]],
    prev_index: int | None,
    new_index: int,
) -> dict | None:
    """필터 prev→new 교체에 따른 포커서 보정. delta = off(new) - off(prev).
    포커서 미연결/미지원이거나 delta==0이면 아무 것도 안 하고 None을 돌려준다.

    run_action(name, params, fn): 감사 로깅 래퍼 (bus.run / orchestrator._action 류 —
    actor는 래퍼가 결정). 호출부가 best-effort로 감싸 실패가 필터 교체를 막지 않게 한다.
    """
    if prev_index is None or prev_index == new_index:
        return None
    delta = filter_focus_offset(cfg, new_index) - filter_focus_offset(cfg, prev_index)
    if delta == 0:
        return None
    foc = drivers.get("focuser")
    if foc is None:
        return None
    st = await asyncio.to_thread(foc.status)
    if not getattr(st, "connected", False) or st.position is None:
        return None
    max_pos = int(getattr(st, "max_position", 60000) or 60000)
    target = max(0, min(max_pos, int(st.position) + delta))
    await run_action(
        "focus_offset_apply",
        {"delta": delta, "target": target, "filter_index": new_index},
        lambda: asyncio.to_thread(foc.move_to, target),
    )
    return {"delta": delta, "target": target}
