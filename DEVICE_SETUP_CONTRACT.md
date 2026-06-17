# DEVICE SETUP — 계약서 (Frontend ↔ Backend)

> 목적: per-device "Setup"(MaximDL 깊이 — 필터표·Focus Offset·게인·Max ΔT·jog 모드 등)을
> **기존 장비 연결 시스템 위에** 얹기 위한 데이터/엔드포인트 계약. 프론트(web/, 담당: Claude)와
> 백엔드(drivers/·app.py, 담당: Codex)가 이 문서만 보고 병렬 작업할 수 있게 한다.
>
> 작성 근거: `web/app.js`, `drivers/__init__.py`, `app.py`, `config.py`, `drivers/ascom.py`,
> `watchtower/status.py` 실측. 줄번호는 작성 시점 기준(이동 가능, 함수명으로 추적).

---

## 0. 한 줄 원칙

**연결(장비 연결 카드) → 식별(`device_name`) → Setup(드로어, configure) → 운영(장비 탭, operate).**
Setup은 새 시스템이 아니라 **기존 연결 카드의 확장**이다. KIND(ascom/pwi4/zwo/davis/sim)는 계속 숨긴다.

---

## 1. 정보구조 (4 표면 / Setup 3 단)

| 표면 | 역할 | 빈도 | 위치 |
|---|---|---|---|
| 장비 탭 | 운영(operate) — 쿨러·노출·GoTo·촬영 | 매 프레임 | mount/camera/… 탭 |
| **System 탭** | 연결·마스터모드·시스템 건강·로그 (pre-flight 보드) | 세션 시작/문제 시 | system 탭 |
| **Setup 드로어** | per-device 깊이 — 필터표·게인·Max ΔT | 가끔/장비 교체 시 | System 탭 카드의 `⚙` |
| 상단 ⚙ 설정 | house/app — 관측지·안전정책·단위·devmode | 거의 안 바뀜 | 상단바 |

Setup 깊이 3단: **conn-dev 카드(런처+요약) → Setup 드로어(기본/고급) → ASCOM 네이티브 SetupDialog(벤더 고유, 기존 `/api/system/setup`).**

---

## 2. 데이터 흐름 (3 채널 분리)

| 채널 | 내용 | 갱신 | 경로 |
|---|---|---|---|
| **config (쓰기)** | 운영자 Setup 값 | 저장 시 | `setup.{key}.*` → `Config.set` → `config.local.json` 오버레이 |
| **capabilities (읽기)** | 드라이버가 알려주는 능력/범위 | **connect 시 1회** | `driver.capabilities()` → 캐시 |
| **status (라이브)** | 실시간 상태 | **1 Hz** | `status()`/`snapshot()` → `/api/status` — **불변** |

> ⚠️ **절대 규칙: capabilities(필터 offset·게인 범위·축 rate 등 정적 값)를 1 Hz `status()`에 넣지 말 것.**
> `StatusSampler._sample`(`watchtower/status.py:201-333`)이 매초 3초 COM 타임아웃으로 status()를 폴링한다.
> 무거운 capability 읽기를 거기 넣으면 COM이 매초 멈춘다. → connect 시 1회 읽어 드라이버에 캐시.

`describe()`는 config(setup) + caps를 합쳐 UI에 한 번에 준다(정적). 라이브는 별도 `/api/status`.

---

## 3. config 스키마 — `setup.{key}.*` (config.local.json 오버레이)

`Config.set(dotted, value)`(`config.py:63-79`)로만 쓴다. config.toml(사람 소유)은 건드리지 않는다.

