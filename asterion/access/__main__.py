"""인증 셋업 도우미 — 비번 해시 / 토큰 / 세션 시크릿 생성.

생성한 값은 git에 안 올라가는 config.local.json 의 server.auth.* 에 넣는다.

사용:
  python -m asterion.access hash [PASSWORD]   # 비번 pbkdf2 해시(미입력 시 안전 입력)
  python -m asterion.access token             # 새 토큰 + 그 sha256 해시(기계용)
  python -m asterion.access secret            # 세션 서명 시크릿(hex)
"""

from __future__ import annotations

import secrets
import sys
from getpass import getpass

from .auth import hash_password, hash_token


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "hash":
        pw = sys.argv[2] if len(sys.argv) > 2 else getpass("password: ")
        print(hash_password(pw))
    elif cmd == "token":
        tok = secrets.token_urlsafe(32)
        print(f"token (클라이언트가 Authorization: Bearer 로 보관): {tok}")
        print(f"hash  (config.local.json tokens.<name>.hash 에 저장): {hash_token(tok)}")
    elif cmd == "secret":
        print(secrets.token_hex(32))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
