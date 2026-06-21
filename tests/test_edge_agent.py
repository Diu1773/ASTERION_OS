"""Weather Edge Agent — 무손실 store-and-forward 불변식.

핵심: durability-first 버퍼링, 네트워크 끊김 시 무손실(미전송 유지), 복구 시 관측시각순
drain, 실패→재시도(at-least-once), 크기상한 disk-full 방지, 전송완료 정리.
"""

import tempfile
import unittest
from pathlib import Path

from asterion.watchtower.edge_agent import EdgeBuffer, WeatherEdgeAgent

TS1 = "2026-01-01T00:00:01+00:00"
TS2 = "2026-01-01T00:00:02+00:00"
TS3 = "2026-01-01T00:00:03+00:00"


def rec(ts, t=12.0, src="edge_01"):
    return {"source_id": src, "timestamp": ts, "temperature_c": t}


class TestEdgeBuffer(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "spool.db"

    def test_append_and_pending_in_ts_order(self):
        b = EdgeBuffer(self.path)
        b.append(rec(TS2)); b.append(rec(TS1)); b.append(rec(TS3))
        self.assertEqual([r["timestamp"] for _, r in b.pending()], [TS1, TS2, TS3])

    def test_mark_sent_removes_from_pending(self):
        b = EdgeBuffer(self.path)
        b.append(rec(TS1)); b.append(rec(TS2))
        b.mark_sent([i for i, _ in b.pending()])
        self.assertEqual(b.pending(), [])
        self.assertEqual(b.stats(), {"pending": 0, "sent": 2})

    def test_size_cap_drops_oldest_unsent(self):
        b = EdgeBuffer(self.path, max_pending=3)
        for ts in (TS1, TS2, TS3, "2026-01-01T00:00:04+00:00", "2026-01-01T00:00:05+00:00"):
            b.append(rec(ts))
        self.assertLessEqual(b.stats()["pending"], 3)
        self.assertNotIn(TS1, [r["timestamp"] for _, r in b.pending()])  # 가장 오래된 것 버림

    def test_prune_sent_keeps_recent(self):
        b = EdgeBuffer(self.path, keep_sent=1)
        for ts in (TS1, TS2, TS3):
            b.append(rec(ts))
        b.mark_sent([i for i, _ in b.pending()])
        b.prune_sent()
        self.assertLessEqual(b.stats()["sent"], 1)


class TestEdgeAgent(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "spool.db"

    def _agent(self, source, link):
        shipped = []

        def ship(records):
            if link[0]:
                shipped.extend(records)
                return True
            return False

        ag = WeatherEdgeAgent(EdgeBuffer(self.path), source, ship, poll_s=0.0)
        return ag, shipped

    def test_happy_path_ships_and_acks(self):
        ag, shipped = self._agent(lambda: [rec(TS1), rec(TS2)], [True])
        st = ag.tick()
        self.assertEqual((st["collected"], st["shipped"], st["pending"]), (2, 2, 0))
        self.assertTrue(ag.linked)
        self.assertEqual(len(shipped), 2)

    def test_network_down_buffers_no_loss(self):
        ag, shipped = self._agent(lambda: [rec(TS1)], [False])
        st = ag.tick()
        self.assertEqual(st["shipped"], 0)
        self.assertEqual(st["pending"], 1)      # 버퍼에 남아 무손실
        self.assertFalse(ag.linked)
        self.assertEqual(shipped, [])

    def test_recovery_drains_backlog_in_time_order(self):
        link = [False]
        srcq = [[rec(TS1)], [rec(TS2)], [rec(TS3)]]
        ag, shipped = self._agent(lambda: (srcq.pop(0) if srcq else []), link)
        ag.tick(); ag.tick(); ag.tick()         # 끊긴 동안 3건 버퍼링
        self.assertEqual(ag.buf.stats()["pending"], 3)
        link[0] = True
        ag.tick()                               # 복구 → backlog drain
        self.assertEqual(ag.buf.stats()["pending"], 0)
        self.assertEqual([r["timestamp"] for r in shipped], [TS1, TS2, TS3])  # 관측시각순
        self.assertTrue(ag.linked)

    def test_resend_same_record_until_acked(self):
        link = [False]
        attempts = []

        def ship(records):
            attempts.append([r["timestamp"] for r in records])
            return link[0]

        b = EdgeBuffer(self.path)
        one = [[rec(TS1)]]
        ag = WeatherEdgeAgent(b, lambda: (one.pop(0) if one else []), ship)
        ag.tick()                               # 수집 1 + 전송 실패
        self.assertEqual(b.stats()["pending"], 1)
        link[0] = True
        ag.tick()                               # 같은 레코드 재시도 → 성공
        self.assertEqual(attempts[0], [TS1])
        self.assertEqual(attempts[-1], [TS1])   # 동일 레코드 재전송(서버 dedup이 멱등 보장)
        self.assertEqual(b.stats()["pending"], 0)


if __name__ == "__main__":
    unittest.main()
