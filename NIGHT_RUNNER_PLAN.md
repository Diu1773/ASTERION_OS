# NIGHT RUNNER PLAN — 무인 야간 운영기 (자율 루프 계약서)

> **상태: ✅ S1~S7 완료(2026-06-20, /loop 자율빌드).** `operation/night_runner.py` NightRunner —
> 승인 시간표를 슬롯 순서대로(대기→안전게이트→실행→분류) 무인 시퀀싱. REST·status 노출, SIM 검증.
> 스트레치(§3 마지막, 월출몰/측광 merit)는 **미착수** — 사용자 판단 후 진행(가드레일 #7: 범위 외).

> 이 문서는 `/loop` 자율 세션이 **그대로 따라가는 체크리스트**다. 한 번에 한 단계씩,
> 각 단계는 검증게이트를 통과해야 다음으로 간다. 막히면 **깨끗한 상태로 두고 결정로그에
> 기록 후 멈춘다**. Ph7의 `PH7_ORCHESTRATOR_PLAN.md`와 같은 방식.

## 0. 목표 (한 줄)
승인된 ObservationPlan **시간표를 슬롯 순서대로 무인 실행**하는 층(`NightRunner`)을
`operation` 패키지에 추가한다 → "목표 한 줄 → 아침에 결과 한 묶음"이 SIM에서 성립.

## 1. 현재 상태 (이미 있는 것 — 새로 만들지 말 것)
- **스케줄러**: `agent/toolkit.py` `_night_plan`/`plan_night` — 비겹침 슬롯 시간표(draft) 생성.
  각 계획 `params`에 `slot_start`/`slot_end`("HH:MM" KST)·`slot_peak_alt`·`slot_moon_*`.
- **단일 실행기**: `operation/orchestrator.py` `ObservationOrchestrator` —
  `start_plan(pid)`(approved/queued만 수락) · `running()` · `request_stop()` · `wait()` ·
  `status_dict()`. 내부 `_run_plan`이 `RUNNING→DONE/ABORTED/FAILED`로 상태 마감 +
  Frame/Decision 적재. **단일 계획 루프는 이미 닫혀 있다** (DONE→`done_target_names()`→캠페인 자동 제외).
- **계획 CRUD**: `operation/meridian.py` `list_plans(status=)` · `set_status` · `approve_plan`.
- **안전(fail-closed)**: `watchtower/safety.py` 스냅샷(WEATHER_HOLD/EMERGENCY) — status에 노출됨.
  Orchestrator가 이미 `_safety_gate`로 소비. NightRunner도 **슬롯 진입 전 같은 게이트**를 본다.
- **REST 패턴**: `operation/api.py` `build_operation_router(meridian, orch)` — 여기에 nightrunner 라우트 추가.
- **app 배선**: `app.py`에서 meridian/orchestrator 생성·status 노출·교차배제. NightRunner도 여기 배선.

## 2. 설계 (NightRunner = Orchestrator 위 시퀀서, 장비 직접조작 X)
신규 `operation/night_runner.py` `class NightRunner`:
- 생성자: `(meridian, orchestrator, safety_fn, events, cfg)` — `safety_fn()`은 fail-closed 스냅샷 반환.
- 공개:
  - `async start(plan_ids=None, respect_slots=True)` — `plan_ids=None`이면 **승인된 계획 전체**를
    `slot_start` 순으로. 이미 도는 중이거나 Orchestrator가 `running()`이면 거부(교차배제).
  - `async stop()` — 진행 중 계획에 `orchestrator.request_stop()` + 큐 비움.
  - `status_dict()` — `{active, held, reason, current, queue:[...], done:[...], failed:[...]}`.
- 내부 루프(asyncio task): 큐의 각 계획에 대해
  1. `respect_slots`면 `slot_start`(오늘 KST)까지 대기. 이미 지났으면 즉시. 슬롯 끝(`slot_end`)이
     이미 지난 계획은 **skip**(사유 기록).
  2. **안전 게이트**: `safety_fn()`이 WEATHER_HOLD/EMERGENCY면 `held=True`로 **일시정지**,
     주기 폴링하며 해제 대기. `cfg [nightrunner].hold_skip_seconds` 초과로 막히면 그 계획 skip.
  3. `orchestrator.start_plan(pid)` → `await orchestrator.wait()`. 결과(plan 최종 status)를
     `done`/`failed`에 분류. **실패해도 다음 계획으로 계속**(밤을 멈추지 않음). stop 요청 시 중단.
  4. 다음 계획.
- 상태는 `app.py`가 `/api/status`의 `night_runner` 키로 노출(Orchestrator처럼).

## 3. 단계별 체크리스트 (각 단계 = 1커밋, 검증 후 다음)
- [x] **S1 — 스켈레톤**: `night_runner.py` `NightRunner`(start/request_stop/wait/status_dict, 빈 루프) +
  `app.py` 배선 + status.py `night_runner` 키. ✅검증: uvicorn 기동→`GET /api/status.night_runner`=
  `{active:false,queue:[],done:[]…}`. (프리뷰 매니저가 포트를 못 띄워 uvicorn 직접 8533로 검증)
- [x] **S2 — 큐 구성**: `_build_queue`가 승인 계획(또는 plan_ids)을 `slot_start`(야간분: 저녁<자정<새벽)
  순 정렬. respect_slots면 `slot_end` 경과분은 skipped. ✅검증: 역순 입력 3개→큐 21:30/23:50/01:00.
  ⚠️야간분 skip은 한낮 실행 시 저녁슬롯을 오탐(wrap) — **S5에서 실제 datetime 앵커로 정밀화**. 운영(야간)·
  respect_slots=False에선 무관.
- [x] **S3 — 실행 시퀀스**: `_loop`이 큐를 순서대로 `_run_one`(start_plan→wait→get_plan 상태로
  done/failed 분류). 개별 실패는 흡수해 밤 계속, `_stop` 시 break. ✅검증(FakeOrch): ①slot순[2,1,3]
  전부 done ②중간실패→나머지 계속 ③정지→잔여 미실행(phase 정지됨) ④교차배제(nr중복·orch실행중 거부).
  풀스택 SIM e2e(실드라이버)는 S7 회귀에서.
- [x] **S4 — 안전 게이트/홀드**: `_safety_hold`가 슬롯 진입 전 `safety_fn()` 소비 — SAFE_TO_OBSERVE면
  즉시 진행, 아니면 held=True로 회복 대기. 회복→진행, hold_skip_seconds 초과→skip, 정지→중단.
  ✅검증(주입 safety_fn): ①unsafe중 held=True→회복→done ②미회복→skip(orch 미호출) ③안전→정상.
- [x] **S5 — 슬롯 타이밍**: `_slot_dt`가 'HH:MM'을 'now-6h grace 이후 가장 이른 발생'에 datetime
  앵커링(S2 야간분 wrap 제거 — 한낮 실행도 저녁슬롯 올바름). `_await_slot`: slot_start까지 대기(과거
  grace내 즉시), slot_end 경과면 skip, stop이면 중단. `_build_queue`는 정렬 전용으로(skip 이관).
  ✅검증: 앵커링 5케이스(낮/새벽/익일), await run/skip/immediate, _loop respect_slots True(대기후실행)·False(즉시).
- [x] **S6 — REST + stop**: api.py `build_operation_router(..., night_runner)` +
  `POST /api/nightrunner/start`(body NightRunStartReq: plan_ids·respect_slots, 생략가능)·
  `/stop`·`GET /status`. app.py 라우터 배선. ✅검증(TestClient+FakeNR): start(body/기본값)
  위임·stop·status + night_runner=None 503 가드. (ActionError→409는 app 전역 핸들러가 처리)
- [x] **S7 — 회귀**: ✅전부 그린. A)AST 4파일 B)create_app 94라우트+nightrunner 3라우트 등록
  C)NightRunner 로직 회귀(S3순서/S4안전/S5타이밍) D)기존 무결성(DSO 124·toolkit) E)실스택
  uvicorn /api/status에 night_runner 키+기존 전부(orchestrator/autoflat/capture/forge/cooler/safety)
  보존, GET /api/nightrunner/status 200 idle. **S1~S7 완료 — Night Runner 동작.**
