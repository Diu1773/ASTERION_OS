# Watchtower — 청람천문대 Observation OS v0.1

관측 전·중·후를 연결하는 통합 운영 플랫폼. APEX(분석 엔진)와 별개의
운영 레이어로, **온톨로지 DB + 액션 감사로그 + 드라이버 추상화** 위에
오토플랫 같은 자동화 세션을 올린다.

설계 원칙 (팔란티어식):

1. **세계 상태는 항상 DB에 존재한다** — Frame, WeatherRecord, TelescopeState,
   QualityMetric, Decision, ActionLog가 관계로 연결된 온톨로지.
2. **세계를 바꾸는 모든 행동은 Action을 통해서만** — 사전조건 검사 →
   실행 → 입출력 상태와 함께 ActionLog 기록. 실패한 사전조건도 기록.
3. **시뮬레이터 우선(sim-first)** — 실제 하드웨어에 붙이기 전 모든
   로직을 시뮬 드라이버로 검증한다. 기본 모드가 `sim`이다.

## 실행

```bat
watchtower\run.bat
```

또는 수동:

```bat
.venv-watchtower\Scripts\python -m watchtower
```

→ 브라우저에서 **http://127.0.0.1:8520**

### 시뮬 데모 (하드웨어 없이 오토플랫 돌려보기)

1. 대시보드 우측 하늘 패널에서 **황혼 시뮬 ON** (낮이어도 박명 하늘을 흉내냄)
2. 오토플랫 카드에서 **오토플랫 시작**
3. 라이브 로그에서 노출 탐색 → 디더 → 촬영 → ADU 판정 흐름 확인
4. 프레임/액션 테이블과 `watchtower/data/` 아래 FITS·DB 확인

## 아키텍처

```
[웹 대시보드  :8520]  ← WebSocket(1 Hz 상태 + 이벤트) + REST
        │
[FastAPI  app.py]
        │
  ┌─────┼──────────┬─────────────┐
  │ StatusSampler  │ ActionBus   │ AutoFlatRunner
  │ (1 Hz 스냅샷)  │ (감사로그)   │ (세션 엔진)
  └─────┬──────────┴─────────────┘
        │  드라이버 추상화 (drivers/base.py)
  ┌─────┴────────┬───────────┬──────────┐
  │ Mount        │ Camera    │ Filter   │ Weather
  │ sim / PWI4   │ sim/ASCOM │ sim/ASCOM│ sim
  │   HTTP :8220 │  (COM)    │  (COM)   │
  └──────────────┴───────────┴──────────┘
        │
[온톨로지 SQLite  data/watchtower.db]
```

## 실제 하드웨어 연결 (청람천문대)

`watchtower/config.toml`:

```toml
[drivers]
mode = "real"          # 마스터 스위치
mount = "pwi4"         # PWI4가 켜져 있어야 함 (포트 8220)
camera = "ascom"       # Moravian C3-61000
filterwheel = "ascom"
weather = "sim"        # 기상 장비 어댑터는 추후
```

장비별 sim 혼용 가능 — 예: 마운트만 실물(PWI4), 카메라는 sim으로
먼저 시험할 수 있다.

### ASCOM ProgID 선택

```bat
.venv-watchtower\Scripts\pip install pywin32
.venv-watchtower\Scripts\python watchtower\scripts\choose_ascom.py
```

Maxim DL 드롭다운과 같은 선택창이 뜬다. 출력된 ProgID를
`[drivers.ascom]`에 붙여넣는다.

### 주의

- **카메라 점유**: ASCOM 카메라는 한 앱만 연결 가능. Watchtower가 직접
  잡을 때는 Maxim DL에서 카메라를 Disconnect할 것 (또는 ASCOM Device Hub 사용).
- **PWI4 offset 파라미터**: `/mount/offset`의 인자명은 PWI4 버전에 따라
  다를 수 있다 — `drivers/pwi4.py` 주석 참고, 실물에서 1회 확인.

## 오토플랫 절차 (자동화된 수동 절차)

```
일몰 후(태양고도 < -0.5°) + 고도 40°↑ (미달 시 반태양 방위·75°로 슬루)
 └─ 필터 순서대로 (예: B → V → R → I)
     ├─ 테스트 노출로 목표 ADU(20,000~25,000) 노출 탐색
     │   · 너무 밝으면(최소노출에서도 초과) 20초 대기 반복 — 하늘 어두워질 때까지
     │   · 최대노출에서도 미달이면 필터 포기
     └─ N장 반복: 디더(±30") → 가대 안정화(슬루 종료 + settle) → 촬영
         → 중앙값 ADU 판정(ok / out_of_range 플래그) → FITS + DB 기록
         → 다음 프레임 노출 보정(하늘 밝기 추적)
```

모든 단계가 ActionLog에 남는다: `autoflat_session_start`,
`mount_goto_flat_field`, `filter_set`, `expose_test`, `dither`,
`expose_flat` — 입출력 상태 포함.

## 온톨로지 테이블 (v0.1)

| 테이블 | 용도 | 현재 적재 |
|---|---|---|
| ObservationSession | 세션 단위 (autoflat 등) | ✅ |
| Frame | 프레임 의미 (경로·필터·노출·통계·플래그) | ✅ |
| TelescopeState | 촬영 시점 마운트 자세 | ✅ |
| QualityMetric | 품질 판정 (→ APEX Quick Mode 연동 예정) | ✅ |
| WeatherRecord | 기상 이력 (30초 간격) | ✅ |
| ActionLog | 모든 액션의 감사 기록 | ✅ |
| Decision | 규칙/AI 판단 기록 | ✅ (세션 요약) |
| Target / UserGoal / ObservationPlan / FocusRun / Feedback | 스키마만 — 다음 단계 | 🔲 |

## API 요약

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/api/status` | 통합 상태 스냅샷 |
| GET | `/api/frames` `/api/actionlog` `/api/sessions` `/api/logs` `/api/weather/history` | 조회 |
| POST | `/api/actions/autoflat/start` `/stop` | 오토플랫 |
| POST | `/api/actions/mount/goto` `/tracking` `/stop` | 마운트 |
| POST | `/api/actions/filter` `/api/actions/camera/cooler` | 카메라/필터 |
| POST | `/api/sim/twilight` | 황혼 시뮬 토글 (sim 전용) |
| WS | `/ws` | 상태(1 Hz) + 로그/프레임/액션 이벤트 |

## 다음 단계 (로드맵 연결)

- **APEX Quick Mode 연동**: Light 프레임 인입 시 FWHM/별 개수/배경 →
  QualityMetric 자동 적재 (`apex` 패키지를 직접 import — 같은 레포라 바로 가능)
- Watchtower 안전 레이어를 액션 사전조건에 결합 (원격 돔 도입 시)
- Maxim DL 촬영 감지(ASCOM ImageReady/폴더) → Live Quality Monitor
- 기상 장비 어댑터 (실물 센서 / KMA API)
