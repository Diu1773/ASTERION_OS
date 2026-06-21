"""ASTERION Weather Edge Agent — 간헐 네트워크에서 무손실 store-and-forward (로드맵 §7.4).

센서 PC에서 도는 경량 클라이언트. 읽은 값을 로컬 SQLite(WAL) 버퍼에 *먼저* 적재한 뒤
중앙 서버의 POST /api/weather/ingest로 배치 전송한다. 네트워크가 끊기면 버퍼에 쌓이고
복구되면 오래된 것부터(관측시각 순) 비운다 — at-least-once. 서버가 (source_id, utc)로 멱등
dedup하므로 재전송은 무해하다(중복은 무시됨).

설계 근거(deep-research 2026-06-21):
- bespoke JSONL spool+byte커서 대신 **SQLite+`sent` 플래그**(FlowFuse 패턴) — 서버리스·원자적·
  inode 문제 없음·쿼리가능. spool의 좋은 아이디어(durability-first·ack 후 정리·크기상한)는 계승.
- 표준 전달의미 = **at-least-once + 멱등 dedup** (exactly-once는 Two Generals/FLP로 불가).
- 필드 스키마는 ASCOM ObservingConditions 관례에 맞춘 §7 JSON
  (temperature_c·humidity_percent·wind_speed_ms·wind_dir_deg·rain·cloud_index ...).
- 버퍼는 **크기상한 필수**(disk-full 방지) — 초과 시 가장 오래된 미전송을 버리고 경고.

실센서 연결: source_fn을 직접 구현해 교체한다(USB 시리얼/CSV-tail/벤더앱). 기본은 sim.
실행: python -m asterion.watchtower.edge_agent --ingest-url http://SERVER:8000/api/weather/ingest
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Iterable

log = logging.getLogger("asterion.edge_agent")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spool(
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT    NOT NULL,           -- 관측 시각(ISO+TZ) = §7 timestamp (정렬 키)
  payload TEXT    NOT NULL,           -- §7 레코드 JSON
  sent    INTEGER NOT NULL DEFAULT 0  -- 0=미전송, 1=전송완료(ack 후)
);
CREATE INDEX IF NOT EXISTS ix_spool_unsent ON spool(sent, ts);
"""


