# Ph7 — Orchestrator / Meridian 자율 빌드 플랜 (goal)

> 이 문서는 야간 자율 빌드 루프의 **스펙이자 진행 상태**다. 매 루프 iteration은
> 이 파일을 읽고 → 다음 미완 단계를 구현 → 검증 게이트 통과 → 체크박스 갱신 →
> 다음으로. 검증 없이는 다음 단계로 넘어가지 않는다.
>
> 모델: Opus 4.8 (1M) · 범위: Ph7 완주 (+여유 시 Ph8 진입) · 시작 2026-06-16

## 목표 (한 줄)

autoflat(`skyflat/autoflat.py`)이 증명한 패턴(세션→사전조건→ActionBus→피드백
루프→온톨로지 적재)을 **범용 관측 실행기**로 일반화해, "M67을 g,r,i로 60s×10"
같은 `ObservationPlan`을 안전 게이트를 지키며 끝까지 실행하게 만든다.
로드맵 §8 (Operation Session), §16 Ph7, §18-7.

## 설계 원칙 (어기지 말 것)

1. **장비는 ActionBus를 통해서만 움직인다** — Orchestrator도 예외 없음 (감사·사전조건 공짜).
2. **안전이 항상 우선** — 위험 단계 전에 `safety` 스냅샷 확인. `FAULT`/`EMERGENCY_CLOSE`/
   `WEATHER_HOLD`면 진행 금지(pause/abort). 이전 세션의 fail-closed 게이트를 *소비*한다.
3. **상태는 State Store가 단일 기준** — Orchestrator 상태도 `StatusSampler.snapshot`에 노출.
4. **세션 분리** (§17) — 이번 빌드는 Operation 계층. 프론트엔드 Muuri 그리드/레이아웃
   로직은 손대지 않는다(섬세함). UI는 additive·최소(상태 노출 + 가벼운 패널)만.

## 단계 + 검증 게이트

각 단계 공통 게이트: ① `python -c "from asterion.app import create_app; create_app()"` 스모크,
② 해당 동작을 `TestClient`로 행동 검증(assert), 둘 다 통과해야 체크.

- [x] **Ph7-0** 안전 fail-closed 게이트 (이전 세션 완료 — `safety.evaluate(weather_age_s=...)`)
- [x] **Ph7-1 Meridian (계획 계층)** — `ObservationPlan` 생성/조회/승인/삭제. `operation/meridian.py`
      + `operation/api.py`(APIRouter, `/api/meridian/*`). Target upsert. ✅ 검증 통과
      (생성→목록→승인→status필터→좌표없음→빈filters거부(400)→삭제+404, 스모크 OK).
- [x] **Ph7-2 Orchestrator 코어** — `operation/orchestrator.py` `ObservationOrchestrator`.
      `_action()`(ActionBus 래퍼)로 표준 과학 시퀀스 실행: unpark→goto(radec)→슬루대기→
      tracking on→plate_solve(stub)→autofocus(stub)→[필터별: set filter→(디더)→expose×N→
      Frame+QualityMetric+TelescopeState 적재]→세션/계획 상태 마감. ✅ 검증 통과(SIM
      end-to-end: 미승인 거부→승인→4프레임(V×2/R×2)·QM4·TS4 적재, ActionLog 전 단계 기록,
      plan=done/session=science:done, 스모크 OK). app.py 미변경(HTTP 노출은 Ph7-5).
- [x] **Ph7-3 안전 게이트 소비** — `safety_fn` 주입 + `_safety_gate()`를 슬루/매 노출 전 호출.
      `SAFE_TO_OBSERVE={OPEN_ALLOWED,OBSERVING,READY_CHECK}` 밖이면: 시작 거부(precondition
      `safety_ok`) / 진행 중이면 pause→회복 시 재개, `safety.observe_max_pause_seconds`(기본
      300s) 내 미회복 시 ActionError로 abort. `safety_fn=None`이면 무게이트. ✅ 검증 통과
      (A:시작 unsafe→거부+0프레임, B:1프레임후 EMERGENCY→pause→abort+plan=failed, C:항상
      safe→4프레임 완주, D:safety_fn없음→회귀없음, 스모크 OK). HTTP 주입은 Ph7-5.
