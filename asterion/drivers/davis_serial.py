"""Davis Vantage 콘솔 직결 시리얼 기상 드라이버 (WeatherLink 게이트웨이 없이).

콘솔에 USB/RS-232로 직접 붙어 LOOP 패킷(99바이트)을 받는다. WeatherLink Live(별도 HTTP
게이트웨이 제품)와 달리 raw 패킷을 직접 파싱하므로 신선도(reading_age_s)도 직접 산출한다 —
이게 frozen 센서(connected=True인데 고착값) 탐지의 핵심이다.

포트는 기본 "auto" — 가용 시리얼 포트를 훑어 LOOP에 응답하는 포트를 Davis로 자동채택한다(COM
번호는 USB 포트/재설치로 바뀌어 불안정하므로 하드코딩보다 견고). 다른 시리얼 장치(돔 등)는
exclude로 제외해 LOOP를 보내지 않는다. 필요하면 drivers.davis_serial.port에 포트를 직접 지정.

LOOP 패킷 오프셋(Vantage Pro2, 리틀엔디안):
  barometer u16@7 (0.001 inHg) · inside temp s16@9 (0.1°F) · inside hum u8@11 ·
  outside temp s16@12 (0.1°F) · wind u8@14 (mph) · wind dir u16@16 (deg, 0=결측) ·
  outside hum u8@33 · rain rate u16@41 (clicks/hr).
결측 sentinel: u8=255, u16=0xFFFF, s16=0x7FFF (콘솔이 ISS 무응답 시 찍는 dash 값).
단위는 표준(°C·m/s·%·hPa)으로 정규화. ISS엔 운량 센서 없음 → cloud_score=None(정직).

신선도(reading_age_s): 콘솔은 실시간 응답이라 정상 read는 LOOP 패킷이 매번 바뀐다(기압이
0.001 inHg 해상도로 미세 변동). 그래서 *패킷이 마지막으로 바뀐 뒤 경과*를 age로 보고하면 —
콘솔이 멈춰 동일 바이트를 계속 돌려주는 고착(버퍼/펌웨어 행)에서 age가 누적돼 stale 게이트가
fail-closed로 닫는다. 고요한 맑은 밤에도 기압 미세변동이 있어 거짓 stale 위험이 낮다.
ISS만 죽고 콘솔이 살아 outside 필드를 dash하면 그 필드는 sentinel→None으로 정직 보고된다.

실기 시리얼 통신(포트·LOOP 응답)은 현장 검증 대상 — 파싱·단위·신선도 로직은 단위테스트로 고정.
청람 SerialDome와 동형(프로토콜은 코드로, HW는 현장).
"""

from __future__ import annotations

import struct
import time

from .base import WeatherDriver, WeatherStatus

_LOOP_LEN = 99


def _read(pkt: bytes, off: int, fmt: str, sentinel: int) -> int | None:
    """오프셋에서 리틀엔디안 정수 읽기. sentinel(결측 마커)이면 None."""
    raw = struct.unpack_from(fmt, pkt, off)[0]
    return None if raw == sentinel else raw


def _dew_point_c(temp_c: float | None, rh: float | None) -> float | None:
    """Magnus 식으로 온도·상대습도에서 이슬점(°C). LOOP엔 이슬점이 없어 계산한다."""
    if temp_c is None or rh is None or rh <= 0:
        return None
    import math
    a, b = 17.62, 243.12
    gamma = math.log(rh / 100.0) + (a * temp_c) / (b + temp_c)
    return round((b * gamma) / (a - gamma), 2)


def parse_loop(pkt: bytes | None) -> dict | None:
    """LOOP 패킷(≥99B, 'LOO' 시작)을 표준단위 dict로. 형식 불일치면 None.

    순수 함수 — 시리얼 없이 단위테스트 가능. 결측 필드는 sentinel→None.
    """
    if not pkt or len(pkt) < _LOOP_LEN or pkt[:3] != b"LOO":
        return None
    baro = _read(pkt, 7, "<H", 0xFFFF)        # 0.001 inHg
    in_t = _read(pkt, 9, "<h", 0x7FFF)        # 0.1 °F
    in_h = _read(pkt, 11, "B", 255)
    out_t = _read(pkt, 12, "<h", 0x7FFF)      # 0.1 °F
    wind = _read(pkt, 14, "B", 255)           # mph
    wdir = _read(pkt, 16, "<H", 0xFFFF)       # deg (0=무응답)
    out_h = _read(pkt, 33, "B", 255)
    rate = _read(pkt, 41, "<H", 0xFFFF)       # clicks/hr

    def f10_to_c(v):
        return None if v is None else round((v / 10.0 - 32.0) * 5.0 / 9.0, 2)

    return {
        "temp_c": f10_to_c(out_t),
        "humidity": None if out_h is None else float(out_h),
        "wind_ms": None if wind is None else round(wind * 0.44704, 2),   # mph→m/s
        "wind_dir_deg": None if (wdir is None or wdir == 0) else float(wdir),
        "pressure_hpa": None if baro is None else round(baro * 0.0338638866667, 2),
        "rain": rate is not None and rate > 0,
        "inside_temp_c": f10_to_c(in_t),
        "inside_humidity": None if in_h is None else float(in_h),
    }