class EdgeBuffer:
    """로컬 durable store-and-forward 버퍼 (SQLite WAL + sent 플래그).

    읽은 값을 *먼저* 여기 적재(durability-first)하고, 전송 성공(서버 ack) 후에만 sent=1로
    표시한다. 끊김 중엔 미전송으로 쌓이고, 복구 시 관측시각 오름차순으로 비운다(at-least-once).
    """

    def __init__(self, db_path, max_pending: int = 500_000, keep_sent: int = 2000):
        self.path = str(db_path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.max_pending = max(1, int(max_pending))
        self.keep_sent = max(0, int(keep_sent))
        self._c = sqlite3.connect(self.path, isolation_level=None,
                                  check_same_thread=False)
        self._c.execute("PRAGMA journal_mode=WAL")    # 동시읽기 + 크래시안전
        self._c.execute("PRAGMA synchronous=NORMAL")  # WAL에서 내구성/성능 균형(명시)
        self._c.executescript(_SCHEMA)

    def append(self, record: dict) -> None:
        ts = str(record.get("timestamp") or record.get("utc") or "")
        # 크기상한(disk-full 방지) — 미전송이 상한 이상이면 가장 오래된 미전송부터 버린다.
        n = self._c.execute("SELECT COUNT(*) FROM spool WHERE sent=0").fetchone()[0]
        if n >= self.max_pending:
            self._c.execute(
                "DELETE FROM spool WHERE id=(SELECT id FROM spool WHERE sent=0 "
                "ORDER BY ts ASC, id ASC LIMIT 1)")
            log.warning("edge buffer full (%d ≥ %d) — dropped oldest unsent",
                        n, self.max_pending)
        self._c.execute("INSERT INTO spool(ts, payload, sent) VALUES(?,?,0)",
                        (ts, json.dumps(record, ensure_ascii=False)))

    def pending(self, limit: int = 500) -> list[tuple[int, dict | None]]:
        rows = self._c.execute(
            "SELECT id, payload FROM spool WHERE sent=0 ORDER BY ts ASC, id ASC LIMIT ?",
            (max(1, int(limit)),)).fetchall()
        out: list[tuple[int, dict | None]] = []
        for rid, payload in rows:
            try:
                out.append((rid, json.loads(payload)))
            except Exception:
                out.append((rid, None))   # 손상 행 → 호출부가 sent 처리해 건너뜀
        return out

    def mark_sent(self, ids: Iterable[int]) -> None:
        ids = [(int(i),) for i in ids]
        if ids:
            self._c.executemany("UPDATE spool SET sent=1 WHERE id=?", ids)

    def prune_sent(self) -> int:
        """전송 완료 행은 최근 keep_sent개만 남기고 정리(디스크 회수)."""
        cur = self._c.execute(
            "DELETE FROM spool WHERE sent=1 AND id NOT IN "
            "(SELECT id FROM spool WHERE sent=1 ORDER BY id DESC LIMIT ?)",
            (self.keep_sent,))
        return cur.rowcount or 0

    def stats(self) -> dict:
        p = self._c.execute("SELECT COUNT(*) FROM spool WHERE sent=0").fetchone()[0]
        s = self._c.execute("SELECT COUNT(*) FROM spool WHERE sent=1").fetchone()[0]
        return {"pending": int(p), "sent": int(s)}

    def close(self) -> None:
        try:
            self._c.close()
        except Exception:
            pass


def http_shipper(ingest_url: str, token: str | None = None, timeout: float = 10.0
                 ) -> Callable[[list[dict]], bool]:
    """기본 shipper — §7 레코드 배치를 POST /api/weather/ingest. 2xx면 True(ack)."""
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _ship(records: list[dict]) -> bool:
        try:
            r = httpx.post(ingest_url, json=records, headers=headers, timeout=timeout)
            return 200 <= r.status_code < 300
        except Exception as exc:    # 네트워크 끊김 등 → False(ack 안 함, 다음 틱 재시도)
            log.warning("ship failed: %s", exc)
            return False

    return _ship


class WeatherEdgeAgent:
    """수집 → 버퍼 적재 → 배치 전송 루프. ship 실패(네트워크 끊김)면 ack하지 않아 무손실."""

    def __init__(self, buffer: EdgeBuffer, source_fn: Callable[[], Iterable[dict]],
                 ship_fn: Callable[[list[dict]], bool], *,
                 batch_size: int = 500, poll_s: float = 2.0):
        self.buf = buffer
        self.source_fn = source_fn      # () -> Iterable[§7 record]
        self.ship_fn = ship_fn          # (list[record]) -> bool (서버 ack 성공 여부)
        self.batch_size = max(1, int(batch_size))
        self.poll_s = float(poll_s)
        self.linked: bool | None = None  # 마지막 링크 상태(None=미상, True=연결, False=끊김)
        self._stop = False

    def collect(self) -> int:
        n = 0
        for rec in (self.source_fn() or []):
            if isinstance(rec, dict):
                self.buf.append(rec)
                n += 1
        return n

    def drain(self) -> int:
        shipped = 0
        while not self._stop:
            batch = self.buf.pending(self.batch_size)
            if not batch:
                break
            good = [(rid, rec) for rid, rec in batch if rec is not None]
            corrupt = [rid for rid, rec in batch if rec is None]
            if corrupt:
                self.buf.mark_sent(corrupt)         # 손상 행은 건너뛰어 진행 막지 않음
            if not good:
                continue
            if self.ship_fn([rec for _, rec in good]):
                self.buf.mark_sent([rid for rid, _ in good])
                shipped += len(good)
                if self.linked is False:
                    log.info("link recovered — draining backlog (%d)", len(good))
                self.linked = True
            else:
                if self.linked is not False:
                    log.warning("link down — buffering locally")
                self.linked = False
                break    # 끊김 → ack 안 함 → 다음 틱 같은 레코드 재시도(서버 dedup이 멱등 보장)
        return shipped

    def tick(self) -> dict:
        collected = self.collect()
        shipped = self.drain()
        self.buf.prune_sent()
        return {"collected": collected, "shipped": shipped,
                "linked": self.linked, **self.buf.stats()}

    def run_forever(self) -> None:
        while not self._stop:
            try:
                self.tick()
            except Exception:
                log.exception("edge agent tick error")
            time.sleep(self.poll_s)

    def stop(self) -> None:
        self._stop = True


def sim_source(source_id: str = "sim_edge_01") -> Callable[[], list[dict]]:
    """테스트/데모용 — 랜덤워크 기상 §7 레코드 1건 생성(실센서 source_fn으로 교체)."""
    import datetime as _dt
    import random as _r

    state = {"t": 12.0, "h": 60.0, "w": 1.5}

    def _src() -> list[dict]:
        state["t"] += _r.uniform(-0.3, 0.3)
        state["h"] = min(100.0, max(0.0, state["h"] + _r.uniform(-2.0, 2.0)))
        state["w"] = max(0.0, state["w"] + _r.uniform(-0.5, 0.5))
        return [{
            "source_id": source_id,
            "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "temperature_c": round(state["t"], 2),
            "humidity_percent": round(state["h"], 1),
            "wind_speed_ms": round(state["w"], 2),
            "rain": False,
        }]

    return _src


def main(argv=None) -> None:
    import argparse

    p = argparse.ArgumentParser(description="ASTERION weather edge agent (store-and-forward)")
    p.add_argument("--ingest-url", default="http://127.0.0.1:8000/api/weather/ingest")
    p.add_argument("--db", default="edge_spool.db", help="로컬 버퍼 SQLite 경로")
    p.add_argument("--token", default=None, help="ingest Authorization Bearer(선택)")
    p.add_argument("--poll", type=float, default=2.0)
    p.add_argument("--source-id", default="edge_01")
    p.add_argument("--max-pending", type=int, default=500_000)
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    buf = EdgeBuffer(a.db, max_pending=a.max_pending)
    agent = WeatherEdgeAgent(buf, sim_source(a.source_id),
                             http_shipper(a.ingest_url, token=a.token), poll_s=a.poll)
    log.info("edge agent start — ingest=%s db=%s (Ctrl+C to stop)", a.ingest_url, a.db)
    try:
        agent.run_forever()
    except KeyboardInterrupt:
        agent.stop()
        buf.close()


if __name__ == "__main__":
    main()
