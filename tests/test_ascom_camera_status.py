"""AscomCamera.status() — 노출 중 COM 점유로 인한 거짓 FAULT 플랩 방지(rank5).

단일 COM 워커가 StartExposure로 노출 내내(60~300s) 점유되면, 동시 status()의 submit().result()
가 그만큼 블록돼 샘플러 STATUS_TIMEOUT→_stuck→missing_required FAULT가 매 노출마다 뜬다(실측
ASCOM 자율운영의 신뢰성 파괴). 노출 중에는 COM을 건드리지 않고 직전 폴 캐시 + state='exposing'
으로 즉시 반환해야 한다. _call을 가짜로 덮어 COM 없이 로직만 검증한다(SIM에선 안 드러나는 결함).
"""

import unittest

from asterion.drivers.ascom import AscomCamera


class _FakeDev:
    """status/expose가 읽는 ASCOM 카메라 속성만 갖춘 가짜 디바이스."""

    def __init__(self, temp=-12.0, cooler=True, power=60.0):
        self.Connected = True
        self.CCDTemperature = temp
        self.CoolerOn = cooler
        self.CoolerPower = power
        # expose 경로용
        self.MaxBinX = 1
        self.CameraXSize = 2
        self.CameraYSize = 2
        self.BinX = self.BinY = 1
        self.NumX = self.NumY = 2
        self.StartX = self.StartY = 0
        self.ImageReady = True
        self.ImageArray = [[1, 2], [3, 4]]

    def StartExposure(self, seconds, light):
        self.started = (seconds, light)


def _cam(dev):
    cam = AscomCamera("")
    cam._dev = dev
    cam._call = lambda fn: fn()      # COM 워커 대신 인라인 실행(테스트)
    return cam


def _boom(fn):
    raise AssertionError("노출 중 status가 COM을 호출했다")


class TestAscomCameraExposureStatus(unittest.TestCase):
    def test_idle_status_reads_and_caches(self):
        cam = _cam(_FakeDev(temp=-12.0, cooler=True, power=60.0))
        st = cam.status()
        self.assertTrue(st.connected)
        self.assertEqual(st.state, "idle")
        self.assertAlmostEqual(st.ccd_temp_c, -12.0)
        self.assertAlmostEqual(cam._last_temp, -12.0)   # 캐시 갱신
        self.assertTrue(cam._last_cooler)

    def test_exposing_status_returns_cache_without_com(self):
        cam = _cam(_FakeDev(temp=-12.0, cooler=True, power=60.0))
        cam.status()                                    # 1회 폴 → 캐시 채움
        cam._state = "exposing"
        cam._call = _boom                               # 호출되면 테스트 실패
        st = cam.status()
        self.assertTrue(st.connected)                   # 미연결 아님 → 거짓 FAULT 차단
        self.assertEqual(st.state, "exposing")
        self.assertAlmostEqual(st.ccd_temp_c, -12.0)    # 캐시값

    def test_exposing_without_prior_poll_still_connected(self):
        # 첫 폴 전 노출이 시작돼도(캐시 None) connected=True/state=exposing → missing_required 아님.
        cam = _cam(_FakeDev())
        cam._state = "exposing"
        cam._call = _boom
        st = cam.status()
        self.assertTrue(st.connected)
        self.assertEqual(st.state, "exposing")
        self.assertIsNone(st.ccd_temp_c)

    def test_expose_sets_state_before_submit(self):
        # expose가 COM 제출 *전에* exposing으로 set → 동시 status가 즉시 캐시 경로(점유 전 set).
        cam = _cam(_FakeDev())
        seen = {}

        def fake_call(fn):
            seen["state"] = cam._state    # 제출 시점 state
            return fn()
        cam._call = fake_call
        cam.expose(0.01)
        self.assertEqual(seen["state"], "exposing")
        self.assertEqual(cam._state, "idle")            # 종료 후 복구


    def test_exposure_ends_resumes_com_poll(self):
        # rank24 — 노출 종료(_state=idle) 후 status()가 다시 COM 폴로 정상화(캐시 마스킹 해소).
        cam = _cam(_FakeDev(temp=-12.0))
        cam.status()                              # idle → COM 폴 + 캐시
        cam._state = "exposing"
        cam._call = _boom                         # 노출 중 COM 호출 금지
        self.assertEqual(cam.status().state, "exposing")
        cam._state = "idle"                       # 노출 종료(finally)
        called = []
        cam._call = lambda fn: (called.append(1) or fn())
        st = cam.status()
        self.assertTrue(called)                   # idle 복귀 → COM 폴 재개
        self.assertEqual(st.state, "idle")

    def test_expose_exception_restores_idle(self):
        # rank24 — expose가 COM 예외로 실패해도 _state='idle'로 복구(마스킹 창 고착 방지).
        cam = _cam(_FakeDev())

        def boom_call(fn):
            raise RuntimeError("COM fail")
        cam._call = boom_call
        with self.assertRaises(RuntimeError):
            cam.expose(0.01)
        self.assertEqual(cam._state, "idle")


if __name__ == "__main__":
    unittest.main()
