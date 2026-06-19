# ASTERION OS 개발 로드맵

> **ASTERION OS**는 장비·기상·데이터·안전·스케줄·분석·AI 판단을 하나의 상태 체계로 통합하는 자율형 천문 관측 운영체제이다.  
> 이 문서는 ASTERION OS의 전체 논리 구조, 세션 분리, 핵심 모듈, 개발 우선순위를 정리한 로드맵이다.

---

## 0. 핵심 정의

### 0.1 프로젝트명

**ASTERION OS**

### 0.2 한 줄 정의

**관측소 규모의 천문 데이터를 하나의 판단 체계로 통합하고, 관측 계획부터 장비 실행, 안전 판단, 데이터 저장, 품질 평가, 분석 흐름까지 연결하는 자율형 천문 관측 OS.**

### 0.3 설계 철학

ASTERION OS는 단순한 웹 대시보드가 아니다.  
장비 연결 프로그램도 아니고, 자동촬영 스크립트도 아니다.

ASTERION OS의 본질은 다음과 같다.

1. 다양한 천문 장비를 하나의 표준 모델로 추상화한다.
2. 현재 관측소 상태를 하나의 State Store에 통합한다.
3. 모든 상태 변화와 사건을 Event Bus로 연결한다.
4. Watchtower와 Policy Engine이 안전과 규칙을 판단한다.
5. Orchestrator가 실제 관측 workflow를 지휘한다.
6. Archive와 Provenance가 데이터의 저장, 복구, 계보를 관리한다.
7. Skygraph가 대상·장비·관측·데이터의 의미 관계를 구성한다.
8. AI Agent는 직접 장비를 움직이는 것이 아니라 상태를 해석하고 추천한다.

---

## 1. 전체 계층 구조

```text
[Interface Layer]
ASTERION Console
ASTERION Command
ASTERION Device Settings
ASTERION Target Page
ASTERION Skyview

        ↓

[Operation Layer]
ASTERION Orchestrator
ASTERION Meridian
ASTERION Watchtower
ASTERION Policy Engine

        ↓

[State / Knowledge Layer]
ASTERION State Store
ASTERION Event Bus
ASTERION Skygraph
ASTERION Provenance

        ↓

[Device / Data Layer]
ASTERION Device Manager
ASTERION Device Core
ASTERION Telemetry
ASTERION Weather Store
ASTERION Archive
ASTERION Calibration Library
ASTERION Forge
ASTERION Sentinel

        ↓

[Infrastructure Layer]
ASTERION Agent Runtime
ASTERION PC Agent
ASTERION Ingestion API
ASTERION Plugin Runtime
ASTERION Access
ASTERION Alert
ASTERION Simulator / Twin

        ↓

[External World]
ASCOM
ASCOM Alpaca
PWI HTTP
ZWO SDK
Serial / USB
TCP / HTTP
Weather PC
Camera PC
Mount PC
File Watcher
Vendor Apps
```

---

## 2. 세션 구조

ASTERION OS 개발은 하나의 거대한 기능으로 진행하면 안 된다.  
각 기능 영역을 세션 단위로 분리해야 한다.

| 세션 | 핵심 역할 | 주요 모듈 |
|---|---|---|
| Device Session | 장비 연결·설정·역할 관리 | Device Manager, Device Core, Adapter Layer |
| State Session | 현재 상태의 단일 기준점 | State Store |
| Event Session | 모듈 간 사건 전달 | Event Bus |
| Safety Session | 기상·안전·정책 판단 | Watchtower, Policy Engine, Alert |
| Weather / Network Session | 분산 PC 기상 수집·DB·재정렬·복구 | Weather Agent, Ingestion API, Weather Store |
| Operation Session | 관측 workflow 실행 | Orchestrator, Meridian |
| Archive / Provenance Session | 관측자료 저장·복구·계보 추적 | Archive, Provenance |
| Analysis Session | 품질 평가·전처리·분석 구조 | Sentinel, Forge, Calibration Library |
| Skygraph Session | 온톨로지·대상 중심 데이터 구조 | Skygraph, Target Page |
| AI Agent Session | 상태 해석·추천·로그 검색 | AI Agent Runtime |
| UI / UX Session | 사용자 화면과 디자인 시스템 | Console, Command, Skyview, Device Settings |
| Infrastructure Session | 서버·PC Agent·권한·플러그인 | API, Plugin Runtime, Access, Simulator |

---

## 3. Device Session

### 3.1 목적

Device Session은 천문 장비를 ASTERION OS에 연결하고, 각 장비를 관측 시스템 안의 역할로 배정하는 계층이다.