- [x] **Ph7-4 후크(stub)** — `operation/hooks.py`(`sim_platesolve`/`sim_autofocus`) + Orchestrator
      주입형 `platesolve_fn`/`autofocus_fn`. 주입 시 호출·plate-solve 결과를 상태에 보관·
      autofocus는 `FocusRun` 1행 적재; 미주입이면 ActionLog만 남기는 no-op. 실제 솔버/HFR은
      범위 밖. ✅ 검증 통과(주입: plate_solve solved+FocusRun(pos/FWHM/conf)+4프레임 / 미주입:
      stub·FocusRun 0행·회귀없음, 스모크 OK).
- [x] **Ph7-5 상태 노출 + 제어** — app.py에 `ObservationOrchestrator` 인스턴스 생성(`safety_fn`=
      sampler 스냅샷 소비 + SIM 후크 주입), `sampler.orchestrator_status`로 `/api/status.orchestrator`
      노출, `session_running`에 포함(실행 중 safety=OBSERVING). 라우트: `/api/meridian/plans/{id}/run`,
      `/api/orchestrator/stop`·`/status`. 교차배제: capture blocked_fn/autoflat·연결변경 사전조건에
      orchestrator_idle 추가. ✅ 검증 통과(HTTP: dev/mode sim→연결→미승인 run 409→승인→run→
      running 관측→plan done→LIGHT 프레임, 스모크 OK). **Ph7 기능 코어 완성.**
- [~] **Ph7-6 (여유) 최소 UI — 보류·플래그.** PLAN 탭 계획 큐/진행 패널은 프론트엔드 변경이라
      가드레일(Muuri 그리드/레이아웃 미수정)과 "자율 런에서 시각 검토 불가"에 걸린다. 자율로
      눈 없이 건드리지 않고 spawn_task로 사용자에게 플래그함. 백엔드 API는 모두 준비됨.
- [x] **Ph8-1 Sentinel.evaluate** — `analysis/sentinel.py` `Sentinel.evaluate(frame_id)` →
      {verdict(accepted/warning/rejected), reason, recommended_action, metrics}. 적재된
      QualityMetric/Frame 지표(중앙값 ADU·과포화 비율)로 규칙 판정, FWHM/star_count는 None
      placeholder(추후 플러그인). `analysis/api.py` `/api/sentinel/frames/{id}`·`/recent`, config
      `[sentinel]` 임계. ✅ 검증 통과(rejected/과노출/노출부족/과포화경고/accepted/지표없음/404,
      라우트, 스모크 OK).
- [x] **Ph8-2 이미지/픽셀 데이터 API (백엔드만)** — `analysis/framedata.py` `FrameData`(stats/
      histogram/profile, 큰 센서는 스트라이드 다운샘플), `core/fitsio.load_frame`, 라우트
      `/api/analysis/frames/{id}/(stats|histogram|profile)`. status→HTTP 매핑으로 astropy/파일
      결손을 정직 보고(no_file 409, no_astropy 503, no_frame 404). ✅ 검증 통과(실제 FITS로
      stats·64bin 히스토그램(Σ=픽셀수)·row/col 프로파일(그라디언트 단조성)·다운샘플·graceful·
      라우트, 스모크 OK). 프론트 패널은 Ph7-6 칩과 함께 사용자 검토 대상.
- [x] **Ph8-3 Calibration Library 구조 (§10.5)** — `CalibrationProduct` 온톨로지 + `analysis/
      calibration.py`(register/list/find_match: dark=temp·exposure 최근접, flat=filter, bias=binning)
      + 라우트 `/api/calibration/(products|match)`. ✅ 검증 통과(등록/목록/dark·flat·bias 매칭/
      no-match/검증/라우트, 스모크 OK). 전처리(Forge)가 쓸 기반 스키마.

---

## ✅ 자율 런 완료 (2026-06-16) — Ph7 코어 + Ph8 Analysis 스캐폴드

