"""UI 사운드 다듬기 — 원본 Windows wav를 트림·페이드·정규화해 이벤트 파일로 굽는다.

원본(asterion/web/sounds/*.wav, 시청용 보존)을 읽어:
  1) 앞뒤 무음 트림(이벤트 즉시 반응)
  2) 짧은 페이드 인/아웃(클릭/팝 제거, 끝 깔끔)
  3) 피크 정규화(-1 dBFS)로 파일 간 음량 통일
한 뒤 sounds/ui/<event>.wav 로 출력한다. 이벤트별 '체감 음량'은 app.js에서 곱한다.

재가공이 필요하면:  python tools/process_sounds.py
"""
from pathlib import Path
import wave
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "asterion" / "web" / "sounds"
OUT = SRC / "ui"

# 원본 클린명 -> 이벤트 출력명 (사용자 확정 매핑)
MAP = {
    "ding-classic.wav":    "capture.wav",     # 촬영(프레임 저장)
    "chimes.wav":          "success.wav",     # 시퀀스/저장 완료
    "notify-legacy.wav":   "disconnect.wav",  # 카메라/연결 해제
    "chord.wav":           "error.wav",       # 정지/오류
    "hardware-insert.wav": "connect.wav",     # 연결됨
    "exclamation.wav":     "warn.wav",        # 경고(safety)
    "critical-stop.wav":   "critical.wav",    # 위험(critical)
}


def read_wav(p):
    with wave.open(str(p), "rb") as w:
        n, sw, fr, nf = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(nf)
    if sw == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported sample width: {sw} bytes ({p.name})")
    return data.reshape(-1, n), fr, n


def write_wav(p, data, fr):
    i16 = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(p), "wb") as w:
        w.setnchannels(data.shape[1])
        w.setsampwidth(2)
        w.setframerate(fr)
        w.writeframes(i16.tobytes())


def process(data, fr):
    mono = np.max(np.abs(data), axis=1)
    peak = float(mono.max()) or 1.0
    thr = peak * 0.004                                   # -48 dB 기준 무음 판정
    idx = np.where(mono > thr)[0]
    if len(idx):
        start = max(0, idx[0] - int(fr * 0.003))         # 앞 3ms 여유
        end = min(len(mono), idx[-1] + int(fr * 0.010))  # 뒤 10ms 여유(페이드 공간)
        data = data[start:end].copy()
    nf = len(data)
    fin = min(int(fr * 0.004), nf // 2)                  # 4ms 페이드인
    fout = min(int(fr * 0.05), nf // 2)                  # 50ms 페이드아웃
    if fin:
        data[:fin] *= np.linspace(0.0, 1.0, fin)[:, None]
    if fout:
        data[-fout:] *= np.linspace(1.0, 0.0, fout)[:, None]
    pk = float(np.max(np.abs(data))) or 1.0
    return data * (0.89 / pk)                            # -1 dBFS 피크 정규화


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for src, out in MAP.items():
        sp = SRC / src
        if not sp.exists():
            print(f"!! missing source: {src}")
            continue
        data, fr, n = read_wav(sp)
        before = len(data)
        data = process(data, fr)
        write_wav(OUT / out, data, fr)
        print(f"{src:22s} -> ui/{out:14s} {fr}Hz {n}ch  {before} -> {len(data)} samples")


if __name__ == "__main__":
    main()