```jsonc
// config.local.json (deep-merge over config.toml)
{
  "setup": {
    "camera": {
      "gain": 0,
      "offset": 10,
      "max_dt_c": 1.0,            // 쿨러 램프 안전한계 (°C/min) — 쿨다운/웜업이 준수
      "default_setpoint_c": -10,  // 카메라 탭 쿨러 초기값(시드). 라이브 조작은 탭
      "default_binning": "1x1",
      "readout_mode": "HighGainDR",
      "max_exposure_s": 3600,
      "fan": "auto",              // 고급
      "anti_dew": true,           // 고급
      "usb_limit_pct": 80,        // 고급
      "dual_chip": false          // 고급
    },
    "filterwheel": {
      "filters": [
        { "name": "B",  "focus_offset": 0, "flat_factor": 3.0 },
        { "name": "V",  "focus_offset": 0, "flat_factor": 1.6 },
        { "name": "Ha", "focus_offset": 5, "flat_factor": 8.0 }
      ]
    },
    "focuser": {
      "step_presets": [10, 100, 1000],  // ± 넛지 버튼 값(하드코딩 대체)
      "backlash": 80,
      "temp_comp": false,
      "temp_comp_coeff": 0.0
    },
    "mount": {
      "jog_mode": "rate",                       // "rate"(PWI3/4 press-hold) | "step"(이산)
      "jog_rates": ["slow", "fast"],            // rate 모드
      "jog_steps_arcsec": [5, 10, 30, 60, 300]  // step 모드
    }
  }
}
```

**기존 키와의 관계 (마이그레이션):**
- `setup.filterwheel.filters[].name` 이 `[filters].names`(config.toml, 기본 `["L","B","V","R","I","Ha"]`,
  `drivers/__init__.py:54`)를 대체한다. 오토플랫 하드코딩의 출처가 여기였음.
  → 최초 1회 `[filters].names` + ASCOM `d.Names`로 `setup.filterwheel.filters`를 시드. 전환 전까지 `filters.names`는 읽기 유지.
- `setup.camera.gain` 이 `drivers.zwo.gain`(`config.toml:45`)을 일반화. 구 키는 폴백으로 잠시 유지.

---

## 4. `capabilities()` 계약 — connect 시 1회 (드라이버별 신규 메서드)

`drivers/base.py`의 각 추상 드라이버에 `def capabilities(self) -> dict: return {}` 기본 구현 추가
(`base.py:159-353`). 연결되면 1회 호출해 드라이버 인스턴스에 캐시. sim/pwi4/davis 등 미지원은 `{}` 반환(graceful).

UI는 caps로 폼을 그린다(드롭다운 옵션·범위·읽기전용 표시), config(setup)로 현재값을 채운다.

```jsonc
// camera.capabilities()  — ASCOM 소스 (drivers/ascom.py)
{
  "gain_min": 0, "gain_max": 100,        // d.GainMin/GainMax   (또는 "gains": [...] 이산 모드)
  "offset_min": 0, "offset_max": 255,    // d.OffsetMin/OffsetMax (또는 "offsets": [...])
  "can_set_ccd_temperature": true,       // d.CanSetCCDTemperature
  "readout_modes": ["HighGainDR","LowGainHC"], // d.ReadoutModes
  "exposure_min_s": 0.001, "exposure_max_s": 3600, // d.ExposureMin/Max
  "pixel_size_um": 3.76,                 // d.PixelSizeX
  "max_binning": 4                       // d.MaxBinX
}
// filterwheel.capabilities()
{ "names": ["B","V","Ha"], "focus_offsets": [0,0,5], "slot_count": 7 }
//   d.Names  (ascom.py:394 — 이미 읽음)  +  d.FocusOffsets  (★ 아직 안 읽음 — 한 줄 추가)
// focuser.capabilities()
{ "max_step": 100000, "step_size_um": 1.1, "temp_comp_available": true }
//   d.MaxStep (ascom.py:~480 — 읽지만 max_position에 collapse됨)  +  d.StepSize  +  d.TempCompAvailable
// mount.capabilities()
{ "can_move_axis": true, "axis_rates": [[0.5, 4.0]], "can_pulse_guide": true }
//   d.CanMoveAxis(axis)  +  d.AxisRates(axis)  +  d.CanPulseGuide
```

> **가장 작은 첫 단계:** `AscomFilterWheel.status`(`ascom.py:386-414`)는 이미 `d.Names`를 읽는다.
> 바로 옆 `d.FocusOffsets` 한 줄만 추가하면 Focus Offset 표가 살아난다. 여기부터 시작 권장.

---

## 5. `describe()` 확장 (`drivers/__init__.py:496-519`)

`GET /api/system/devices`의 각 device 엔트리에 `setup`(현재값)과 `caps`(능력)를 추가:

