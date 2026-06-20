# ASTERION OS — 자율 빌드 세션 요약 (2026-06-20)

> 한 세션에서 로드맵 잔여 단계 + 차별화를 자율 루프(`/goal`·`/loop`)로 구현. 각 작업은
> **계획 파일(계약서) → 단계별 SIM 검증 → 커밋 → 풀리뷰** 패턴. 새 코드는 전부 additive·SIM 전용.

## 한 줄
자율 관측 루프를 **실제로 닫았다**: 목표 한 줄 → 계획 → 무인 실행 → 대상 중심 데이터 →
측광/품질 → 피드백 → 재계획. + 대화 제어, 분산 기상, 픽셀 분석.

---

## 빌드 아크 (시간순)

### 1. 스케줄러 기반 (Phase 1b·3 + 카탈로그)
- **카탈로그 110**(`4ca07ed`): 백엔드 DSO를 `web/catalog.js` 단일소스 로더로 → 메시에 110 전체.
- **Phase 1b**(`deed297`): PLAN 탭 'AI 야간 계획' 패널 — 비겹침 슬롯 시간표(마스터 타임라인 핸드오프 + 순서표 + 승인/실행/FOV).
- **Phase 3**(`5d88715`): 달 정밀(위상·고도·하늘밝힘) + 프로파일별 merit(광대역 민감 / 협대역 관대).

### 2. Night Runner — 무인 야간 운영기 (S1~S7, `7cc6b5b`→`52f3690`)
`operation/night_runner.py` — 승인 시간표를 슬롯 순서로 무인 시퀀싱(슬롯 대기→안전 게이트→
Orchestrator 실행→분류). `/api/nightrunner/*`. 검증: 순서·실패continue·정지·교차배제·슬롯 datetime 앵커링.

### 3. Ph9 Skygraph / Target Page (T1~T4, `febc908`→`dec110c`)
- T1 `ObservationSession.plan_id`(Frame→Target 1급 조인). T2 `core/skygraph.py` dossier + `/api/targets`.
- T3 PLAN 탭 '대상 페이지'(관측이력·프레임·품질·가시성·추천). T4 리뷰(frames 캡·레거시 json 매칭).

### 4. Forge UI + 가벼운 라이트커브 (L1~L4, `6289bcd`→`f8a27fe`)
- L1 `framedata.photometry`(조리개 측광) + `/api/photometry`. L2 ANALYSIS 'Forge 전처리' 카드.
- L3 Target Page 라이트커브 차트. L4 리뷰(측광 윈도우화 대형센서 8×, 336→42ms).

### 5. A 차별화 — 대화 제어 + 피드백 학습 (A1~A4, `4a1ffef`→`f803272`)
- A1 챗으로 Night Runner 제어(`run_night`/`stop_night`/`night_status`).
- A2 `analysis/feedback.py`(결과→추천+노출힌트, Decision 적재) + Target Page '학습 피드백'.
- A3 `plan_night`이 힌트로 노출 적응(**피드백 루프 닫힘**). A4 리뷰(조회 무적재).

### 6. B 스케줄러 잔여 — 측광 merit + 월출몰 (B1~B3, `c7b1692`→`858796a`)
- B1 측광 프로파일(단주기=이벤트 연속 / 장주기=빈틈 1방문). B2 `_moon_riseset`(월출몰).
- B3 리뷰(월출몰 벡터화 11×, 1224→107ms).

### 7. C 분산 Weather / Ingestion (C1~C3, `5643044`→`43f6099`)
- C1·C2 `POST /api/weather/ingest`(§7 원격 PC) + 중복제거 재정렬 + `GET /api/weather/sources`.
- C3 리뷰(백필 N+1→단일쿼리). `watchtower/ingest.py`, 추가형.

### 8. 잔여 3종 — Sentinel FWHM · 기상 안전연동 · 픽셀 뷰어 (W1~W4, `b9ae0db`→`0dcb4ff`)
- W1 `detect_stars`(별 검출+FWHM) → Sentinel placeholder 채움. W2 원격 기상 안전 게이트 폴백(fail-closed).
- W3 ANALYSIS '프레임 뷰어'(히스토그램·프로파일·통계·FWHM, 라이브 검증). W4 리뷰(노이즈 제거 + 5.6×).

---

## 신규 모듈 / 엔드포인트 / 도구
- **모듈**: `operation/night_runner.py`, `core/skygraph.py`, `analysis/feedback.py`, `watchtower/ingest.py`,
  + `analysis/framedata.py`(photometry·light_curve·detect_stars), `agent/toolkit.py`·`analysis/sentinel.py` 확장.
- **온톨로지**: `ObservationSession.plan_id`, `WeatherRecord.source_id`(둘 다 `_sync_columns` 자동 마이그레이션).
- **REST**: `/api/nightrunner/start|stop|status`, `/api/targets`·`/api/targets/{name}`, `/api/photometry/{name}`,
  `/api/feedback/{name}`, `/api/weather/ingest`·`/api/weather/sources`. (create_app 100 라우트)
- **에이전트 도구(17개)**: + `run_night`·`stop_night`·`night_status`·`target_feedback`.
- **UI 패널**: AI 야간 계획·대상 페이지(+라이트커브·피드백)·Forge 전처리·프레임 뷰어.

## 검증
- 전부 SIM/합성 FITS/임시DB/Fake 주입 + TestClient + uvicorn 직접. 측광·FWHM은 합성으로 **정량 일치**
  (flux∝amp, FWHM 5.17 vs 이론 5.18). 라이브 UI는 대상 페이지·프레임 뷰어 렌더 확인(콘솔 에러 0).
- **풀리뷰가 실제 성능 결함 다수 적발·수정**: 측광 8×, 월출몰 11×, 백필 N+1, 별검출 5.6×+노이즈.
- 캐비엇: 실하드웨어 미검증(SIM), 프리뷰 매니저 간헐 불안정(데이터경로로 갈음), 측광=점광원 상대광도.

## 남은 로드맵
- **블로커 없음**: 기상 예보 게이팅 · 보틀/사이트 프로파일 · 멀티나잇 캠페인 · Provenance 완성 · Alert.
- **입력 필요**: Access/권한 · Plugin Runtime · 실HW(청람 수동셔터) · 음성(STT/TTS).
- **별도 레포**: Forge 정밀(정렬·스택)=AstralImage.

> 메모리: [[asterion-scheduler-design]] [[asterion-nightrunner-build]] [[asterion-skygraph-target]]
> [[asterion-photometry-forge-ui]] [[asterion-differentiation]] [[asterion-weather-ingestion]]
