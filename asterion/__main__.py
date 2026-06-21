"""실행: python -m asterion [--config 경로] [--host H] [--port P]"""

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
    ap = argparse.ArgumentParser(prog="asterion")
    ap.add_argument("--config", help="config.toml 경로 (기본: 패키지 내장)")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    args = ap.parse_args()

    if args.config:
        os.environ["ASTERION_CONFIG"] = args.config
    cfg = Config.load(os.environ.get("ASTERION_CONFIG"))
    host = args.host or str(cfg.get("server.host", "127.0.0.1"))
    # 포트 우선순위: --port 플래그 > PORT 환경변수(프리뷰/오케스트레이터가 빈 포트 할당) >
    # config.toml. 일반 실행(env 없음)은 그대로 config의 8520을 쓴다.
    env_port = os.environ.get("PORT")
    port = args.port or (int(env_port) if env_port else int(cfg.get("server.port", 8520)))

    # 리버스 프록시/Tailscale serve 뒤(REMOTE_ACCESS_PLAN Phase B): X-Forwarded-* 를 신뢰해
    # 실제 클라이언트 IP를 복원한다(감사/레이트리밋용). serve는 localhost에서 프록시하므로 기본
    # 127.0.0.1만 신뢰. 직접 노출(프록시 없음)이면 이 헤더는 오지 않아 무해.
    fwd = str(cfg.get("server.forwarded_allow_ips", "127.0.0.1"))
    print(f"Asterion — http://{host}:{port}  (Ctrl+C로 종료)")
    uvicorn.run("asterion.app:create_app", factory=True,
                host=host, port=port, log_level="warning",
                proxy_headers=True, forwarded_allow_ips=fwd)


if __name__ == "__main__":
    main()
