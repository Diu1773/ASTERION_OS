"""DavisSerialWeather — 콘솔 직결 LOOP 파싱·단위변환·결측·신선도.

시리얼 통신은 현장 검증 대상이라, 합성 LOOP 패킷으로 *파싱·단위·sentinel·신선도 로직*만
단위테스트로 고정한다(청람 SerialDome와 동형: 프로토콜은 코드 검증, HW는 현장).
"""

import struct
import unittest

from asterion.drivers.davis_serial import DavisSerialWeather, parse_loop


def _loop(*, baro=29921, in_t=720, in_h=41, out_t=752, wind=3, wdir=68, out_h=54, rate=0):
    """합성 LOOP 패킷(99B). baro=0.001inHg, in_t/out_t=0.1°F, wind=mph, wdir=deg, rate=clicks/hr."""
    pkt = bytearray(99)
    pkt[0:3] = b"LOO"
    struct.pack_into("<H", pkt, 7, baro & 0xFFFF)
    struct.pack_into("<h", pkt, 9, in_t)
    pkt[11] = in_h & 0xFF
    struct.pack_into("<h", pkt, 12, out_t)
    pkt[14] = wind & 0xFF
    struct.pack_into("<H", pkt, 16, wdir & 0xFFFF)
    pkt[33] = out_h & 0xFF
    struct.pack_into("<H", pkt, 41, rate & 0xFFFF)
    return bytes(pkt)


class TestParseLoop(unittest.TestCase):
    def test_basic_values_and_units(self):
        v = parse_loop(_loop(out_t=752, out_h=54, wind=3, wdir=68, baro=29921, rate=0))
        self.assertAlmostEqual(v["temp_c"], 24.0, places=1)       # 75.2°F → 24°C
        self.assertAlmostEqual(v["humidity"], 54.0)
        self.assertAlmostEqual(v["wind_ms"], 1.34, places=2)      # 3 mph
        self.assertAlmostEqual(v["wind_dir_deg"], 68.0)
        self.assertAlmostEqual(v["pressure_hpa"], 1013.3, places=0)
        self.assertFalse(v["rain"])

    def test_dew_point_below_temp(self):
        v = parse_loop(_loop(out_t=752, out_h=54))
        self.assertIsNotNone(v["temp_c"])
        self.assertIsNotNone(parse_loop(_loop())["humidity"])
        # 이슬점은 기온보다 낮아야(물리), 그리고 sane 범위.
        from asterion.drivers.davis_serial import _dew_point_c
        dp = _dew_point_c(v["temp_c"], v["humidity"])
        self.assertIsNotNone(dp)
        self.assertLess(dp, v["temp_c"])

    def test_rain_rate_positive_is_raining(self):
        self.assertTrue(parse_loop(_loop(rate=5))["rain"])
        self.assertFalse(parse_loop(_loop(rate=0))["rain"])

    def test_sentinels_become_none(self):
        v = parse_loop(_loop(out_t=0x7FFF, out_h=255, wind=255,
                             wdir=0xFFFF, baro=0xFFFF, rate=0xFFFF))
        self.assertIsNone(v["temp_c"])
        self.assertIsNone(v["humidity"])
        self.assertIsNone(v["wind_ms"])
        self.assertIsNone(v["wind_dir_deg"])
        self.assertIsNone(v["pressure_hpa"])
        self.assertFalse(v["rain"])           # rain sentinel → 강수 아님

    def test_wind_dir_zero_is_none(self):
        # Davis는 풍향 무응답을 0으로 표기 → None.
        self.assertIsNone(parse_loop(_loop(wdir=0))["wind_dir_deg"])

    def test_rejects_bad_packets(self):
        self.assertIsNone(parse_loop(None))
        self.assertIsNone(parse_loop(b"XXX"))                  # 너무 짧음
        self.assertIsNone(parse_loop(b"BAD" + bytes(96)))      # 헤더 불일치(길이 OK)


