# Access / 사이버 보안 — 원격 운영 PLAN (자율 빌드 계약서)

> **상태: 🟢 Phase A·B·C·D 완료(2026-06-22). 위험명령 2단계 확인만 선택 잔여.** 외부 원격에서
> 천문대를 굴리기 위한 접속·인증·인가·원격전용 안전·하드닝. 전부 **추가형(additive)** — 기존
> 안전계층(fail-closed·ActionBus 사전조건·SolarWatchdog·DomeGuard)의 *판정*은 **0줄 수정**. 인증은
> 기본 `enabled=false`라 로컬 SIM 개발 워크플로([[asterion-dev-workflow]])는 무변경. A=`access/`+
> ASGI 게이트+웹 로그인 UI. B=앱 하드닝(proxy-headers·TrustedHost·Tailscale 신원헤더)+[tools/
> tailscale/](tools/tailscale/README.md). C=세션 데드맨(`session_watchdog.py`, DomeGuard/SolarWatchdog
> 동형 행동 레이어)+보안 Alert. D=레이트리밋(`access/ratelimit.py`, /login·/agent/chat→429)+시크릿
> 권한 점검+[tools/hardening/](tools/hardening/README.md) 운영/백업/탐지 문서. 검증: 전체 118 그린.

> 맥락: 지금 플랫폼은 `127.0.0.1` 바인딩·무인증이라 "로컬에서만 안전"하다. 외부 원격 운영을
> 하려면 명령자의 *자격(인증·인가)*과 *원격 단절 시 안전*을 별도 계층으로 세워야 한다.
> 안전계층은 "잘못된 명령"을 막지만 "권한 없는 명령자"는 막지 않는다 — 그 빈틈을 채운다.
> 관련: [[asterion-safety-invariants]] · [[asterion-alert-system]] · [[asterion-ai-agent-layer]]

---

## 0. 목표

외부 인터넷에서 ASTERION에 안전하게 접속해 천문대를 원격 운영한다.
- **토폴로지:** 서버(ASTERION)는 **천문대 제어 PC**에서 돈다. 운영자는 폰·노트북으로 *접속해 들어간다*.
  따라서 네트워크 제약(CGNAT·포트포워딩)은 **천문대 인터넷** 기준이다.
- 사용 주체: **나 + 신뢰 운영자 몇 명** → 역할 분리(viewer/operator/admin) + 사람별 신원 감사.
- 비기능 요건: **소규모 팀 무료**, 천문대 인터넷이 CGNAT여도 동작, 운영자가 잘 모르는 네트워크
  작업(DNS·포트포워딩·TLS)을 최소화.
- 원칙: 인터넷에 앱을 **직접 노출하지 않는다.** 사설망(Tailscale, 비공개) 뒤 localhost 바인딩 유지.
- 비목표(이번 범위 밖): 멀티테넌시, 외부 IdP(OIDC) 연동(여지만 기록), 모바일 네이티브 앱.

---

## 1. 현재 상태 (코드 기준 진단)

| 항목 | 현재 | 근거 |
|---|---|---|
| 인증 | **전무** — 모든 `/api/actions/*`·`/api/system/*`·`/ws`·AI 채팅 무인증 | `asterion/app.py` |
| 바인딩 | 기본 `127.0.0.1` (지금은 로컬 전용이라 안전) | `asterion/__main__.py:28`, `config.toml:23` |
| TLS | 없음(평문 HTTP, uvicorn 직접) | `__main__.py:35` |
| 원격 쓰기 | `POST /api/weather/ingest` **무인증** — 값이 fail-closed 폴백으로 유입 | `app.py:560`, `watchtower/ingest.py` |
| 감사 신원 | `bus.run(actor="operator")` 하드코딩 — 누가 명령했는지 없음 | `core/actions.py:60` |
| AI 주체 | ToolKit이 ActionBus·NightRunner 제어 가능(태양 슬루만 차단) | [[asterion-ai-agent-layer]] |
| 시크릿 | `config.local.json` git 제외(✅ 적합한 시크릿 저장처) | `.gitignore` |

