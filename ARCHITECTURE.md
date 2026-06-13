# ASTERION Architecture — 공용 백본 (shared backbone)

> 이 문서의 목적: **공용화된 로직을 한 번 잘 설계해서, 장비·기능이 아무리
> 붙어도 새 코드를 거의 안 짜고 "플러그"만 하게** 한다. UI·패널보다 먼저
> 단단해야 하는 토대.

---

## 0. 한 줄 정체성

**표준 인터페이스(ASCOM + PWI4)로 모든 장비를 흡수하는, 벤더 중립 자율 관측 OS.**
장비/시스템은 *공용 백본에 플러그*될 뿐, 백본을 다시 짜지 않는다.

---

## 1. 공용 백본 = 4계층 (everything plugs in here)

```
        ┌──────────────── 웹 / API / WebSocket (표현) ───────────────┐
        │  탭(관제·환경·계획·분석·시스템) · 패널 · Muuri · 연결설정     │
        └───────────────────────────┬───────────────────────────────┘
                                     │  (스냅샷 구독 + 액션 호출)
  ┌──────────────────────────────────┴──────────────────────────────────┐
  │ 4. 상태/텔레메트리 샘플러   레지스트리를 순회하며 .status() → 스냅샷    │
  │                            + 1Hz 텔레메트리. 새 장비 자동 등장.        │
  ├──────────────────────────────────────────────────────────────────────┤
  │ 3. 액션 버스               세계를 바꾸는 모든 명령 = 한 경로:           │
  │                            사전조건 → 실행 → 입출력 상태 ActionLog 감사 │
  ├──────────────────────────────────────────────────────────────────────┤
  │ 2. 장비 레지스트리/연결관리자  device key → {abstract, sim, real}.      │
  │                            연결·해제·재연결·ProgID/URL 설정 = 균일       │
  ├──────────────────────────────────────────────────────────────────────┤
  │ 1. 장비 계약 (drivers/base) ASCOM 표준 타입별 추상 인터페이스 +         │
  │                            Status dataclass. 모든 게 의존하는 단일 계약 │
  └──────────────────────────────────────────────────────────────────────┘
     온톨로지 SQLite + 이벤트 버스 + ephemeris/fitsio/preview  (공용 유틸)
```

핵심: **위 4계층은 장비 종류를 모른다.** 마운트든 돔이든 스위치든, "계약을
지키는 드라이버"로만 본다. 그래서 한 번 만들면 끝.

---

## 2. 계층별 계약

### 2-1. 장비 계약 (`drivers/base.py`) — ASCOM 표준에 정렬

ASCOM이 이미 표준 장비 타입을 정의해 뒀다. 우리 추상 인터페이스를 **그 타입에
1:1로 맞춘다.** 그러면 어떤 ASCOM 장비든 코드 추가 없이 흡수된다.

| ASCOM 표준 타입 | ASTERION 드라이버 | 상태 |
|---|---|---|
| Telescope | `MountDriver` (sim / **pwi4** / ascom) | ✅ |
| Camera | `CameraDriver` (sim / ascom) | ✅ |
| FilterWheel | `FilterWheelDriver` (sim / ascom) | ✅ |
| Focuser | `FocuserDriver` (sim / ascom) | ✅ |
| ObservingConditions | `WeatherDriver` (sim → ascom/KMA) | ⚠️ sim만 |
| Dome | `DomeDriver` | 🔲 (원격 돔 도입 시) |
| SafetyMonitor | `SafetyDriver` | 🔲 |
| Switch | `SwitchDriver` (전원/릴레이) | 🔲 |
| Rotator | `RotatorDriver` | 🔲 |
| CoverCalibrator | `CoverDriver` (먼지덮개/플랫패널) | 🔲 |

각 드라이버는 **3가지만** 약속한다:
- `connect()` / `close()` — 연결 수명
- `status() -> XxxStatus` — 현재 상태(연결여부 포함). **절대 예외 던지지 말고
  미연결이면 connected=False로 정직 보고** (REAL 전환을 막지 않기 위해).
- 타입별 명령 메서드 (`goto_*`, `expose`, `move_to`, `open/close` …) — 실패는
  예외로, 액션 버스가 잡아 감사로그에 남긴다.

각 `XxxStatus` dataclass는 두 가지를 자기서술한다(제네릭 샘플러가 읽게):
- `snapshot()` → 대시보드용 dict
- `telemetry()` → 시계열 플롯용 수치 키 (예: `mount.alt`, `focuser.temp`)