중요한 원칙은 다음과 같다.

> **UI가 장비에 직접 맞춰지는 것이 아니라, 장비가 ASTERION의 표준 모델에 맞춰 번역되어야 한다.**

예를 들어 RST 가대, ZWO 필터휠, Moravian 카메라, PlaneWave PWI, 기상센서, 포커서, 돔 컨트롤러는 연결 방식이 서로 다르지만 ASTERION 내부에서는 표준 상태 모델로 표현되어야 한다.

### 3.2 구성요소

| 모듈 | 역할 |
|---|---|
| Device Manager | 장비 등록, 연결 설정, 역할 배정 |
| Device Registry | 장비 목록, 드라이버, 포트, 설정 저장 |
| Capability Model | 장비가 지원하는 기능 정의 |
| Device Core | 연결 유지, 상태 polling, 명령 큐, 충돌 방지 |
| Adapter Layer | ASCOM, Alpaca, PWI HTTP, ZWO SDK, Serial 등을 표준 모델로 변환 |

### 3.3 구조

```text
Device Settings UI
        ↓
Device Manager
        ↓
Device Registry
        ↓
Device Core
        ↓
Adapter Layer
        ↓
ASCOM / Alpaca / PWI HTTP / ZWO SDK / Serial / TCP
        ↓
Physical Devices
```

### 3.4 장비 연결 UI와 관측 운영 UI 분리

#### 장비 연결 화면

장비 연결 화면은 관측 중 사용하는 화면이 아니라 설정 화면이다.

```text
Settings > Devices

Mount
- Rainbow Astro RST
- Adapter: ASCOM Telescope
- Role: Primary Mount
- Status: Connected

Filter Wheel
- ZWO EFW
- Adapter: ASCOM FilterWheel
- Role: Main Filter Wheel
- Bound to: Science Camera
```

#### 관측 운영 화면

관측 운영 화면은 장비명보다 관측 행위를 중심으로 보여야 한다.

```text
Capture

Camera: C3-61000
Filter: r
Exposure: 60 s
Sequence: g × 10, r × 10, i × 10
Status: Ready
```

필터휠은 대시보드에서 독립 카드로 크게 보이기보다는 Capture 흐름 안에 흡수되어야 한다.

### 3.5 Role Binding

장비는 단순히 연결되는 것이 아니라 ASTERION 안에서 역할을 가져야 한다.

```text
RST Mount → Primary Mount
ZWO EFW → Main Filter Wheel
C3-61000 → Main Science Camera
Focuser → Primary Focuser
Weather Agent → Primary Weather Source
```

필터휠은 카메라와 연결된다.

```text
ZWO EFW
Role: Main Filter Wheel
Bound to: C3-61000 Camera
Filter Set: SDSS g, r, i, Hα
```

### 3.6 Capability Model

다양한 장비가 붙기 위해서는 장비별 UI를 하드코딩하면 안 된다.  
각 장비가 자신이 지원하는 기능을 선언하게 해야 한다.

#### Mount Capabilities

```ts
MountCapabilities {
  canSlew: boolean
  canPark: boolean
  canUnpark: boolean
  canPulseGuide: boolean
  canSetTracking: boolean
  canReportAltAz: boolean
  canReportPierSide: boolean
}
```

#### Filter Wheel Capabilities

```ts
FilterWheelCapabilities {
  canSetPosition: boolean
  canReportNames: boolean
  canReportMoving: boolean
  filterCount: number
}
```

#### Camera Capabilities

```ts
CameraCapabilities {
  canExpose: boolean
  canCool: boolean
  canSetGain: boolean
  canSetOffset: boolean
  canSetBinning: boolean
  canDownloadFits: boolean
}
```

### 3.7 저수준 기능을 관측 행위로 변환

장비 드라이버의 기능을 그대로 UI에 노출하면 장비 테스트 프로그램처럼 보인다.  
ASTERION은 저수준 기능을 관측 행위로 변환해야 한다.

```text
SetPosition(2) → Change filter to r
SlewToCoordinates → GoTo target
StartExposure → Capture frame
```

---

## 4. State Session

### 4.1 목적

State Session은 ASTERION OS의 현재 상태를 관리하는 계층이다.

장비와 모듈이 많아지면 상태 불일치가 쉽게 발생한다.

```text
Console은 exposing이라고 표시
Camera adapter는 idle
Archive는 파일 미수신
Orchestrator는 saving이라고 판단
```

이를 막기 위해 **State Store**가 필요하다.

### 4.2 State Store 역할

State Store는 다음 상태를 중앙에서 관리한다.