밝은 면: 물리 안전계층은 견고(fail-closed·SolarWatchdog·DomeGuard·ActionBus). 보안은 그 **위에**
얹는 직교 계층이다.

---

## 2. 위협 모델 (왜 일반 웹앱보다 위험한가)

1. **비가역 물리 피해** — 태양 일소(센서 소손)·돔 충돌·가대 케이블 랩·우천 중 셔터 개방.
2. **무인/원격** — 세션 단절·탈취 시 물리 E-stop 누를 사람이 없음.
3. **피벗** — 제어 PC 침해 시 관측 LAN(PWI4·카메라 SDK) 전체로 확산.
4. **공급 데이터 오염** — 무인증 ingest로 가짜 "안전" 기상 주입 → 악천후에 돔 유지 시도.
5. **AI 인젝션** — 노출된 채팅으로 ActionBus 경유 제어 시도(안전 사전조건이 1차 방어).

---

## 3. 설계 결정 — 접속 경계(perimeter)

**결정: Tailscale 메시 VPN(사설망) + Tailscale Serve(비공개 HTTPS) + 앱 자체 로그인.**

천문대 PC와 운영자 기기를 하나의 tailnet으로 묶고, `tailscale serve`로 앱을 **tailnet 내부에만**
HTTPS 노출한다(공개 인터넷 노출 0). 사용자 상황(네트워크 비전문 + 천문대 인터넷 불명 + Tailscale
경험 보유)에 가장 부합:

- **도메인·Cloudflare·포트포워딩 전부 불필요.** MagicDNS가 고정 이름(`asterion.<tailnet>.ts.net`)을,
  Serve가 HTTPS를 준다. CGNAT/공유기 제약을 NAT 트래버설로 자동 우회.
- 앱은 **계속 `127.0.0.1:8520` 바인딩**. `tailscale serve`가 TLS 종단 후 localhost로 프록시.
- **인터넷 공개면 0** — tailnet에 등록된 기기만 도달 가능. PLAN 원칙("직접 노출 금지")을 native 충족.
- 비용: 무료 Personal 플랜 = **6 사용자·기기 무제한**(2026-04 개편으로 과금 우려 해소). "나 + 운영자
  몇 명"은 무료 범위. 6명 초과 시에만 유료.
- 단점: 기기마다 Tailscale 앱 설치(2분) 1회. (대신 DNS·포트·TLS·CGNAT를 통째로 건너뜀.)

**대안(보류) — 자체 리버스 프록시(Caddy) + 전용 도메인 + Let's Encrypt TLS.** 다음 경우에만 전환:
(a) 운영자가 **6명 초과**, 또는 (b) **앱 설치 없이 순수 브라우저**로만 접속해야 할 때. 이 경우 앱은
동일하게 localhost 바인딩, 앞단만 Caddy로 교체(코드 무변경). 인입은 포트포워딩 443 또는 Cloudflare
Tunnel 택1. 외부 IdP는 Caddy `forward_auth`로 끼울 여지.

**핵심: 경계가 Tailscale든 Caddy든 앱 코드는 동일하다** — 앱은 localhost에만 붙고, 앞단만 바뀐다.

---

## 4. 아키텍처 (5계층 defense-in-depth)

```
[운영자: 폰/노트북, tailnet 가입 기기]
        │  ① Tailscale 사설망 (공개 인터넷 노출 0, NAT/CGNAT 자동 우회)
        ▼
[tailscale serve: 비공개 HTTPS]  ──(localhost)──►  [Asterion :8520, 127.0.0.1 바인딩]
        │                                                  │  ② 앱 인증(세션 로그인 / API 토큰)
        │                                                  ▼
        │                                           [③ 역할 인가: viewer/operator/admin/AI]
        │                                                  ▼
        │                                           [ActionBus + 안전계층] ← 신원 박힌 감사
        │                                                  ▼
        │                                           [④ 원격 데드맨: 세션 끊기면 자동 세이프]
        ▼                                                  ▼
[⑤ 호스트/네트워크 하드닝 + Tailscale ACL]          [하드웨어 + 로컬 물리 페일세이프]
```