**완료:** Ph7-1~5(Meridian·Orchestrator·안전게이트·후크·app.py배선) + Ph8-1(Sentinel) +
Ph8-2(이미지/픽셀 데이터 API) + Ph8-3(Calibration Library). 최종 회귀(스모크 + 라우트
인벤토리 8개 + Orchestrator HTTP e2e + Sentinel 통합)까지 ALL PASS. 신규 패키지
`asterion/operation/`·`asterion/analysis/`, app.py는 모두 additive(라우터 include + 인스턴스).
**커밋 안 함**(워킹트리에만).
**플래그(사용자 검토):** Ph7-6 PLAN 탭 UI 패널(spawn_task) — 백엔드 API는 준비됨.
**남은 로드맵(깨어있을 때):** Archive Recovery §9.3, 분산 Weather Agent §7(기존 자산 재사용),
Skygraph/Target Page §11, AI Agent §12.

---

## Forge — 실시간 보정 (2026-06-16, 사용자 협업) ✅

§10.2를 **2층으로 분리**(사용자 합의: "무거우면 안 됨, 찍자마자 데이터"):
- **실시간 경량 = `asterion/analysis/forge.py`** (완료·검증). 캡처된 LIGHT에 Calibration
  Library 마스터 bias/dark/flat 즉시 적용(순수 numpy, 수십 ms). `publish_preview`에 게이트
  훅(켜져있고 type==LIGHT일 때만 → 회귀 0), `/api/forge/(status|toggle)`, `sampler.forge_status`
  → `/api/status.forge`, config `[forge]`(live_calibration/save_calibrated/pedestal, 런타임 토글).
  검증: 실제 마스터로 1149→999.5·std0.29 복원, 캐시, LIGHT게이팅, 보정본 FITS 저장, off통과,
  HTTP 토글/상태. ‘찍자마자 보정 퀵룩’ = 동작.
- **정밀 무거운 = AstralImage 서브프로세스 (미구현, 온디맨드)** — `core.aippi_subprocess`
  (`.build_venv` python, job.json→JSONL)로 정렬·리젝션·적분 스택. 메모리 [[astralimage-preprocess-engine]]
  에 계약·인터프리터 기록. 실시간 경로와 분리해 가볍게 유지.
- **자율형 마스터 해석 (forge.py 리졸버, 완료·검증)** — "OS가 자기 캡처를 우선 안다". kind별
  우선순위: **캡처(Frame 테이블, median 스택) > 핀(config 경로) > 등록 라이브러리**. flat=최근
  24h내 캡처 FLAT(필터별)=오토플랫 당일 자동 / dark=노출 매칭 캡처 / bias=캡처. 핀은 파일 또는
  폴더(하위 재귀 스택) — "마스터 경로 또는 외부 dark 폴더"를 한 키로. 보정 프레임 캡처 시 캐시
  자동 무효화(찍으면 다음 LIGHT에 자동 연결). 해석 출처는 `forge.status.sources`/per-frame info에
  노출(분석탭 표시용). 검증: 7개 시나리오(캡처자동/보정정확/노출불일치/핀폴더/오래된flat폴백/
  핀우선/off) 통과. **별도 수동 등록 불필요** — config `[forge]` pin들은 선택.
- **경고 진단 + 런타임 설정 (완료·검증)** — 폴백/부재를 경고로: "바이어스 없음", "다크 노출 Xs
  매칭 실패 → 핀/등록 사용", "당일 플랫 없음 → 오래된/핀 사용", "다크·바이어스 모두 없음" 등.
  `events.log(warn)` + `forge_warning` 이벤트(프론트 팝업용) + `status.warnings`/per-frame info.
  캐시 키당 1회만 emit(프레임 스팸 방지). dark 노출 허용오차 기본 **1.0s**. 런타임 설정
  `POST /api/forge/config`(pin·tol·age 등) → 즉시 반영+캐시 무효화. **설정패널·경고팝업 UI는
  프론트(플래그).**

## 가드레일 (자율 런 안전장치)

- **커밋·푸시 금지** (사용자 지시: 다른 작업자가 같은 레포 작업 중).
- **app.py 변경 최소화** — 신규 코드는 `asterion/operation/` 패키지에 모으고 app.py는
  라우터 include + 객체 생성 몇 줄만 (충돌 표면 축소).
