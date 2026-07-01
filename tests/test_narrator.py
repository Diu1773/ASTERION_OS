"""Narrator — 능동 내레이션(먼저 말하기). 발화 경보 → '관측+권고' 한 줄.

규칙 기반 순수 함수 + EventHub.alert() 통합(narrator 설정 시 advisor 로그+narration 이벤트).
사건 시에만 말하고(빈 경보=침묵), 추천 톤(실행 명령 아님).
"""

import unittest

from asterion.agent.narrator import Narrator
from asterion.core.events import EventHub


class TestNarrate(unittest.TestCase):
    def test_known_rule_template(self):
        line = Narrator.narrate({"rule_id": "weather_forecast_rain", "detail": "강수 65%"})
        self.assertIsNotNone(line)
        self.assertIn("강수 65%", line)
        self.assertIn("보류", line)

    def test_shutter_stuck_urgent(self):
        line = Narrator.narrate({"rule_id": "session_deadman_shutter_stuck",
                                 "detail": "슬릿 열림"})
        self.assertIn("현장", line)          # 즉시 현장 조치 권고

    def test_unknown_rule_falls_back_to_title_detail(self):
        line = Narrator.narrate({"rule_id": "brand_new_alert",
                                 "title": "새 경보", "detail": "상세정보"})
        self.assertIsNotNone(line)
        self.assertIn("새 경보", line)
        self.assertIn("상세정보", line)

    def test_empty_returns_none(self):
        self.assertIsNone(Narrator.narrate(None))
        self.assertIsNone(Narrator.narrate({}))
        self.assertIsNone(Narrator.narrate({"rule_id": "unknown"}))  # 할 말 없으면 침묵

    def test_recommends_not_acts(self):
        # 안전 원칙 가드 — 내레이션은 권고/확인 톤이지 스스로 실행하는 명령이 아니다.
        line = Narrator.narrate({"rule_id": "emergency_close", "detail": "강수 감지"})
        self.assertTrue(any(w in line for w in ("확인", "권고", "닫")))


class TestEventHubIntegration(unittest.TestCase):
    def test_alert_narrated_into_logbuffer(self):
        hub = EventHub()
        hub.narrator = Narrator.narrate
        hub.alert({"rule_id": "weather_forecast_rain", "title": "강수 예보",
                   "detail": "강수 65%", "level": "warn"})
        # 경보 로그 + advisor 내레이션 로그 둘 다 버퍼에(emit은 루프 없으면 no-op, 버퍼는 동기).
        advisor = [e for e in hub.log_buffer if e.get("source") == "advisor"]
        self.assertEqual(len(advisor), 1)
        self.assertIn("💬", advisor[0]["msg"])
        self.assertIn("보류", advisor[0]["msg"])

    def test_no_narrator_no_narration(self):
        hub = EventHub()                     # narrator 미설정
        hub.alert({"rule_id": "weather_stale", "title": "stale",
                   "detail": "d", "level": "warn"})
        self.assertFalse(any(e.get("source") == "advisor" for e in hub.log_buffer))


if __name__ == "__main__":
    unittest.main()
