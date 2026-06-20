# SKYGRAPH / TARGET PAGE PLAN — Ph9 (자율 빌드 계약서)

> `/goal` 자율 세션이 따라가는 체크리스트. 한 번에 한 단계, 각 단계 SIM 검증 통과 후 커밋.
> 막히면 깨끗한 상태로 두고 결정로그 기록 후 멈춘다. NIGHT_RUNNER_PLAN.md와 같은 방식.

## 0. 목표 (한 줄)
로드맵 **Ph9 Skygraph/Target Page** — "데이터는 파일이 아니라 Target/Observation 중심"(원칙 #6)의
실현. 한 대상에 대해 **관측요청·프레임·품질·가시성·추천관측**을 한 화면(dossier)으로 묶는다.

## 1. 현재 상태 (있는 것 — 새로 만들지 말 것)
- **온톨로지(=Skygraph 데이터층)**: `core/ontology.py` Target{name,ra_hours,dec_degs,type,magnitude,notes}·
  ObservationPlan{target_id,params_json,approval_status,created_utc}·ObservationSession{kind,summary_json,
  started/ended,status}·Frame{session_id,image_type,filter_name,exposure_s,date_obs_utc,median_adu,flag}·
  QualityMetric{frame_id,verdict,...}. `Db._sync_columns`가 ALTER TABLE ADD COLUMN 자동 마이그레이션.
- **약한 고리**: Frame→Target 직접 링크 없음. 현재는 session.summary_json.target(문자열)로만 연결.
  Plan은 target_id로 Target에 1급 연결됨(meridian.done_target_names가 Target⋈Plan 조인).
- **집계 재료**: meridian(list_plans/get_plan), framedata(`FrameData`, /api/analysis/frames/*),
  스케줄러 가시성 계산(toolkit `_radec_alt`/astropy), catalog(dso_catalog/catalog.js).
- **UI 패턴**: PLAN 탭(Telescopius식 코랄 1색), PANEL_DEF/PROTO_GS_LAYOUT 패널 등록, 가시성 그래프
  (drawTimelineOn), DSS 이미지(hips2fits). [[asterion-frontend-conventions]]

## 2. 설계
- **T1 Skygraph 엣지(1급)**: `ObservationSession`에 `plan_id`(FK, nullable) 추가 → orchestrator가
  세션 생성 시 기록. 그러면 Target→Plan→Session→Frame이 깔끔한 조인(문자열 매칭 제거). 자동 마이그레이션.
- **T2 dossier 백엔드**: `core/skygraph.py` `target_dossier(name)` — target정보 + 관측요청(plans) +
  프레임/품질(plan→session→frame⋈QM) + 가시성(오늘밤 peak/관측창) + 추천(관측가능·다음시각).
  REST `/api/targets`(목록: 카탈로그∪관측된 대상) + `/api/targets/{name}`(dossier).
- **T3 Target Page UI**: PLAN 탭(또는 신규)에 패널 — 개요·가시성그래프·프레임이력표·품질추세·요청목록·
  추천. SkyPortal/Telescopius식. '오늘밤 베스트' 카드 클릭/검색에서 진입.
- **T4 풀리뷰**: review-full 또는 /code-review로 변경분 검토 + 전체 회귀(create_app·기존 키 보존·SIM).

## 3. 체크리스트 (각 단계 = 1커밋, 검증 후 다음)
- [x] **T1 — Skygraph 엣지**: ontology `ObservationSession.plan_id`(FK nullable) + orchestrator가
  `ObservationSession(kind="science", plan_id=pid)`로 기록. ✅검증: 기존 DB에 _sync_columns가 plan_id
  컬럼 자동 ALTER ADD, 저장/조회(4242) OK, create_app 94라우트 회귀 그린.
- [ ] **T2 — dossier 백엔드**: `core/skygraph.py` target_dossier + `/api/targets`·`/api/targets/{name}`.
  검증: 시드(target+plan+session+frame+QM)→API가 관측요청/프레임/품질/가시성/추천 집계 반환.
- [ ] **T3 — Target Page UI**: dossier를 보여주는 패널(개요·가시성·프레임이력·품질·추천). 패널 등록.
  검증: preview_eval로 렌더/데이터 바인딩(스크린샷은 폰트CDN 타임아웃 가능).
- [ ] **T4 — 풀리뷰 + 회귀**: review-full(변경분) + create_app/기존 status 키/SIM e2e 그린.

## 4. 검증 게이트 (매 단계)
- SIM 직접 스크립트(Fake/시드) 또는 uvicorn 직접(프리뷰 매니저 포트 바인딩 불안정 — 8533+ 직접).
- DB 경로 `asterion/data/asterion.db`(루트 ./data 아님). 시드는 테스트 후 정리하거나 가짜 주입.
- 콘솔/서버 에러 0, 기존 동작(status 키·라우트·스케줄러) 보존.

## 5. 가드레일
1. **SIM 전용** — 실하드웨어/실돔 금지.
2. **매 증분 커밋** — 브랜치 안 깨지게. 메시지 한국어 + `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
3. **기존 동작 보존** — 회귀 깨지면 롤백. 새 기능 additive(새 컬럼 nullable).
4. **`config.local.json` 절대 금지**.
5. **아키텍처** — ontology/operation/web 패턴, ActionBus, fail-closed.
6. **막히면 멈춤** — 추측 금지, 결정로그 기록.
7. 범위 = Ph9(T1~T4). 새 스코프로 새지 말 것.

## 6. 결정 로그 (루프가 추가)
- `2026-06-20 T1 — ObservationSession.plan_id(FK observation_plan, nullable) 추가. orchestrator
  세션 생성에 plan_id=pid 기록 → Target→Plan→Session→Frame 1급 조인(문자열 summary 매칭 탈피).
  _sync_columns가 기존 DB에 자동 ALTER ADD COLUMN(검증됨). 기존 세션은 plan_id=NULL(하위호환).
  autoflat 등 다른 세션은 plan_id 없이 생성 — science 세션만 채움. create_app 회귀 그린.`
