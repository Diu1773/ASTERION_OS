"""테스트 공용 — 가짜 cfg/장비/버스 + 임시 DB.

자동 테스트가 없던 프로젝트에 안전 불변식 회귀 가드를 stdlib unittest로 고정한다.
실행: 프로젝트 루트에서  python -m unittest discover -s tests -t .
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from asterion.core.actions import ActionBus
from asterion.core.events import EventHub
from asterion.core.ontology import Db


class Cfg:
    """config.get(key, default) 스텁 — 키를 주면 그 값, 없으면 default."""

    def __init__(self, **kw):
        self._kw = kw

    def get(self, k, d=None):
        return self._kw.get(k, d)


def tmp_db() -> Db:
    return Db(Path(tempfile.mkdtemp()) / "test.db")


def new_bus(db: Db | None = None, events: EventHub | None = None) -> ActionBus:
    return ActionBus(db or tmp_db(), events or EventHub(), lambda: {})


class _Status:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeDevice:
    """status()가 연결 상태 등을 돌려주는 가짜 장비 (기본 connected=True)."""

    def __init__(self, **status):
        st = {"connected": True}
        st.update(status)
        self._status = _Status(**st)

    def status(self):
        return self._status


def run(coro):
    """비동기 코루틴을 동기 테스트에서 실행."""
    return asyncio.run(coro)