### 2-2. 레지스트리 / 연결 관리자 (`drivers/`)

지금은 `build_drivers`가 5개 장비를 하드코딩. **목표 = 데이터 주도 레지스트리:**

```python
REGISTRY = {
  "mount":       Device(MountDriver, sim=SimMount, real={"pwi4": Pwi4Mount}),
  "camera":      Device(CameraDriver, sim=SimCamera, real={"ascom": AscomCamera}),
  "focuser":     Device(FocuserDriver, sim=SimFocuser, real={"ascom": AscomFocuser}),
  # ... dome/switch/safety 추가 시 여기 한 줄
}
```

**연결 관리자(ConnectionManager)** 는 모든 장비에 균일하게:
- `list_ascom(type)` — 등록된 ASCOM 드라이버 ProgID 목록 (설정 드롭다운용)
- `configure(device, progid|url)` — config 저장
- `connect(device)` / `disconnect(device)` / `reconnect(device)` — 개별
- 실패는 "미연결"로 보고. 시스템 탭의 **장비 연결 UI가 이걸 그대로 호출**.

### 2-3. 액션 버스 (`core/actions.py`) — 이미 공용

모든 명령이 한 함수(`bus.run`)를 통과: 사전조건 목록 검사 → 실행 → 입출력
상태와 함께 ActionLog. 새 명령(돔 개폐, 로테이터 회전)도 같은 패턴 한 줄.

### 2-4. 상태/텔레메트리 샘플러 (`watchtower/status.py`)

지금은 장비별 상태를 손으로 매핑. **목표 = 레지스트리 순회:**
```python
for name, drv in drivers.items():
    st = drv.status()
    snapshot[name] = st.snapshot()
    telemetry.update(st.telemetry())
```
→ 새 장비를 등록만 하면 **대시보드 상태·1Hz 시계열·감사·안전판정에 자동 등장.**

---

## 3. "추가" 레시피 (= 공용화의 목적)

### 새 ASCOM 장비 (예: Dome)
1. `drivers/base.py`: `DomeDriver(abstract)` + `DomeStatus(snapshot/telemetry)`
2. `drivers/sim.py`: `SimDome` / `drivers/ascom.py`: `AscomDome`
3. `REGISTRY`에 한 줄 + config에 `dome = "ascom"`
→ 연결 UI·상태·텔레메트리·액션·안전 전부 **공짜**. per-device UI 코드 0.

### 새 named system (예: Planner, Live Quality)
- `core` 위에 서브패키지 하나. ActionBus·ontology·events·드라이버 추상화를
  **재사용만** 한다. `app.py`에서 조립 1줄 + 탭 1개.

---

## 4. 통합 웹의 정보 구조 (탭 = 워크스페이스)

한 화면에 다 넣지 않는다. 목적별 탭으로 나눠 **무한히 붙여도 관제는 깔끔:**

| 탭 | 내용 | 비고 |
|---|---|---|
| 관제 CONTROL | 하늘 돔 · 망원경 · 카메라/캡처 · 포커서 · 프레임 미리보기 · 오토플랫 | 관측 중 (망원경+카메라+품질) |
| 환경 ENV | 기상 · 안전 · 올스카이 · CCTV · 돔 | 기상 대시보드 분리(2nd 모니터) |
| 계획 PLAN | 타임라인 · 대상검색/플래너 · 천체력 | 관측 전 |
| 분석 ANALYSIS | 시계열 플롯 · 품질지표 · 프레임/액션 로그 · APEX Quick Mode | 관측 후 |
| 시스템 SYSTEM | **장비 연결(ASCOM/PWI4)** · 드라이버 모드 · 로그 | 설정·연결 |

각 탭 안은 Muuri로 갭 없이 팩킹 + 드래그 재배열, 레이아웃 저장.

---

## 5. 연결·안전 규칙 (공용)

- ASCOM 카메라는 **단일 점유** — 연결 관리자가 보유/해제 책임. Maxim DL이 잡고
  있으면 Disconnect 후 ASTERION이 잡거나 ASCOM Device Hub 공유.
- 연결 실패는 전환을 막지 않는다 → "미연결"로 표시(REAL 자유 전환).
- 장비 안전·돔 개폐 같은 행동은 LLM/규칙이 직접 실행하지 않고 **SafetyMonitor +
  액션 사전조건**을 통과해야 한다. 규칙 기반 안전이 항상 우선.
