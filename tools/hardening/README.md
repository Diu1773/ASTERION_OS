# 운영 하드닝 · 백업 · 탐지 (REMOTE_ACCESS_PLAN Phase D)

원격 운영 시 앱 밖(호스트·네트워크·운영)에서 해둘 것들. 앱 측 하드닝(인증·역할·데드맨·
레이트리밋·proxy-headers·TrustedHost)은 Phase A~D 코드로 들어가 있고, 여기는 **운영자가
환경에서 처리**할 항목 + 권장 설정이다.

## 1. 앱 레이트리밋 (코드, 기본 on)

`[server.ratelimit]` — `/login`(브루트포스)·`/api/agent/chat`(LLM 비용)만 제한, 초과 시 429.
원격에선 그대로 두고, 필요 시 한도 조정:

```toml
[server.ratelimit]
enabled = true
login_max = 10     ; 60초당 IP별 로그인 시도
agent_max = 30     ; 60초당 IP별 AI 챗
```

## 2. 시크릿 관리

- 시크릿(비번 해시·토큰 해시·`session_secret`)은 **`config.local.json`(git 제외)에만**.
- **권한 제한(POSIX)**: `chmod 600 asterion/config.local.json` — 기동 시 느슨하면 앱이 경고한다.
- **로테이션**: 정기적으로 재발급.
  ```
  python -m asterion.access secret     # 새 session_secret(교체 시 전원 재로그인)
  python -m asterion.access hash <pw>  # 비번 변경
  python -m asterion.access token      # 토큰 회전(기존 hash 교체 → 옛 토큰 즉시 무효)
  ```

## 3. 제어 PC (천문대)

- **디스크 암호화**: BitLocker(Windows) / LUKS(Linux) — 도난 시 데이터·시크릿 보호.
- **RDP/VNC**: 끄거나 **tailnet 뒤로만**(공개 포트 금지). 원격 데스크톱이 필요하면 Tailscale로.
- **OS·의존성 패치** 최신 유지.
- **앱 노출 불변식**: `server.host=127.0.0.1` 유지(Tailscale serve가 앞단). `0.0.0.0` 공개 금지.

## 4. 네트워크

- 관측 LAN(가대 PWI4·카메라 SDK·돔 컨트롤러)을 **VLAN/서브넷으로 분리** — 제어 PC가 뚫려도
  장비 네트워크로의 확산을 제한.
- 인바운드는 Tailscale만(공유기 포트포워딩 0). 자세한 경계 설정은 [../tailscale/README.md](../tailscale/README.md).

## 5. 백업 (오프사이트)

영속 상태 두 가지만 챙기면 된다 — **SQLite DB + `config.local.json`**.

```bash
# DB 경로: 실측 data/asterion.db, 시뮬 data/sim/asterion.db (시뮬은 90일 보존 정리됨)
# SQLite는 켜진 채로도 안전하게 백업(.backup):
sqlite3 asterion/data/asterion.db ".backup '/backup/asterion-$(date +%F).db'"
cp asterion/config.local.json /backup/config.local-$(date +%F).json
```
(스케줄은 cron/작업 스케줄러로. FITS 프레임은 용량이 크니 별도 정책 — 시뮬분은 어차피 보존 정리됨.)

## 6. 탐지 · 감사

- **로그인 실패 임계** 초과 → 보안 Alert 자동 발화(Phase C2). Alert 패널/배지/경보음으로 확인.
- **모든 세계-변경 명령**은 ActionLog에 **사람별 신원**으로 남는다(`operator(alice)` 등, Phase A3).
  비정상 시간대/예상 밖 actor의 액션을 주기적으로 리뷰: `GET /api/actionlog`.
- **위험 오버라이드**(`allow_solar_slew`)가 켜진 채 기동하면 critical Alert로 가시화(Phase C2).
- 데드맨 발화·비상 폐쇄·기상 stale 등도 Alert로 흐른다([[asterion-alert-system]]).
- 외부 푸시(SMS/webhook)는 범위 밖 — 필요하면 Alert를 외부 채널로 중계하는 별도 작업으로.

## 7. 점검 체크리스트

- [ ] `config.local.json` 권한 600, git에 안 올라감
- [ ] `session_secret`·비번·토큰 발급 완료, 로테이션 주기 정함
- [ ] 제어 PC 디스크 암호화 + RDP/VNC 비공개
- [ ] 관측 LAN 분리(VLAN/서브넷)
- [ ] DB + config.local.json 오프사이트 백업 스케줄
- [ ] Alert 패널 모니터링(로그인 실패·오버라이드·데드맨)
- [ ] `server.host=127.0.0.1` + Tailscale serve(비공개)·Funnel off
