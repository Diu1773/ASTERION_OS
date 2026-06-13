"""ASCOM Chooser로 카메라/필터휠 드라이버를 선택해 ProgID를 출력한다.

Maxim DL의 드라이버 드롭다운과 동일한 선택창이 뜬다.
출력된 ProgID를 asterion/config.toml의 [drivers.ascom]에 붙여넣을 것.

요구사항: ASCOM Platform + pywin32 (.venv에 설치:
    .venv\\Scripts\\pip install pywin32)
"""

from __future__ import annotations

import sys


def choose(device_type: str) -> str:
    import win32com.client
    chooser = win32com.client.Dispatch("ASCOM.Utilities.Chooser")
    chooser.DeviceType = device_type
    return chooser.Choose("")


def main() -> int:
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        print("pywin32가 없습니다:  .venv\\Scripts\\pip install pywin32")
        return 1
    for device in ("Camera", "FilterWheel"):
        print(f"\n[{device}] 선택창이 뜹니다 — 취소하면 건너뜀")
        try:
            progid = choose(device)
        except Exception as exc:
            print(f"  Chooser 실행 실패 (ASCOM Platform 설치 확인): {exc}")
            return 1
        if progid:
            key = "camera_progid" if device == "Camera" else "filterwheel_progid"
            print(f"  → config.toml [drivers.ascom] {key} = \"{progid}\"")
        else:
            print("  (선택 안 함)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