```jsonc
// 기존: {key,label,backend,selected,real_kinds,ascom_type,config_kind,has_progid,has_url,progid,url}
// 추가:
{
  "...": "...(기존 필드)",
  "setup": { /* cfg.get(f"setup.{key}", {}) — §3 */ },
  "caps":  { /* 캐시된 capabilities() — §4, 미연결 시 {} */ }
}
```

→ 프론트는 추가 fetch 없이 `/api/system/devices` 하나로 폼을 그린다(폴백: 두 필드 없으면 free-text).

---

## 6. REST 엔드포인트

| 메서드 | 경로 | 상태 | 동작 |
|---|---|---|---|
| GET | `/api/system/devices` | **확장** | `describe()` + `setup`/`caps` (app.py:652-655) |
| POST | `/api/system/setup-config` | **신규** | `{device, setup:{...patch}}` → `conn.set_setup(device, patch)` → `cfg.set('setup.{device}.*')` → `describe()` 반환. `configure`(app.py:664-673) 패턴 그대로 |
| GET | `/api/system/capabilities?device=KEY` | **신규(선택)** | `driver.capabilities()` 스레드 호출. `list_ascom` 패턴. *또는* describe()에 폴드(권장) |
| POST | `/api/system/master` | **확인/추가** | `{mode:"sim"\|"real"}` → `cfg.set('drivers.mode', mode)` + rebuild. (master_mode 읽기는 있음, setter 확인) |
| POST | `/api/system/connect-all` · `/disconnect-all` | **확인/추가** | `conn.connect_all()`/`disconnect_all()` 노출 (connect_all 내부 존재) |
| GET | `/api/system/setup-config` | (불필요) | describe()에 포함되므로 별도 불요 |

신규 요청 모델(예, `app.py:142` `DeviceConfigReq` 옆):
```python
class SetupConfigReq(BaseModel):
    device: str
    setup: dict  # §3 부분 패치 (deep-merge)
```
`set_setup`는 `configure`(`drivers/__init__.py:483-494`)와 동형: device를 REGISTRY로 검증 → 필드별 `cfg.set` → 로그 → `describe()` 반환.

---

## 7. `status()` 불변 (§2 재강조)

1 Hz `status()`/`snapshot()`(`drivers/base.py`, `watchtower/status.py`)에는 **라이브 상태만**.
Setup 정적값/capability는 절대 넣지 않는다. 기존 snapshot 필드(connected/name/state/position/temp/…)는 그대로.

---

## 8. 소비 매핑 (누가 setup.* 를 읽나)

| setup 키 | 소비처 | 비고 |
|---|---|---|
| `setup.filterwheel.filters[].name` | 오토플랫 필터 시퀀스, 카메라 필터 select | 하드코딩 `B,V,R,I` / `[filters].names` 대체 |
| `setup.filterwheel.filters[].focus_offset` | 필터 교체 시 포커서 자동보정 — `/api/actions/filter` + orchestrator | ✅ 구현 (`core/focus_offset.py`, 상태없는 델타 off(new)−off(prev); autoflat 제외) |
| `setup.filterwheel.filters[].flat_factor` | 오토플랫 필터별 노출 보정 | ASTERION 전용(ASCOM 무관) |
| `setup.camera.gain/offset` | 카메라 factory/capture (빌드 시 cfg.get, 현 `drivers.zwo.gain`과 동일 방식) | |
| `setup.camera.max_dt_c` | 쿨러 램프 — 쿨다운/웜업이 준수 | 안전 |
| `setup.camera.default_setpoint_c/default_binning` | 카메라 탭 연결 시 초기값 시드 | 조작은 탭 |
| `setup.focuser.step_presets` | 포커서 ± 넛지 버튼 (현 `FocuserNudgeReq` 임의 delta 대체) | |
| `setup.mount.jog_mode` (+ `caps.axis_rates`) | 마운트 jog UI: rate면 press-hold, step이면 이산 | `/api/actions/mount/jog`(현 arcsec) rate 변형 필요할 수 있음 |

---

## 9. 경계 — 누가 무엇을