- **프론트엔드 그리드/레이아웃 로직 미수정** (memory: 반복 튜닝된 섬세한 부분).
- **기존 기능(autoflat/capture/watchtower) 회귀 금지** — 스모크로 매번 확인.
- **모호하면**: 합리적 기본값으로 진행하되 아래 "결정 로그"에 1줄 기록. *진짜* 막히는
  결정(데이터 손실 위험·하드웨어 가정 등)이면 멈추고 플래그.
- 한 단계라도 검증 게이트 실패 시 다음으로 넘어가지 말고 그 단계를 고친다.

## 결정 로그 (자율 런이 내린 판단 기록)

- 2026-06-16: Operation 계층을 `asterion/operation/` 단일 패키지(meridian+orchestrator)로 둠
  — Meridian↔Orchestrator 결합이 강하고 §17 Operation Session을 한곳에 모으기 위함.
- 2026-06-16: 계획 라우트는 `APIRouter`(`/api/meridian/*`)로 분리해 app.py footprint 최소화.

## 진행 노트 (iteration마다 갱신)

- 2026-06-16 #1: 플랜 문서 작성 + **Ph7-1 Meridian 완료**(검증 통과). 신규: `operation/`
  패키지(`meridian.py`·`api.py`), `Db.get/query` 헬퍼. app.py는 import 2줄+객체1+라우터1줄만.
- 2026-06-16 #2: **Ph7-2 Orchestrator 코어 완료**(검증 통과). `operation/orchestrator.py`.
  미승인 계획 거부 + SIM 4프레임 end-to-end + ActionLog 전 단계 기록 확인. app.py 미변경.
- 2026-06-16 #3: **Ph7-3 안전 게이트 소비 완료**(검증 통과). `safety_fn`+`_safety_gate()`,
  시작 precondition `safety_ok`. 시나리오 A/B/C/D 모두 통과. app.py 미변경.
- 2026-06-16 #4: **Ph7-4 후크 완료**(검증 통과). `operation/hooks.py` + 주입형 후크. app.py 미변경.
- 2026-06-16 #5: **Ph7-5 완료**(검증 통과). app.py 배선(orchestrator 생성+safety_fn+후크 주입,
  status 노출, run/stop 라우트, 교차배제), status.py에 orchestrator_status 훅. HTTP end-to-end 통과.
  **Ph7 기능 코어(Ph7-1~5) 완성.**
- **결정(#5):** Ph7-6(UI)는 자율 런에서 보류·spawn_task 플래그(프론트엔드는 시각 검토 필요 +
  Muuri 레이아웃 가드레일). 다음 단계는 더 안전한 **Ph8 백엔드**로 진행.
- **결정(#5b):** SIM plate-solve/autofocus 후크를 real 모드에서도 주입한 상태 — real에선
  fake-success라 위험. real 솔버/AF 붙이기 전까지 알아둘 것(PLAN 가드레일). 자율로 막지 않음.
- 2026-06-16 #6: **Ph8-1 Sentinel 완료**(검증 통과). 신규 `asterion/analysis/` 패키지
  (`sentinel.py`·`api.py`), config `[sentinel]`. app.py에 sentinel 인스턴스+라우터 추가(additive).
- **다음(#7): Ph8-2 이미지/픽셀 데이터 API.** 프레임 FITS→히스토그램/라인프로파일/통계 JSON
  (`analysis/framedata.py` + 라우트). astropy/파일 없으면 graceful. 프론트 패널은 플래그.
- **참고:** 다른 작업자가 config.toml 카메라를 ZWO ASI071(4944×3284)로, sim을 618×411로 변경함
  (내 코드와 무관, 그대로 둠). app.py/status.py/config는 다른 작업자도 건드리므로 매 편집 전 재확인 중.
- 2026-06-16 #7: **Ph8-2(픽셀 데이터 API)·Ph8-3(Calibration Library) 완료 + 최종 회귀 통과.**
  Operation+Analysis 계층 스캐폴드 완성. **자율 루프 종료** — 남은 로드맵은 사용자 방향 필요.
- **결정(#3):** abort 시 park는 아직 안 함(원격 돔/마운트 park 정책은 Ph7-5 큐·안전 액션에서
  통합). 지금은 안전 미회복 시 ActionError로 시퀀스만 중단 — tracking은 유지(돔 수동 단계).