```text
현재 마운트 상태
현재 카메라 상태
현재 필터 상태
현재 포커서 상태
현재 기상 상태
현재 안전 상태
현재 관측 시퀀스 단계
현재 스케줄 상태
현재 네트워크 상태
```

### 4.3 구조

```text
Device Core
Weather Agent
Archive
Sentinel
Orchestrator
        ↓
State Store
        ↓
Console / Command / Watchtower / Meridian / AI Agent
```

### 4.4 설계 원칙

1. 대시보드는 장비에 직접 묻지 않는다.
2. Console, Watchtower, Meridian, AI Agent는 State Store를 기준으로 판단한다.
3. 현재 상태는 한 곳에서 관리되어야 한다.
4. 과거 이력 DB와 현재 상태 캐시는 분리한다.

---

## 5. Event Session

### 5.1 목적

Event Session은 ASTERION 내부 모듈들이 서로 느슨하게 연결되도록 하는 신경망이다.

모듈이 서로 직접 호출하면 구조가 쉽게 꼬인다.

#### 나쁜 구조

```text
Weather가 Camera를 직접 멈춤
Sentinel이 Mount를 직접 움직임
Archive가 Scheduler를 직접 수정함
```

#### 좋은 구조

```text
Watchtower → Event Bus → Orchestrator
Sentinel → Event Bus → Orchestrator
Archive → Event Bus → Console
```

### 5.2 주요 이벤트

```text
WEATHER_UNSAFE
WEATHER_STALE
DEVICE_CONNECTED
DEVICE_DISCONNECTED
EXPOSURE_STARTED
EXPOSURE_FAILED
FRAME_RECEIVED
FRAME_REJECTED
AUTOFOCUS_REQUIRED
ARCHIVE_RECOVERY_COMPLETED
OBSERVATION_SEQUENCE_DONE
SAFETY_ABORT_TRIGGERED
```

### 5.3 예시 흐름

```text
Event: WEATHER_UNSAFE
Reason: Humidity above threshold

        ↓

Orchestrator:
1. Pause capture
2. Park mount
3. Close dome
4. Write safety event
5. Notify user
```

---

## 6. Safety Session

### 6.1 목적

Safety Session은 기상, 장비, 네트워크, 정책 조건을 바탕으로 관측 가능 여부와 안전 조치를 판단한다.

### 6.2 구성요소

| 모듈 | 역할 |
|---|---|
| Watchtower | 기상·안전 상태 판단 |
| Policy Engine | 안전 규칙과 관측 규칙 평가 |
| Alert | 위험 상황 알림 |
| Device Core | dome close, mount park 등 실제 실행 |

### 6.3 Watchtower가 보는 정보

```text
태양 고도
달 고도
구름량
습도
이슬점 차이
풍속
비 감지
센서 stale 여부
돔 상태
마운트 상태
전원 상태
네트워크 상태
```

### 6.4 안전 판단 원칙

가장 중요한 원칙은 다음과 같다.

> **기상 데이터가 없으면 safe가 아니라 unsafe다.**

예시:

```text
UNSAFE

Primary reason
Weather telemetry timeout

Last update
3m 42s ago

Blocking systems
- Weather Agent: stale
- Watchtower: unsafe
- Meridian: paused

Recommended action
Keep dome closed and wait for telemetry recovery
```

### 6.5 Fail-Closed 원칙

관측소 자동화에서는 불확실한 상태를 안전하다고 보면 안 된다.

```text
기상 데이터 정상 수신 → 조건 판단
기상 데이터 30초 이상 지연 → warning
기상 데이터 2분 이상 지연 → unsafe
센서값 비정상 → warning 또는 unsafe
네트워크 단절 → warning 또는 unsafe
```

---

## 7. Weather / Network Session

### 7.1 목적

Weather / Network Session은 다른 PC, USB/Serial 기상기기, 자체 앱, 로그 파일, 네트워크 지연 상황을 통합 관리한다.

사용자가 이전에 구현한 원격관측소 기상 데이터 DB와 네트워크 재정렬·아카이브 복구 알고리즘은 이 세션의 기반이 된다.

### 7.2 구조

```text
Weather Device
USB / Serial / Vendor App / Log File
        ↓
Weather PC
        ↓
ASTERION Weather Agent
        ↓
Ingestion API
        ↓
Stream Reorder Engine
        ↓
Weather Store
        ↓
Watchtower
```

### 7.3 중앙 API 구조

다른 PC가 DB에 직접 쓰게 하지 않는 것이 좋다.

```text
Weather PC → ASTERION API → DB
```

Weather Agent는 중앙 서버에 데이터를 전송한다.