경계(①)가 뚫려도 앱 인증(②③)이 또 막고, 그마저 뚫려도 안전계층·데드맨(④)·물리 페일세이프(⑤)가
피해를 봉쇄한다. (Tailscale은 "누가 포트에 닿나"만 풀고, 역할·감사·이중방어는 앱이 책임진다.)

---

## 5. 단계별 설계 (Phase A–D)

### Phase A — 앱 인증/인가 게이트 (코드, 추가형)

신규 서브패키지 `asterion/access/`:
- `auth.py` — 세션(서명 쿠키, `SessionMiddleware` + 시크릿)·비번 검증(Argon2/`pbkdf2`)·API 토큰 검증
  (Bearer, 토큰은 해시로만 저장)·선택적 TOTP(admin).
- `roles.py` — `Role(viewer<operator<admin)`, 엔드포인트→최소역할 매핑, `require_role(...)` 의존성.
- `audit.py` — `current_principal` **contextvar** + `actor_label(base)`. 인증 의존성이 요청마다 설정.

연결(전부 추가):
- `app.py`: 보호 라우트에 `Depends(require_role(...))` 부착(라우트 본문 무변경). `/login`·`/logout`·
  `/api/session/me` 추가. `[server.auth].enabled=false`면 의존성이 통과(하위호환).
- `core/actions.py`: `_record`가 `current_principal`로 actor 라벨 보강 — 예 `operator(alice)`.
  **단일 추가 라인, 사전조건/안전 로직 불변.** (ActionBus가 actor 라벨 출처만 풍부해짐.)
- `config.local.json`: 사용자/토큰 시크릿 저장(git 제외). 형식만 `config.toml`에 주석 안내.

엔드포인트 권한 분류(초안):

| 역할 | 접근 |
|---|---|
| viewer | `/api/status`·텔레메트리·프리뷰·로그·프레임 **읽기** |
| operator | + `/api/actions/*`(가대·카메라·돔·포커서·오토플랫·관측) |
| admin | + `/api/system/*`·`/api/dev/mode`·`allow_solar_slew` 등 **안전 오버라이드** |
| AI | 자체 제한(태양 슬루 차단 유지 + NightRunner/모드전환은 명시 화이트리스트) |
| token(machine) | `/api/weather/ingest`만(scope=ingest), 소스별 토큰 |

### Phase B — 경계/전송 (운영 설정 + 앱 소수정)

- **Tailscale 설치**: 천문대 제어 PC + 운영자 기기 전부 동일 tailnet 가입. MagicDNS 켜기.
- **노출**: `tailscale serve https / proxy 127.0.0.1:8520` — tailnet 내부에만 HTTPS. **Funnel(공개
  노출)은 금지.**
- **Tailscale ACL**: 운영자 기기/태그만 천문대 노드(8520)에 도달하도록 제한. (앱 역할과 별개 1차 경계.)
- 앱: `--proxy-headers` + `forwarded-allow-ips`(클라이언트 IP 정확 — 감사/레이트리밋용),
  HTTPS(Serve) 뒤이므로 쿠키 `Secure/HttpOnly/SameSite`, `TrustedHostMiddleware`로 `.ts.net` 이름 핀.
  선택: Tailscale 신원 헤더(`Tailscale-User-Login`)를 로그인 힌트로 활용.
- **불변식: 앱은 절대 `0.0.0.0` 공개 바인딩·Funnel 금지.** 항상 Serve 뒤 localhost.
- (대안 전환 시) Caddy `Caddyfile`: `obs.example.com → reverse_proxy 127.0.0.1:8520`(자동 HTTPS),
  인입은 포트포워딩 443 또는 Cloudflare Tunnel 택1.

