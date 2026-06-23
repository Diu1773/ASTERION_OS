"""Pwi4Mount park 계열 — 표준 park 계약을 PWI4 HTTP 엔드포인트로 번역하는지 계약 검증.

실 PWI4 없이: _get(HTTP GET)을 레코더로 갈아끼워 park/unpark/find_home/set_park가
올바른 /mount/* 경로·파라미터를 때리는지, status()가 can_park/can_home을 정직 보고하는지 확인.
엔드포인트는 PlaneWave 공식 pwi4_client.py 기준(park=/mount/park, set_park=/mount/set_park_here,
find_home=/mount/find_home, unpark=두 축 /mount/enable).
"""

import unittest

from asterion.drivers.pwi4 import Pwi4Mount


class TestPwi4Park(unittest.TestCase):
    def _mount(self):
        m = Pwi4Mount("http://127.0.0.1:8220")
        self.calls = []

        def fake_get(path, **params):
            self.calls.append((path, params))
            return ""                      # 파킹 명령 응답 본문은 안 씀
        m._get = fake_get                  # 실 HTTP 대신 레코더
        return m

    def test_park_hits_mount_park(self):
        m = self._mount()
        m.park()
        self.assertEqual(self.calls, [("/mount/park", {})])

    def test_unpark_enables_both_axes(self):
        m = self._mount()
        m.unpark()
        self.assertEqual(self.calls,
                         [("/mount/enable", {"axis": 0}),
                          ("/mount/enable", {"axis": 1})])

    def test_find_home_hits_endpoint(self):
        m = self._mount()
        m.find_home()
        self.assertEqual(self.calls, [("/mount/find_home", {})])

    def test_set_park_hits_set_park_here(self):
        m = self._mount()
        m.set_park()
        self.assertEqual(self.calls, [("/mount/set_park_here", {})])

    def test_status_reports_can_park_and_home(self):
        # 표준 모델이 능력을 정직 보고 — UI 버튼·데드맨 분기가 이걸 본다(기존엔 미설정→False).
        m = self._mount()
        st = m.status()
        self.assertTrue(st.can_park)
        self.assertTrue(st.can_home)

    def test_no_longer_raises_notimplemented(self):
        # 회귀 가드 — 베이스 NotImplementedError가 더는 올라오지 않는다(번역 채워짐).
        m = self._mount()
        for fn in (m.park, m.unpark, m.find_home, m.set_park):
            try:
                fn()
            except NotImplementedError:
                self.fail(f"{fn.__name__}가 여전히 NotImplementedError")


if __name__ == "__main__":
    unittest.main()