```json
{
  "source_id": "weather_pc_01",
  "sensor_id": "weather_station",
  "timestamp": "2026-06-14T02:13:21+09:00",
  "temperature_c": 12.4,
  "humidity_percent": 68.2,
  "wind_speed_ms": 1.4,
  "rain": false,
  "cloud_index": 0.23
}
```

### 7.4 Weather Agent 역할

```text
Serial/USB/TCP/파일 로그에서 기상 데이터 읽기
제조사별 형식을 ASTERION 표준 형식으로 변환
단위 통일
timestamp 부여
센서값 검증
중앙 서버로 전송
중앙 서버 연결 실패 시 로컬 버퍼 저장
연결 복구 후 누락 데이터 재전송
```

### 7.5 Stream Reorder Engine 역할

```text
timestamp 기반 재정렬
중복 데이터 제거
누락 데이터 감지
지연 데이터 처리
로컬 버퍼 복구
중앙 DB 정합성 유지
```

### 7.6 Weather Store

기상 데이터는 두 층으로 나누어 관리한다.

| 저장소 | 역할 |
|---|---|
| Current State | 대시보드와 Watchtower가 읽는 현재 상태 |
| Time-series DB | 추세 분석, 안전 이벤트 분석, 관측 조건 기록 |

초기에는 SQLite 또는 PostgreSQL로 시작할 수 있고, 장기적으로는 PostgreSQL + TimescaleDB 또는 InfluxDB로 확장할 수 있다.

---

## 8. Operation Session

### 8.1 목적

Operation Session은 관측 계획을 실제 장비 명령과 데이터 흐름으로 변환한다.

### 8.2 역할 분리

| 모듈 | 역할 |
|---|---|
| Meridian | 무엇을 관측할지 결정 |
| Orchestrator | 어떤 순서로 실행할지 관리 |
| Device Core | 실제 장비 명령 실행 |
| Watchtower | 안전 승인 또는 중단 |
| Sentinel | 프레임 품질 평가 |
| Archive | 데이터 저장 확인 |

### 8.3 관측 실행 흐름

```text
Meridian:
M67 g,r,i 관측 요청

        ↓

Orchestrator:
1. Watchtower safety check
2. Dome open
3. Mount unpark
4. GoTo target
5. Plate solve or sync
6. Autofocus
7. Set filter g
8. Expose 60 s
9. Save frame
10. Archive confirm
11. Sentinel quality check
12. Repeat or adjust
13. Move to next filter
14. Finish sequence
```

### 8.4 Orchestrator의 핵심 역할

Orchestrator는 ASTERION OS의 관측 실행 지휘자이다.

```text
안전 확인
장비 명령 순서화
명령 큐 관리
실패 시 복구
재촬영 판단
Archive 저장 확인
다음 관측 단계로 이동
긴급 정지 이벤트 처리
```

Orchestrator가 없으면 ASTERION은 장비 버튼 모음에 머문다.  
Orchestrator가 있어야 관측 행위가 하나의 workflow가 된다.

---

## 9. Archive / Provenance Session

### 9.1 목적

Archive / Provenance Session은 관측 데이터의 저장, 복구, 정합성, 계보를 관리한다.

Archive는 단순 폴더 저장이 아니다.  
ASTERION에서는 데이터가 왜, 어떻게, 어떤 조건에서 만들어졌는지까지 기록되어야 한다.

### 9.2 Archive 역할

```text
원본 FITS 저장
파일 무결성 검사
DB-파일 정합성 확인
전송 누락 복구
중복 파일 정리
관측 세션별 정리
대상별 정리
로컬/원격 아카이브 동기화
```

### 9.3 Archive Recovery

네트워크 환경에서는 다음 문제가 발생할 수 있다.

```text
Light_001.fit
Light_002.fit
Light_004.fit
Light_003.fit
전송 실패
DB 기록 누락
파일만 있고 메타데이터 없음
메타데이터만 있고 파일 없음
```

따라서 Archive Recovery는 다음을 수행해야 한다.

```text
파일-DB 대조
누락 파일 탐지
중복 파일 탐지
순서 역전 복구
checksum 검증
재전송 요청
복구 로그 저장
```

### 9.4 Provenance 역할

Provenance는 데이터의 계보를 기록한다.

각 FITS 또는 처리 결과에 대해 다음 정보를 추적한다.

```text
어떤 대상인가
어떤 관측 요청에서 나왔는가
누가 요청했는가
어떤 장비로 찍었는가
어떤 필터인가
어떤 기상 조건이었는가
왜 이 노출시간이 선택됐는가
어떤 calibration frame이 적용됐는가
Sentinel 품질 점수는 어땠는가
어떤 pipeline version으로 처리됐는가
```