### Phase C — 원격 전용 물리안전 (코드, 추가형)

- **세션 데드맨**: 운영자 UI가 `POST /api/session/heartbeat`를 주기 전송. 워치독이 마지막 하트비트
  추적 → **수동 원격 세션** 중 T초 무신호면 세이프-스테이트(추적 정지→돔 닫힘→파킹).
  jog의 `jog_keepalive`(`app.py:697`) 데드맨을 **세션 레벨로 일반화**. *NightRunner 무인 운영은
  제외*(자체 안전 보유 — 자율 설계와 충돌 금지).
- **위험명령 2단계 확인**(원격): park·돔 개폐·`dev/mode`·`allow_solar_slew`는 확인 토큰 요구.
- **보안 Alert**: 로그인 실패 임계 초과·오버라이드 사용·신규 토큰을 기존 AlertManager+WS+사운드로
  경보([[asterion-alert-system]] · [[asterion-sound-system]]).

### Phase D — 하드닝/탐지/운영

- 레이트리밋: `/api/agent/chat`(비용)·`/login`(브루트포스).
- 시크릿: `config.local.json` 파일권한 제한·키 로테이션·세션 시크릿 분리.
- 호스트: 제어 PC BitLocker(도난)·RDP/VNC 끄거나 tailnet 뒤로만·OS 패치·관측 LAN VLAN 분리.
- 백업: SQLite DB + `config.local.json` 오프사이트 백업.
- 탐지: 로그인 실패·비정상 시간대 액션 감사 리뷰 + 푸시.

---

## 6. 체크리스트

- [x] **A1 — access 패키지**: `access/audit.py`(Principal·contextvar·actor_label)·`roles.py`
  (secure-by-default 경로정책)·`auth.py`(pbkdf2 비번·sha256 토큰·hmac 서명세션·AccessPolicy)·
  `__main__.py`(hash/token/secret CLI). `[server.auth]` config(enabled/cookie_secure + 형식주석).
- [x] **A2 — 라우트 게이트 + UI**: `access/middleware.py`(ASGI 게이트, `Depends` 대신 단일 미들웨어로
  라우트 본문 0줄 수정) + `/login`·`/logout`·`/api/session/me` + **웹 로그인 오버레이**(index.html·
  style.css·app.js: 미인증 시 게이트, 401 재노출, 사용자배지/로그아웃, auth off면 무표시). 프리뷰 검증.
- [x] **A3 — 신원 감사**: `core/actions.py._record`가 `actor_label`로 actor 보강(단일 추가 라인).
  ingest는 token:ingest scope 의무화(enabled 시). `/metrics`는 token:metrics 또는 로그인 사용자.
- [~] **B1 — Tailscale 경계**: ✅ 앱 측 — proxy-headers(`__main__`)·`TrustedHostMiddleware`(.ts.net
  핀, 설정 시)·`cookie_secure`·Tailscale 신원헤더 옵션(`trust_tailscale_identity`+`tailscale_users`,
  serve 뒤에서만). ✅ [tools/tailscale/](tools/tailscale/README.md) serve/ACL/Funnel금지/하드닝 가이드.
  ⬜ **tailnet 가입·`tailscale serve` 실행은 운영자 환경 작업**(문서대로).
- [x] **C1 — 세션 데드맨**: `watchtower/session_watchdog.py`(safety_actuator 체인에 추가) +
  `POST /api/session/heartbeat`(viewer)·`GET /api/session/deadman` + app.js 30s 하트비트 +
  `[safety.session_deadman]`(기본 off). 수동 원격만(NightRunner 면제·미무장 시 무동작) → 추적정지·
  돔닫힘·파킹. 테스트 8.
