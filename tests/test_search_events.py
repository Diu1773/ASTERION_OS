"""ToolKit.search_events — AI 에이전트 운영이력 자유 검색(L5 첫 수, 읽기전용).

ActionLog·Alert·Decision을 시간창 + 자유텍스트(공백=AND) + 종류로 걸러 시간역순 반환.
staticmethod라 ToolKit 인스턴스(필수 deps 6개) 없이 db만으로 검증한다.
"""

import unittest
from datetime import datetime, timedelta, timezone

from asterion.agent.toolkit import ToolKit
from asterion.core.ontology import ActionLog, Alert, Decision

from ._helpers import tmp_db


def _iso(dt):
    return dt.isoformat()


class TestSearchEvents(unittest.TestCase):
    def _seed(self):
        db = tmp_db()
        now = datetime.now(timezone.utc)
        recent = _iso(now - timedelta(hours=1))
        old = _iso(now - timedelta(days=30))
        db.add(ActionLog(action_type="dome_close", actor="watchtower", success=False,
                         message="셔터 닫기 실패 COM 타임아웃", utc=recent))
        db.add(ActionLog(action_type="mount_goto", actor="operator", success=True,
                         message="화성 슬루 완료", utc=recent))
        db.add(Alert(rule_id="weather_stale", level="warn",
                     title="기상 텔레메트리 stale", detail="120s 지연", utc=recent))
        db.add(Decision(source="orchestrator", recommendation="관측 중단",
                        outcome="stopped", utc=recent))
        db.add(ActionLog(action_type="dome_close", actor="watchtower", success=True,
                         message="옛 기록", utc=old))          # 창 밖(30일 전)
        return db

    def test_text_match_is_and(self):
        db = self._seed()
        ev = ToolKit.search_events(db, "닫기 실패", hours=72)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["kind"], "action")
        self.assertFalse(ev[0]["ok"])               # 실패 액션

    def test_kind_filter_alert_only(self):
        db = self._seed()
        ev = ToolKit.search_events(db, "", hours=72, kind="alert")
        self.assertEqual([e["kind"] for e in ev], ["alert"])
        self.assertEqual(ev[0]["rule"], "weather_stale")

    def test_time_window_excludes_old(self):
        db = self._seed()
        ev = ToolKit.search_events(db, "dome_close", hours=72, kind="action")
        self.assertEqual(len(ev), 1)                # recent 실패만 (old 성공은 창 밖)
        self.assertIn("타임아웃", ev[0]["message"])
        wide = ToolKit.search_events(db, "dome_close", hours=24 * 60, kind="action")
        self.assertEqual(len(wide), 2)              # 창 넓히면 옛 기록도

    def test_empty_query_returns_all_in_window(self):
        db = self._seed()
        ev = ToolKit.search_events(db, "", hours=72, kind="all")
        self.assertEqual(sorted(e["kind"] for e in ev),
                         ["action", "action", "alert", "decision"])   # old action 제외

    def test_sorted_newest_first(self):
        db = self._seed()
        ev = ToolKit.search_events(db, "dome_close", hours=24 * 60, kind="action")
        utcs = [e["utc"] for e in ev]
        self.assertEqual(utcs, sorted(utcs, reverse=True))

    def test_no_match_returns_empty(self):
        db = self._seed()
        self.assertEqual(ToolKit.search_events(db, "존재하지않는키워드xyz", hours=72), [])

    def test_none_db_safe(self):
        self.assertEqual(ToolKit.search_events(None, "x"), [])


if __name__ == "__main__":
    unittest.main()