### 9.5 구조

```text
Camera / Weather / Device State
        ↓
Frame Metadata
        ↓
Archive
        ↓
Provenance
        ↓
Skygraph / Target Page / Forge
```

---

## 10. Analysis Session

### 10.1 목적

Analysis Session은 프레임 품질 평가, 전처리, 측광, 분석 자동화의 구조를 담당한다.

초기 단계에서는 ML이나 자동 데이터 분석을 본격적으로 구현하지 않아도 된다.  
다만 나중에 붙일 수 있도록 인터페이스를 미리 정의해야 한다.

### 10.2 구성요소

| 모듈 | 초기 역할 | 장기 역할 |
|---|---|---|
| Sentinel | 기본 품질 지표 계산 구조 | ML 기반 불량 프레임 분류 |
| Forge | 전처리 pipeline interface | 자동 보정·정렬·측광 |
| Calibration Library | dark/flat/bias 관리 구조 | 최적 calibration 추천 |
| Quality Metrics | FWHM, ADU, star count 저장 | 품질 예측 모델 |

### 10.3 초기 최소 구조

```text
Frame received
        ↓
Basic metadata extraction
        ↓
Quality metrics placeholder
        ↓
Archive
        ↓
Target Page
```

### 10.4 Sentinel Interface

```text
Sentinel.evaluate(frame_id)

Return:
- accepted / rejected / warning
- reason
- metrics
- recommended action
```

예시:

```text
Frame Quality

Status: Warning
Reason: FWHM above threshold

Metrics:
- FWHM: 5.2"
- Median ADU: 18,420
- Star count: 721

Recommended action:
Run autofocus
```

### 10.5 Calibration Library

Calibration Library는 전처리 자동화를 위한 기반이다.

```text
Bias
Dark
Flat
Master calibration frames
Filter별 flat
온도별 dark
날짜별 calibration set
적용 가능한 calibration frame 추천
calibration 품질 점수
```

---

## 11. Skygraph Session

### 11.1 목적

Skygraph는 ASTERION OS의 천문 관측 온톨로지이다.

온톨로지는 단순 DB가 아니라, 관측소 세계를 컴퓨터가 이해할 수 있게 만든 의미 지도이다.

### 11.2 파일 중심 구조의 한계

나쁜 구조:

```text
2026-06-14/M67/r/Light_001.fit
```

좋은 구조:

```text
Target: M67
- Observation Requests
- Executed Sequences
- Frames
- Filters
- Weather Conditions
- Quality Metrics
- Calibration Products
- Analysis Results
- Recommended Next Observation
```

### 11.3 Skygraph가 연결하는 것

```text
Target
Observation Request
Observation Sequence
Frame
Device
Weather Condition
Quality Metric
Calibration Product
Analysis Result
User
Science Goal
```

### 11.4 Target Page

Skygraph는 UI에서 Target Page로 드러난다.

```text
Target Page: M67

Overview
Visibility tonight
Observation history
Latest frames
Photometry products
Quality trend
Weather history
Calibration products
Recommended next observation
Related catalog data
```

---

## 12. AI Agent Session

### 12.1 목적

AI Agent는 ASTERION OS에 나중에 붙는 해석·추천 계층이다.

초기에는 직접 장비를 움직이면 안 된다.  
AI Agent는 먼저 읽기, 설명, 추천 역할부터 시작해야 한다.

### 12.2 초기 역할

```text
현재 시스템 상태 요약
왜 unsafe인지 설명
다음 관측 가능 시간 설명
최근 프레임 품질 요약
관측 실패 원인 추정
장비 연결 문제 진단
스케줄 추천
로그 기반 문제 검색
```

### 12.3 AI Agent가 읽는 정보

```text
State Store
Event Bus log
Watchtower decisions
Meridian schedule
Archive metadata
Sentinel metrics
Skygraph
Device Registry
```

### 12.4 직접 하면 안 되는 것

초기 AI Agent는 다음을 직접 수행하지 않는다.

```text
마운트 직접 slew
돔 직접 open/close
카메라 직접 exposure
필터휠 직접 이동
```

### 12.5 안전한 제어 흐름

```text
AI Agent
        ↓
Recommendation
        ↓
User / Policy Approval
        ↓
Orchestrator
        ↓
Device Core
```

---

## 13. UI / UX Session

### 13.1 목적

UI / UX Session은 사용자가 ASTERION OS를 실제로 조작하고 이해하는 화면을 구성한다.