- [~] **C2 — 보안 Alert**: ✅ `AlertManager.fire()` + 로그인 실패 임계 경보(브루트포스, AccessPolicy
  카운터) + 기동 시 `allow_solar_slew` 오버라이드 가시화 경보 + 데드맨 발화 경보. 테스트 1.
  ⬜ 위험명령 2단계 확인(park/돔/모드/override)은 D로 이월(선택).
- [x] **D1 — 레이트리밋 + 하드닝**: `access/ratelimit.py`(슬라이딩 윈도, /login·/api/agent/chat만
  →429, 인증 무관 최외곽, 기본 on) + `[server.ratelimit]` + 기동 시 `config.local.json` 권한 경고
  (POSIX) + [tools/hardening/](tools/hardening/README.md)(디스크암호화·RDP/VNC·VLAN·시크릿 로테이션·
  백업·탐지/감사 체크리스트). 테스트 8.
- [~] **D2 — 위험명령 2단계 확인**(선택, C2서 이월): park·돔·dev/mode·allow_solar_slew 원격 확인
  토큰. 프론트 확인 플로우 동반 필요 → 독립 변경으로 분리(미착수).

---

## 7. 검증 게이트

- stdlib **unittest** 게이트(`tests/test_access_gate.py`, 기존 `test_*_gate.py` 패턴) + `TestClient`.
  - 무인증 → 401/403; viewer가 액션 POST 거부; operator 허용; admin 전용 엔드포인트; ingest 토큰.
  - ActionLog에 사람별 신원(`operator(alice)`) 기록 확인.
  - SIM에서 데드맨 발화 → 세이프-스테이트 도달.
  - `auth.enabled=false`면 기존 흐름 **무변경**(회귀 가드).
- DB 경로 `asterion/data`(임시DB). 기존 안전 불변식 회귀 스위트 전체 그린 유지. 콘솔/서버 에러 0.
- **경계(Tailscale)는 코드 밖** — 운영 셋업 체크리스트로 검증(serve 비공개·Funnel off·ACL).

---

## 8. 가드레일

1. **SIM 전용** 개발·검증(실하드웨어 전 항상 sim).
2. **추가형** — 안전계층(safety/precondition/SolarWatchdog/DomeGuard) **0줄 수정**. `actions.py`는
   actor 라벨 출처 보강 1라인만(판정 로직 불변).
3. **시크릿은 `config.local.json`(git 제외)** 에만. 절대 커밋 금지. `config.toml`엔 형식 주석만.
4. **하위호환** — `auth.enabled=false` 기본. 로컬 dev 워크플로 무변경.
5. **노출 불변식** — 앱은 Tailscale Serve 뒤 localhost. `0.0.0.0` 공개 바인딩·**Funnel 금지**.
6. 매 증분 커밋(+`Co-Authored-By`). 막히면 멈춤+로그.
7. **자율 설계 존중** — 세션 데드맨은 *수동 원격*만. NightRunner 무인 운영은 제외.

---

## 9. 결정 로그

- `2026-06-21 설계확정 — 경계 후보 비교(메시VPN vs 리버스프록시+도메인). 주체=나+운영자 몇 명 →
  viewer/operator/admin 역할 + 사람별 신원감사. 전부 추가형, 안전계층 0줄, auth 기본 off.`
- `2026-06-22 Phase D — 레이트리밋(access/ratelimit.py): 인증 무관 항상 동작하는 별도 ASGI
  미들웨어를 최외곽에 배치(add_middleware 마지막 → AccessMiddleware보다 바깥, throttle가 인증보다
  먼저). 지정 경로(/login·/api/agent/chat)만 슬라이딩 윈도, 나머지 무제한이라 대시보드 폴링 무영향.
  클라이언트 IP는 proxy-headers로 복원된 scope['client']. 기본 on(한도 넉넉)이라 기존 흐름·테스트
  무영향(기존 게이트 테스트는 RateLimit 미배선 미니앱). 시크릿 권한 경고는 POSIX에서만(Windows 무의미).
  하드닝/백업/탐지는 tools/hardening 문서. 위험명령 2단계 확인은 프론트 동반이라 독립 변경으로 분리.
  라이브: /login 13회 → 10×200 + 3×429, /api/status 무제한. 테스트: ratelimit 8, 전체 118 그린.`