- [ ] (스트레치, S1~S7 전부 그린일 때만) 스케줄러 잔여: **월출몰 시각**(astropy로 달 set/rise) ·
  **측광 merit 프로파일**(단주기=이벤트 연속/장주기=빈틈 1점) 중 하나. 새 파일은 안 만들고
  toolkit `_night_plan`에 모듈식 추가. 각각 별도 커밋.

## 4. 검증 게이트 (매 단계 필수)
- SIM 모드로 직접 스크립트/TestClient 검증(이 레포의 `data/asterion.db`는 **`asterion/data/`** 경로 —
  `Path(asterion.__file__).parent/'data'/'asterion.db'`. 루트 `./data` 아님).
- 검증은 **가짜 Meridian/주입**으로 DB 오염 없이 하거나, 시드한 테스트 plan은 끝나고 정리.
- 프런트 변화 있으면 preview_*로 확인(스크린샷은 폰트CDN으로 타임아웃 가능 → preview_eval로 검증).
- 콘솔/서버 에러 0 확인.

## 5. 가드레일 (어기지 말 것)
1. **SIM 전용** — 실하드웨어/실돔/실셔터 절대 조작·연결 시도 금지. 드라이버는 sim만.
2. **매 작동 증분마다 커밋** — main/현재 브랜치를 깨진 상태로 두지 않는다. 커밋 메시지 한국어,
   끝에 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