UI는 기능별이 아니라 사용자의 작업 맥락별로 나누어야 한다.

### 13.2 주요 화면

| 화면 | 역할 | 디자인 참고 |
|---|---|---|
| ASTERION Home | 프로젝트 소개 | Raycast + Linear |
| ASTERION Console | Muuri 기반 통합 관측 패널 | NASA Open MCT + Linear |
| ASTERION Command | 명령 팔레트 | Raycast |
| Device Settings | 장비 연결·역할 설정 | macOS Settings |
| Target Page | 대상 중심 데이터 페이지 | SkyPortal |
| Meridian Planner | 관측 요청 생성 | LCO Portal |
| Telemetry View | 수치·시계열 지표 | Grafana |
| Watchtower View | 안전 상태와 원인 | Datadog |
| Skyview | 하늘 시각화 | NASA Eyes |

### 13.3 디자인 레퍼런스 역할 분리

각 레퍼런스는 담당 역할이 다르다.

| 레퍼런스 | 적용 영역 |
|---|---|
| Raycast | 홈페이지, Command Palette |
| Linear | 전체 polish, 글꼴, 간격, 차분한 다크 UI |
| NASA Open MCT | 메인 관측 Console 구조 |
| Grafana | Telemetry, 수치, 그래프, 로그 밀도 |
| Datadog | 안전 상태, 경고, 원인 표시 |
| SkyPortal | Target Page, 대상 중심 데이터 구조 |
| LCO Portal | 관측 요청 workflow |
| NASA Eyes | Skyview, 하늘 시각화 |

### 13.4 UI 원칙

1. 장비 연결 UI와 관측 운영 UI를 분리한다.
2. 필터휠은 Capture 흐름에 흡수한다.
3. 상태 색은 의미가 있을 때만 사용한다.
4. 네온, 과한 glow, AI식 사이버 대시보드 느낌을 피한다.
5. 모든 카드가 같은 중요도로 보이지 않게 한다.
6. 관측자가 3초 안에 현재 상태를 파악할 수 있어야 한다.

---

## 14. Infrastructure Session

### 14.1 목적

Infrastructure Session은 ASTERION OS가 실제 관측소 환경에서 동작하기 위한 서버, PC Agent, API, 권한, 플러그인 구조를 담당한다.

### 14.2 구성

```text
Central Server
- API server
- State Store
- Event Bus
- DB
- Archive manager
- Watchtower
- Orchestrator

PC Agents
- Weather Agent
- Camera Agent
- Mount Agent
- Archive Agent
- File Watcher

Plugin Runtime
- Device adapters
- Analysis plugins
- Sensor parsers
```

### 14.3 분산 구조

```text
Weather PC
Camera PC
Mount PC
Analysis PC
        ↓
ASTERION Agents
        ↓
Central Server
        ↓
Console
```

각 PC가 서로를 직접 건드리는 것이 아니라 중앙 서버를 통해 연결되어야 한다.

### 14.4 Plugin Runtime

장기적으로 ASTERION OS는 다양한 장비와 분석 모듈을 받아들여야 한다.

```text
새 기상센서 어댑터
새 카메라 드라이버
새 품질평가 모델
새 분석 파이프라인
새 관측 전략 알고리즘
외부 연구자가 만든 플러그인
```

### 14.5 Access

사용자 권한도 별도 계층으로 둔다.

```text
관리자
관측자
학생
분석자
외부 연구자
읽기 전용 사용자
장비 제어 가능 사용자
긴급 정지 권한 사용자
```

---

## 15. 핵심 모듈 정리

| 모듈 | 한 줄 역할 |
|---|---|
| Console | 사용자가 보는 통합 관측 대시보드 |
| Command | 빠른 명령 팔레트 |
| Device Settings | 장비 연결·역할 설정 화면 |
| Device Manager | 장비 등록, 설정, 역할 관리 |
| Device Core | 실제 장비 연결, 상태, 명령 큐 관리 |
| Adapter Layer | ASCOM, PWI HTTP, Serial 등을 표준 모델로 변환 |
| State Store | 현재 상태의 단일 기준점 |
| Event Bus | 모듈 간 사건 전달 |
| Watchtower | 기상·안전 판단 |
| Policy Engine | 관측·안전 규칙 평가 |
| Meridian | 관측 계획·스케줄링 |
| Orchestrator | 관측 workflow 실행 지휘 |
| Weather Store | 기상 현재값·이력 저장 |
| Archive | 관측자료 저장·복구 |
| Provenance | 데이터 생성 계보 추적 |
| Sentinel | 프레임 품질 평가 |
| Forge | 전처리·분석 파이프라인 |
| Calibration Library | 보정 프레임 관리 |
| Skygraph | 대상·관측·장비·데이터 의미망 |
| Target Page | 대상 중심 데이터 UI |
| AI Agent | 상태 해석·추천·로그 검색 |
| Simulator / Twin | 실제 장비 없이 개발·테스트 |
| Alert | 위험 상황 알림 |
| Access | 사용자 권한 관리 |

