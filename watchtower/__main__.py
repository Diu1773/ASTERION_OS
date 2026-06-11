"""실행: python -m watchtower [--config 경로] [--host H] [--port P]"""

from __future__ import annotations

import argparse
import os
import sys

import uvicorn

from .config import Config


def main() -> None:
    # 한국어 Windows 콘솔(cp949)에서도 UTF-8 출력이 깨지지 않게
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="watchtower")
    ap.add_argument("--config", help="config.toml 경로 (기본: 패키지 내장)")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    if args.config:
        os.environ["WATCHTOWER_CONFIG"] = args.config
    cfg = Config.load(os.environ.get("WATCHTOWER_CONFIG"))
    host = args.host or str(cfg.get("server.host", "127.0.0.1"))
    port = args.port or int(cfg.get("server.port", 8520))

    print(f"Watchtower — http://{host}:{port}  (Ctrl+C로 종료)")
    uvicorn.run("watchtower.app:create_app", factory=True,
                host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