3. **기존 동작 보존** — 회귀(S7) 깨지면 그 단계 롤백. 새 기능은 additive.
4. **`config.local.json` 절대 열람/수정/커밋 금지** (gitignored, API키 있음). 키를 echo/type 금지.
5. **아키텍처 따르기** — ActionBus 경유 실행, ontology 적재, `operation/` 패키지, fail-closed 게이트 소비.
6. **막히면 멈춤** — 불확실하면 추측으로 밀어붙이지 말고 결정로그에 적고 멈춰 사용자 판단 남긴다.
7. 범위는 **Night Runner(S1~S7)**. 끝나기 전 스트레치로 새지 말 것.

## 6. 결정 로그 (루프가 추가)
- `2026-06-20 S1 — NightRunner 스켈레톤. Orchestrator 패턴 미러(_task/_stop/_state, status_dict/running/
  request_stop/wait). status는 sampler.night_runner_status 콜백→스냅샷 "night_runner" 키(orchestrator와
  동형). REST는 S6로 미룸(S1 범위=status 노출만). 교차배제: start()가 self.running()+orch.running() 체크.
  검증: 프리뷰 매니저가 포트 바인딩 실패(코드무관, create_app() 91라우트 정상) → uvicorn 직접 8533 기동해
  /api/status.night_runner 확인. 이후 단계도 서버 필요시 uvicorn 직접 또는 FakeMer 스크립트로 검증.`
- `2026-06-20 S2 — _build_queue: slot_start 야간분 정렬, respect_slots면 slot_end 경과 skip.
  버그수정: night_runner.py에 'from . import meridian as M' 누락(NameError)→추가. 알려진 한계:
  _now_night_min이 한낮(h<12 +24h wrap)엔 저녁슬롯을 과거로 오탐 → S5에서 슬롯을 '다가오는 밤'의
  실제 datetime에 앵커링해 해결. respect_slots=False(즉시·테스트)는 이 경로 안 탐.`
- `2026-06-20 S3 — _loop 큐 순회 + _run_one(start_plan→wait→get_plan 상태 분류). 개별 실패는
  _run_one에서 흡수(밤 안 멈춤), _stop은 루프 top에서 체크. 검증 경계: NightRunner 책임=시퀀싱이라
  FakeOrch로 결정론 검증(순서/실패continue/정지/교차배제), 실드라이버 풀스택 e2e는 S7로. 프로젝트에
  테스트 디렉터리 없음(Ph7도 애드혹) → FakeMer/FakeOrch 인라인 스크립트 검증 패턴 유지.`
- `2026-06-20 S4 — _safety_hold(슬롯 진입 전). orchestrator.SAFE_TO_OBSERVE/watchtower.safety 재사용
  (상태 동형). Orchestrator의 fail-closed는 '실패'지만 NightRunner는 '보류→회복/스킵'으로 밤을 잇는다
  (전이 weather가 계획을 잃지 않게). poll=min(2.0,timeout), held/reason 상태 노출. config
  nightrunner.hold_skip_seconds(기본1800). 검증: 주입 safety_fn으로 hold/skip/proceed 3케이스.`
- `2026-06-20 S5 — _slot_dt 앵커링: {T@어제,오늘,내일} 중 now-6h(grace) 이상 가장 이른 것 선택 →
  한낮 실행도 저녁슬롯을 오늘로 올바르게(S2 wrap 완전 해결). _await_slot이 slot_start까지 대기(poll=
  cfg nightrunner.poll_seconds 기본5)·slot_end 경과 skip. _build_queue는 정렬 전용으로(night-min skip
  제거). _now_kst 메서드로 분리해 테스트 클럭 주입 가능. _now_night_min 제거(미사용).`
- `2026-06-20 S6 — build_operation_router에 night_runner 파라미터+3라우트(start/stop/status),
  app.py include_router(meridian,orch,night_runner). NightRunStartReq(plan_ids,respect_slots 기본).
  start 응답은 {started:True}만(큐는 status로 폴링 — 백그라운드 task라 즉시 빌드 보장 안 됨).
  검증: TestClient+FakeNR로 위임/503. 교차배제 409는 app 전역 ActionError 핸들러(기존) 재사용.`
- `2026-06-20 S7 — 회귀 전부 그린. 코드(AST/create_app 94라우트/NightRunner 로직/기존 무결성) +
  실스택(uvicorn /api/status에 night_runner 키+기존 보존, nightrunner 라우트 200). 주의: /api/status는
  sampler 첫 틱 전엔 {"mode":"starting"} 폴백 → 'safety' 키 뜰 때까지 폴링해 확인. **S1~S7 종료.**
  스트레치(월출몰/측광 merit)는 가드레일 #7(범위 외)+자율루프 원칙(새 스코프 무단 착수 금지)으로 미착수 —
  사용자에게 넘김.`