| 작업 | 파일 | 담당 |
|---|---|---|
| System 탭 정돈 (마스터모드·전체연결·`⚙` 런처) | `web/app.js`, `index.html`, `style.css` | **Claude** |
| Setup 드로어 UI (`.drawer` 재활용, 기본/고급, caps→폼·setup→값) | `web/app.js`, `style.css` | **Claude** |
| `connDevHtml` 확장 (`⚙ Setup` 행/요약) | `web/app.js:1389` | **Claude** |
| `deviceAction`에 `setup-open`/`setup-save` 분기 | `web/app.js:1479` | **Claude** |
| 패널 소비 (오토플랫 필터칩·포커서 스텝·jog 모드 렌더) | `web/app.js` | **Claude** |
| `describe()` + setup/caps merge | `drivers/__init__.py:496` | **Codex** |
| `set_setup()` + `POST /api/system/setup-config` | `drivers/__init__.py`, `app.py` | **Codex** |
| `capabilities()` per driver (d.FocusOffsets/Gains/MaxStep/AxisRates) | `drivers/base.py`, `ascom.py`, … | **Codex** |
| consumers (오토플랫·capture·orchestrator가 setup.* 읽기) | `autoflat`/`capture`/`orchestrator` | **Codex** |
| master-mode / connect-all 엔드포인트 | `app.py` | **Codex** |

프론트는 **폴백 우선**: `setup`/`caps`가 없어도 안 깨지고(빈 폼/free-text), 백엔드가 필드를 실으면 자동 업그레이드. → 두 사람이 순서 의존 없이 병렬.

---

## 10. 폴백 & 마이그레이션

1. **프론트 폴백:** `describe().setup` 없으면 기본값/빈 폼; `caps` 없으면 범위·드롭다운 대신 free-text 입력.
2. **필터 마이그레이션:** 최초 describe(또는 configure)에서 `setup.filterwheel.filters`가 비면
   `[filters].names`(+연결 시 `d.Names`)로 시드. 소비처 전환 전까지 `filters.names` 읽기 유지.
3. **게인:** `drivers.zwo.gain` → `setup.camera.gain` 일반화, 구 키 폴백.
4. **KIND 숨김 유지:** Setup의 운영 항목(필터표·게인·MaxΔT)은 `.devmode-only` 밖(운영자 노출),
   원시 backend/ProgID는 기존대로 `.devmode-only` 안.

---

## 11. 결정 필요 (Codex 확인 사항)

1. **caps 노출 방식:** describe()에 폴드(권장, UI fetch 1회) vs 별도 `/api/system/capabilities`?
2. **게인 모델:** ASCOM은 `Gains[]`(이산 이름) 또는 `GainMin/GainMax`(연속) 두 모드. caps가 둘 다 표현
   (`gains[]` 또는 `gain_min/max`) → UI가 분기. 확정 필요.
3. **mount jog rate 모드:** `/api/actions/mount/jog`(현 arcsec only)에 rate/press-hold 변형을 이 작업에
   포함할지, 별도 PR로 뺄지. (press-hold = pointerdown→move, pointerup→stop + 백엔드 워치독.)
4. **flat_factor 위치:** ASTERION 전용값 → `setup.filterwheel.filters[]` 확정.
5. ~~**focus_offset 소비 주체:** 필터 교체 시 offset 적용을 orchestrator가 소유 확인.~~
   ✅ 해결 — `core/focus_offset.py` 공용 헬퍼를 `/api/actions/filter`(수동)와 orchestrator
   `_do_filter`(과학촬영)가 호출. 상태없는 델타(off(new)−off(prev), prev=교체 직전 현재 필터).
   autoflat은 의도적 제외(플랫엔 초점 무관, 빠른 필터 순환에 포커서 churn 방지).
6. **connect-all/master 엔드포인트** 현존 여부 확인(없으면 추가).

---

## 12. 권장 착수 순서

1. **(Codex)** `AscomFilterWheel`에 `d.FocusOffsets` 읽기 + `capabilities()` 시작 → 필터표가 가장 적은 변경으로 살아남.
2. **(Codex)** `describe()`에 `setup`/`caps` merge + `set_setup`/`/api/system/setup-config`.
3. **(Claude)** System 탭 정돈 + `⚙` 런처 + Setup 드로어(필터휠부터), 폴백 포함.
4. **(양쪽)** 카메라(gain/maxΔT)·포커서(step_presets)·마운트(jog_mode) 순차 확장.
5. **(Codex)** consumers를 `setup.*`로 전환(오토플랫 필터·쿨러 램프·포커서 스텝).
