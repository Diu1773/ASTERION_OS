# A. 차별화 — 피드백 학습 + 대화 제어 PLAN (자율 빌드 계약서)

> **상태: ✅ A1~A4 완료(2026-06-20, /goal 자율빌드).** 대화 제어(챗→Night Runner 도구) +
> 피드백 학습(결과→추천→다음 계획 노출 자동 조정, 루프 닫힘) + 풀리뷰(조회 무적재 수정).
> 라이브 UI 검증 성공. 남은 로드맵: B(스케줄러 잔여)/C(분산 Weather)/D(Forge 정밀).

> 로드맵 옵션 A. 경쟁 빈틈([[asterion-competitive-landscape]]): 기본기는 표준이고 빈틈이
> **대화 제어 + 피드백 학습**. `/goal` 자율 세션 체크리스트 — 한 단계씩 SIM 검증 후 커밋.

## 0. 목표
1. **대화 제어**: 챗으로 무인 야간 운영(Night Runner)을 제어 — "오늘 밤 자동으로 돌려",
   "야간 운영 멈춰", "지금 어디까지 돌았어". 계획만 짜던 AI가 이제 *실행·중단·조회*까지.
2. **피드백 학습**: 관측 결과(품질 verdict·측광 SNR·불량률)를 읽어 **다음 계획에 반영할 추천**을
   낸다 — "이 대상 LIGHT 30% 불량 → 노출↓", "SNR 낮음 → 적분↑". 챗·dossier로 노출.

## 1. 현재 상태 (있는 것)
- **Night Runner**: `operation/night_runner.py`(start/request_stop/status_dict, /api/nightrunner). [[asterion-nightrunner-build]]
- **AI 도구계층**: `agent/toolkit.py` — `specs`(fn() 도구정의) + `_t_<name>` 핸들러 디스패치. 이미
  plan_night/set_goal/run_plan 등. ToolKit.__init__(cfg,snapshot_fn,meridian,orchestrator,bus,drivers,db,sentinel).
- **결과 데이터**: `core/skygraph.py` dossier(프레임·품질·불량률), `framedata.photometry`(SNR), Sentinel verdict,
  ontology `Feedback`/`Decision` 테이블(이미 존재).

## 2. 설계
- **A1 대화 제어**: ToolKit에 `night_runner` 주입 + 도구 `run_night`(now 옵션)·`stop_night`·`night_status`.
  NightRunner.start/request_stop/status_dict 래핑. app.py ToolKit 생성에 night_runner 전달.
- **A2 피드백 학습**: `analysis/feedback.py` `target_feedback(db, name)` — dossier+측광에서 불량률·평균SNR·
  필터별 결과 → 규칙기반 추천(노출/적분/필터/재촬영). ontology Feedback에 적재(설명가능). 도구 `target_feedback`
  + dossier에 surface.
- **A3 학습 반영**: plan_night/merit가 feedback을 소비(대상별 권장 노출/제외) — 적응형. (작게: 추천을
  계획 strategy 기본값에 반영)
- **A4 풀리뷰 + 회귀**.

## 3. 체크리스트
- [x] **A1 — 대화 제어 도구**: ToolKit에 night_runner 주입(app.py) + 도구 run_night(now)/stop_night/
  night_status + 핸들러. NightRunner.start가 큐를 동기 구성하게 개선(직후 status/응답에 큐 보임).
  ✅검증: run_night→큐즉시(2)·완료 done2, 교차배제·stop·미연결 사유, NightRunner S3/S5 회귀 무손상,
  create_app 97라우트, 에이전트 16도구.
- [x] **A2 — 피드백 학습**: `analysis/feedback.py` target_feedback(dossier 품질 + 측광 SNR/포화 → 규칙
  추천 + exposure_hint) + Decision(source=feedback) 적재 + 도구 target_feedback + `/api/feedback/{name}` +
  Target Page '학습 피드백' 박스. ✅검증: GOOD→keep/양호, BADX→decrease/포화·재촬영, Decision 2건,
  도구·route, **라이브 UI**(DEMO 변광성 피드백 박스 렌더, 콘솔에러 0).
- [x] **A3 — 학습 반영**: feedback.latest_hint(최신 Decision의 exposure_hint) + adapt_exposure(inc×1.5/
  dec×0.7). _night_plan이 각 계획 생성 시 라벨로 힌트 조회 → 노출 적응 + st2/응답에 feedback_hint.
  ✅검증: latest_hint 정확매칭(M5≠M51), adapt 180/84/120, _night_plan이 decrease 시드 대상 노출 120→84
  전부 적응. **루프 닫힘**(결과→추천→다음 계획).
- [x] **A4 — 풀리뷰 + 회귀**: 정독 리뷰 — 핵심 결함 1건 확정·수정: GET /api/feedback가 조회마다
  Decision 적재(무한 증가) → persist=False(조회 읽기전용), 학습 기록은 도구만. ✅검증: 조회3회 무적재,
  도구 +1. 회귀 create_app 98라우트·17도구·디스패치 무결·NightRunner/스케줄러 무손상.

## 4. 검증 게이트
- SIM 직접 스크립트/TestClient/Fake 주입. DB 경로 `asterion/data/asterion.db`. 임시DB/시드 정리.
- 프리뷰 불안정 → 데이터경로+node-check. 콘솔/서버 에러 0, 기존 동작 보존.

## 5. 가드레일
1. SIM 전용. 2. 매 증분 커밋(+Co-Authored-By). 3. 기존 보존(additive). 4. config.local.json 금지.
5. 레이어(agent→operation/core, analysis→core). 6. 막히면 멈춤+결정로그. 7. 범위 A(A1~A4).
   **실행 도구는 ActionBus/사전조건 그대로 — AI가 안전게이트 우회 금지(추천·제어만, 안전은 NightRunner/Orchestrator가).**

## 6. 결정 로그
- `2026-06-20 A1 — ToolKit(night_runner) 주입 + run_night/stop_night/night_status. NightRunner.start가
  _build_queue를 동기 호출해 큐를 즉시 _state에 노출(기존엔 async 태스크가 만들어 start 직후 비어 보임 —
  /api/nightrunner/start 응답도 개선). _loop는 prebuilt queue 인자 받음. 안전은 그대로 NightRunner의
  _safety_hold가 책임(AI는 시작/정지만, 게이트 우회 없음). 검증: 도구 위임·교차배제·회귀 S3/S5.`
- `2026-06-20 A2 — analysis/feedback.py(규칙기반, 설명가능): dossier 불량률·필터편중 + 측광 평균SNR·
  포화 → 추천 + exposure_hint(decrease/increase/keep). 학습=Decision(source=feedback)에 근거json 적재
  (Feedback 테이블은 사람교정용이라 부적합 → Decision 사용). 도구 target_feedback + /api/feedback/{name}
  + Target Page 박스. **라이브 UI 검증 성공**(이 환경 프리뷰가 이번엔 협조 — DEMO 변광성 라이트커브
  13.8% 페인트 + 피드백 박스 렌더 + 콘솔에러 0). A3가 exposure_hint를 plan_night에 반영 예정.`