---

## 16. 개발 로드맵

### Phase 0. 개념 구조 확정

목표는 코드 작성 전에 ASTERION의 뼈대를 고정하는 것이다.

#### 해야 할 일

```text
모듈 이름 확정
계층 구조 확정
장비 표준 모델 정의
State 모델 정의
Event 이름 정의
DB 기본 스키마 정의
UI 화면 구분
```

#### 산출물

```text
architecture.md
device-model.md
state-model.md
event-schema.md
database-schema.md
ui-map.md
```

---

### Phase 1. Simulator 먼저 만들기

실제 장비 없이 개발 가능해야 한다.

#### 구현 대상

```text
가짜 마운트
가짜 카메라
가짜 필터휠
가짜 포커서
가짜 기상센서
가짜 FITS 프레임
가짜 네트워크 지연
```

#### 이유

Simulator가 있어야 실제 장비 없이 Console, Watchtower, Orchestrator, Sentinel을 개발하고 테스트할 수 있다.

---

### Phase 2. Device Manager / Device Core

실제 장비 연결의 중심을 만든다.

#### 해야 할 일

```text
Device Registry
Adapter Interface
Capability Model
Role Binding
Connection Test
State Polling
Command Queue
```

#### 우선순위

```text
1. Simulator Adapter
2. ASCOM Mount Adapter
3. ASCOM FilterWheel Adapter
4. PWI HTTP Adapter
5. Serial Weather Adapter
6. File Watcher Adapter
```

---

### Phase 3. State Store / Event Bus

장비 연결이 되면 상태와 이벤트를 중앙화한다.

#### 해야 할 일

```text
State Store 구현
Event Bus 구현
장비 상태 업데이트
이벤트 로그 저장
Console에서 실시간 구독
```

#### 목표

이 단계부터 ASTERION이 단순 웹앱이 아니라 OS처럼 보이기 시작한다.

---

### Phase 4. Weather Store / Watchtower

기상과 안전을 붙인다.

#### 해야 할 일

```text
Weather Agent
Ingestion API
Weather Store
Stream Reorder Engine
Telemetry stale 판단
Safety Policy
Watchtower View
Alert
```

#### 기존 자산

사용자가 이전에 구현한 원격관측소 기상 DB와 네트워크 재정렬·아카이브 복구 알고리즘을 이 단계의 핵심 기반으로 재사용한다.

---

### Phase 5. Archive / Provenance

관측자료 저장 구조를 잡는다.

#### 해야 할 일

```text
Frame metadata 저장
FITS archive 경로 관리
파일-DB 정합성 검사
누락 파일 복구
중복 처리
Provenance 기록
Target과 Frame 연결
```

#### 목표

ASTERION을 연구 플랫폼으로 확장할 수 있는 데이터 기반을 만든다.

---

### Phase 6. Console UI / Command UI

Muuri 기반 통합 대시보드와 명령 팔레트를 정리한다.

#### 해야 할 일

```text
ASTERION Console
Device cards
Capture card
Watchtower card
Telemetry card
Sky Monitor
Night Timeline
Command Palette
Device Settings
```

#### UI 주의사항

```text
장비 연결 UI와 관측 운영 UI 분리
필터휠은 Capture 흐름에 흡수
기상은 Watchtower 판단으로 표시
상태 색은 의미 있을 때만 사용
```

---

### Phase 7. Orchestrator / Meridian

진짜 관측 workflow를 실행한다.

#### 해야 할 일

```text
관측 요청 생성
대상 선택
필터/노출 전략 입력
안전 확인
장비 명령 순서 실행
실패 시 복구
프레임 저장 확인
다음 시퀀스로 이동
```

#### 목표

ASTERION을 단순 대시보드에서 관측 실행 시스템으로 발전시킨다.

---

### Phase 8. Sentinel / Forge 구조 연결

ML은 아직 하지 않아도 된다.  
다만 분석 구조를 연결할 자리를 만든다.

#### 해야 할 일

```text
프레임 수신 이벤트
기본 메타데이터 추출
품질 지표 placeholder
accepted / warning / rejected 구조
Forge pipeline interface
Calibration Library 구조
```

---