- `2026-06-22 Phase C — 세션 데드맨(원격 운영자 하트비트 끊김→세이프-스테이트). DomeGuard/
  SolarWatchdog와 동형 '행동 레이어'로 추가: safety.evaluate(판정) 0줄 수정, sampler.safety_actuator
  체인에 한 줄. 무장은 하트비트 1회 수신 후에만(로컬/헤드리스 보존), NightRunner 무인은 면제, 위험
  (돔열림/추적/슬루/세션) 있을 때만 발화, 에피소드당 1회+하트비트 재개 시 재무장. heartbeat=viewer
  권한(곁의 viewer도 '사람 있음'). 보안 Alert: AlertManager.fire()로 로그인실패 임계(AccessPolicy
  윈도 카운터)·기동 시 allow_solar_slew 가시화·데드맨 발화. config [safety.session_deadman]는 하위
  테이블이라 [safety] 평면 키 *뒤*에 배치(안 그러면 sun_avoidance_deg 등을 삼킴 — 검증으로 확인).
  위험명령 2단계 확인은 D로 이월. 테스트: 데드맨 8 + 로그인실패 1, 전체 110 그린.`
- `2026-06-22 A2 UI + Phase B 하드닝 — 웹 로그인 오버레이(index/style/app.js: 미인증 게이트·401
  재노출·사용자배지/로그아웃, auth off면 /api/session/me=authenticated라 무표시; 프리뷰로 기본부팅
  무영향 확인). Phase B 앱측: __main__ proxy_headers+forwarded_allow_ips, TrustedHostMiddleware
  (server.allowed_hosts 설정 시), AccessPolicy에 Tailscale 신원헤더(trust_tailscale_identity+
  tailscale_users, serve 뒤에서만; 직접노출 위조 방지로 기본 off). tools/tailscale/README(serve 비공개
  HTTPS·ACL·Funnel금지·하드닝). 테스트: 접근게이트 20(신원 4 포함) 그린. tailnet 가입/serve 실행은
  운영자 환경.`
- `2026-06-21 Phase A 구현(백엔드) — 신규 asterion/access/(audit·roles·auth·middleware·__main__).
  설계상 Depends(require_role) 대신 단일 ASGI 미들웨어 + 경로/메서드 secure-by-default 정책 채택:
  라우트 50여 개 본문을 0줄도 안 고치고, 새 엔드포인트가 생겨도 자동 보호(기본 거부형). 의존성
  추가 0(stdlib pbkdf2·sha256·hmac 서명세션). core/actions._record는 actor_label 1라인만(안전·
  사전조건 불변). enabled=false 기본이라 기존 동작 보존. 검증: tests/test_access_gate.py 16개 +
  전체 81 그린, create_app 빌드·CLI 동작 확인. 남음: A2 프론트 로그인 화면, Phase B(Tailscale)~D.`
- `2026-06-21 경계 결정 = Tailscale(번복). 초안은 도메인+Caddy였으나 (1) 토폴로지 정정 — 서버는
  천문대 PC, 제약은 천문대 인터넷(CGNAT 가능); (2) 비용 전제 정정 — Tailscale 무료 Personal이
  2026-04부터 6사용자·기기무제한(과금 우려 해소); (3) 사용자가 네트워크 비전문 + Tailscale 경험.
  → Tailscale Serve(비공개 HTTPS)로 도메인·Cloudflare·포트포워딩·TLS를 통째로 우회. 공개노출 0.
  도메인+Caddy는 '6명 초과 / 무설치 브라우저' 전환 대안으로 보류. 앱 코드는 경계와 무관(localhost
  고정)이라 Phase A·C·D 불변, Phase B만 Tailscale 셋업으로 교체.`