class DavisSerialWeather(WeatherDriver):
    def __init__(self, port: str = "auto", baud: int = 19200, exclude_ports=()):
        # port="auto"(또는 "") → 가용 포트를 훑어 LOOP에 응답하는 포트를 Davis로 자동채택.
        # COM 번호는 USB 포트/재설치로 바뀌므로(충돌은 아니나 불안정) 하드코딩 대신 자동감지가 견고.
        self._port = str(port).strip()
        self._baud = int(baud)
        self._exclude = tuple(p for p in (exclude_ports or ()) if p)  # 다른 시리얼 장치(돔 등) 제외
        self._ser = None
        self._resolved_port = ""
        self._name = "Davis Vantage (serial)"
        self._last_raw: bytes | None = None        # 직전 LOOP 바이트(변화 감지)
        self._last_change_mono: float | None = None  # 패킷이 마지막으로 바뀐 monotonic

    # ---------- 포트 자동감지 (probe) ----------

    def _list_ports(self) -> list[str]:           # 테스트에서 주입 가능
        from serial.tools import list_ports
        return [p.device for p in list_ports.comports()]

    def _open(self, dev: str):                    # 테스트에서 주입 가능
        import serial
        return serial.Serial(
            dev, baudrate=self._baud, bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=2.0)

    @staticmethod
    def _responds_loop(ser) -> bool:
        """포트가 Davis LOOP에 응답하면 True(자동감지 판별). Davis가 아니면 무응답/형식불일치."""
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        ser.write(b"\n")
        ser.read(2)                               # 깨우기 ACK(\n\r) 소비
        ser.write(b"LOOP 1\n")
        buf = ser.read(256) or b""
        i = buf.find(b"LOO")
        return i != -1 and len(buf[i:i + _LOOP_LEN]) >= _LOOP_LEN

    def _autodetect(self) -> str | None:
        """exclude를 뺀 가용 포트를 훑어 LOOP에 응답하는 첫 포트를 반환(없으면 None).
        제외 포트(돔 등 다른 시리얼 장치)엔 LOOP를 보내지 않는다."""
        for dev in self._list_ports():
            if dev in self._exclude:
                continue
            ser = None
            try:
                ser = self._open(dev)
                if self._responds_loop(ser):
                    return dev
            except Exception:
                continue
            finally:
                try:
                    if ser is not None:
                        ser.close()
                except Exception:
                    pass
        return None

    def connect(self) -> None:
        port = self._port
        if port in ("", "auto"):
            port = self._autodetect()
            if not port:
                raise RuntimeError(
                    "Davis 시리얼 포트 자동감지 실패 — LOOP에 응답하는 포트 없음 "
                    "(연결·전원 확인, 또는 drivers.davis_serial.port에 직접 지정)")
        import serial   # real 모드에서만 import(테스트/sim 무의존)
        self._ser = serial.Serial(
            port, baudrate=self._baud, bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=2.5)
        self._resolved_port = port
        try:                                  # 콘솔 깨우기(best-effort)
            self._ser.write(b"\n")
            self._ser.read(2)
        except Exception:
            pass

    def _request_loop(self) -> bytes | None:
        s = self._ser
        s.reset_input_buffer()
        s.write(b"LOOP 1\n")
        buf = s.read(256)
        i = buf.find(b"LOO")
        if i == -1:
            return None
        pkt = buf[i:i + _LOOP_LEN]
        return pkt if len(pkt) >= _LOOP_LEN else None

    def read(self) -> WeatherStatus:
        if self._ser is None:
            return WeatherStatus(connected=False, detail="미연결", device_name=self._name)
        try:
            pkt = self._request_loop()
        except Exception as exc:
            return WeatherStatus(connected=False, detail=f"시리얼 오류: {exc}",
                                 device_name=self._name)
        return self._status_from_packet(pkt)

    def _status_from_packet(self, pkt: bytes | None) -> WeatherStatus:
        """LOOP 패킷 → WeatherStatus(+신선도). 시리얼 없이 단위테스트 가능."""
        vals = parse_loop(pkt)
        if vals is None:
            return WeatherStatus(connected=False, detail="LOOP 패킷 없음",
                                 device_name=self._name)
        now = time.monotonic()
        # 신선도: 패킷 바이트가 바뀌면 갱신, 동일하면 누적(콘솔 고착=동일 바이트 반복 감지).
        # 살아있는 콘솔은 기압 미세변동으로 매번 패킷이 달라 age≈0 유지.
        if pkt != self._last_raw:
            self._last_raw = pkt
            self._last_change_mono = now
        age = 0.0 if self._last_change_mono is None else round(now - self._last_change_mono, 1)
        return WeatherStatus(
            connected=True,
            temp_c=vals["temp_c"], humidity=vals["humidity"],
            dew_point_c=_dew_point_c(vals["temp_c"], vals["humidity"]),
            wind_ms=vals["wind_ms"], wind_dir_deg=vals["wind_dir_deg"],
            cloud_score=None, rain=vals["rain"],
            reading_age_s=age, detail="Davis LOOP(serial)", device_name=self._name)

    def close(self) -> None:
        try:
            if self._ser is not None:
                self._ser.close()
        finally:
            self._ser = None