### Phase 9. Skygraph / Target Page

대상 중심 구조를 만든다.

#### 해야 할 일

```text
Target entity
Observation request
Sequence
Frame
Quality metric
Weather condition
Calibration product
Analysis result
관계 정의
```

#### 목표

파일 중심이 아니라 대상·관측·과학목표 중심으로 데이터 구조를 바꾼다.

---

### Phase 10. AI Agent

마지막에 붙인다.  
처음에는 읽기·설명·추천만 한다.

#### 해야 할 일

```text
상태 요약
안전 판단 이유 설명
실패 원인 추정
관측 추천
로그 검색
장비 연결 진단
사용자 승인 기반 명령 요청
```

#### 원칙

```text
AI Agent → 추천
User / Policy 승인
Orchestrator 실행
```

AI Agent가 Device Core를 직접 만지지 않는다.

---

## 17. 개발 세션 분리

Claude, Codex, Cursor 같은 AI 코딩 도구를 사용할 때도 세션을 나누어야 한다.  
한 세션에서 모든 구조를 다루면 코드가 쉽게 꼬인다.

| 세션 | 다룰 것 | 다루면 안 되는 것 |
|---|---|---|
| Architecture Session | 전체 구조, 모듈 관계, 문서화 | 세부 UI 디자인 |
| Device Session | 장비 연결, Adapter, Capability, Registry | 데이터 분석 |
| State/Event Session | State Store, Event Bus, 상태 동기화 | 장비별 UI |
| Weather/Safety Session | Weather Agent, Watchtower, Policy | AI Agent |
| Archive Session | DB, 파일 저장, 복구, Provenance | 실시간 장비 제어 |
| Console UI Session | Muuri 대시보드, 카드 UX | 드라이버 로직 |
| Operation Session | Orchestrator, Meridian, 관측 workflow | ML 분석 |
| Analysis Session | Sentinel, Forge, Calibration 구조 | 자율 판단 |
| Skygraph Session | 온톨로지, Target Page, 관계 모델 | 장비 연결 |
| AI Agent Session | 설명, 추천, 로그 검색, 승인 기반 명령 | 직접 장비 제어 |
| Infrastructure Session | PC Agent, API, Plugin, Access, Alert | 카드 디자인 |

---

## 18. 최종 우선순위

지금 당장 중요한 순서는 다음과 같다.

```text
1. Device Manager / Device Core
2. State Store
3. Event Bus
4. Weather Store / Watchtower
5. Archive / Recovery
6. Console UI
7. Orchestrator
8. Meridian
9. Sentinel / Forge 구조
10. Skygraph
11. AI Agent
```

AI와 ML은 나중에 해도 된다.  
하지만 State, Event, Archive, Provenance 구조는 처음부터 잡아야 한다.  
이 구조들은 나중에 붙이기 어렵기 때문이다.

---

## 19. 가장 중요한 설계 원칙 7개

### 1. 장비는 직접 UI에 붙지 않는다.

```text
UI → Device Core → Adapter → 장비
```

### 2. 모든 장비는 ASTERION 표준 모델로 번역된다.

```text
ASCOM Mount
PWI Mount
Simulator Mount
        ↓
MountState
```

### 3. 현재 상태는 State Store가 단일 기준이다.

```text
Console도 State Store를 보고
Watchtower도 State Store를 보고
AI Agent도 State Store를 본다
```

### 4. 모듈 간 통신은 Event Bus를 거친다.

```text
Watchtower → Event Bus → Orchestrator
```

### 5. 안전 판단은 fail-closed다.

```text
기상 데이터 없음 = unsafe
센서 stale = unsafe
네트워크 끊김 = warning 또는 unsafe
```

### 6. 데이터는 파일이 아니라 Target과 Observation 중심으로 저장된다.

```text
Frame → Observation → Target → Science Goal
```

### 7. AI Agent는 직접 제어자가 아니라 해석자이자 제안자다.

```text
AI Agent → 추천
User / Policy 승인
Orchestrator 실행
```

---

## 20. 요약

ASTERION OS의 핵심은 장비를 많이 연결하는 것이 아니다.  
핵심은 장비, 기상, 데이터, 안전, 스케줄, 분석, AI 판단을 하나의 상태 체계와 사건 흐름 안에서 통제하는 것이다.

초기 개발의 핵심 뼈대는 다음 다섯 가지이다.

```text
Device Manager + Device Core
State Store
Event Bus
Watchtower
Archive Recovery
```

이 다섯 개가 잡히면 ASTERION OS는 단순한 통합 대시보드가 아니라 자율 관측 운영체제로 발전할 수 있다.