class TestSerialWeatherFreshness(unittest.TestCase):
    def test_valid_packet_connected(self):
        d = DavisSerialWeather(port="")
        s = d._status_from_packet(_loop(out_t=752, out_h=54))
        self.assertTrue(s.connected)
        self.assertAlmostEqual(s.temp_c, 24.0, places=1)
        self.assertIsNotNone(s.dew_point_c)
        self.assertIsNone(s.cloud_score)          # ISS엔 운량 센서 없음
        self.assertIsNotNone(s.reading_age_s)

    def test_bad_packet_disconnected(self):
        d = DavisSerialWeather(port="")
        self.assertFalse(d._status_from_packet(None).connected)
        self.assertFalse(d._status_from_packet(b"XXX").connected)

    def test_concurrent_read_returns_cache(self):
        # rank9 — read 진행 중(lock 보유)이면 새 시리얼 트랜잭션을 시작하지 않고 직전 캐시 반환.
        d = DavisSerialWeather(port="")
        d._ser = object()                              # 미연결 아님
        d._request_loop = lambda: _loop(out_t=752)     # 정상 read
        s1 = d.read()
        self.assertTrue(s1.connected)
        d._lock.acquire()                              # 다른 read '진행 중' 흉내
        try:
            d._request_loop = lambda: (_ for _ in ()).throw(
                AssertionError("진행 중인데 새 트랜잭션 시작"))
            s2 = d.read()
        finally:
            d._lock.release()
        self.assertTrue(s2.connected)                  # 캐시 기반(connected 유지)
        self.assertIn("진행 중", s2.detail)

    def test_freshness_tracks_packet_change(self):
        # 동일 패킷 반복 → age 누적(frozen 감지), 다른 패킷 → age 리셋(fresh).
        import time
        d = DavisSerialWeather(port="")
        p1 = _loop(out_t=752)
        d._status_from_packet(p1)
        self.assertEqual(d._last_raw, p1)                 # 패킷 기록
        # 변화시각을 100s 과거로 주입 → 동일 패킷이면 갱신 안 돼 age가 누적돼야(frozen).
        d._last_change_mono = time.monotonic() - 100.0
        s_frozen = d._status_from_packet(p1)
        self.assertEqual(d._last_raw, p1)                 # 동일 → _last_raw 그대로
        self.assertGreaterEqual(s_frozen.reading_age_s, 90.0)
        # 다른 패킷 → 변화 감지 → age 리셋.
        s_fresh = d._status_from_packet(_loop(out_t=760))
        self.assertEqual(d._last_raw, _loop(out_t=760))
        self.assertLess(s_fresh.reading_age_s, 1.0)


class _FakeSer:
    """probe용 가짜 시리얼 — responds=True면 LOOP 응답, 아니면 NAK."""

    def __init__(self, responds):
        self._responds = responds
        self.closed = False

    def reset_input_buffer(self):
        pass

    def write(self, b):
        pass

    def read(self, n=1):
        return (b"\x06" + _loop()) if self._responds else (b"\x15" * 8)

    def close(self):
        self.closed = True


class TestAutodetect(unittest.TestCase):
    def test_picks_responding_port_and_excludes(self):
        # 가용 포트 중 LOOP에 응답하는 포트를 채택, 제외 포트(돔)엔 LOOP 안 보냄.
        d = DavisSerialWeather(port="auto", exclude_ports=("COM3",))
        d._list_ports = lambda: ["COM3", "COM5", "COM8"]
        opened = []

        def fake_open(dev):
            opened.append(dev)
            return _FakeSer(responds=(dev == "COM8"))
        d._open = fake_open
        self.assertEqual(d._autodetect(), "COM8")
        self.assertNotIn("COM3", opened)          # 제외 포트는 열지도(probe하지도) 않음

    def test_returns_none_when_no_davis(self):
        d = DavisSerialWeather(port="auto")
        d._list_ports = lambda: ["COM5", "COM6"]
        d._open = lambda dev: _FakeSer(responds=False)
        self.assertIsNone(d._autodetect())


if __name__ == "__main__":
    unittest.main()
