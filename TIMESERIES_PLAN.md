# 시계열 품질 뷰어 — PP(보정) 적용 (계약서)

설계 워크플로(3아키텍트+심사) 합성. **채택: calibrate-on-read + metric 영속 + graceful degradation.**
핵심: 픽셀/히스토그램 표시는 외부도구(MaxIm/NINA/SharpCap) 몫 → 제거. 패널은 **PP된 프레임에서 잰
시계열**(백그라운드·FWHM·라이트커브)에 집중. raw에서 재면 다크전류·바이어스·플랫이 섞여 과학적 무효.

## 측정 시점 = 캡처 직후 메모리
orchestrator가 img를 RAM에 든 그 자리에서 Forge 보정배열로 측정 → QualityMetric 영속.
**보정본 FITS 미저장(2x디스크 X)·재측정 X·재읽기 X.** 매칭 마스터 없으면 raw로 재되 `calibrated=false` 태깅.

## 단계 (각 SIM 게이트 통과 후 커밋)
- [ ] **S1** QualityMetric += fwhm(Float)·star_count(Integer)·background_adu(Float)·calibrated(Bool nullable
  default false)·cal_sources_json(Text '{}'). Frame 무변경. ✅PRAGMA에 5컬럼·기존행 NULL·sentinel 예외없음.
- [ ] **S2** `Forge.measure_calibrated(img, meta)→(cal, {calibrated,sources,warnings,applied,reason})`.
  enabled+LIGHT면 masters_for+calibrate_array, 아니면 (img,false,reason). 순수(process와 분리). ✅합성491+빈마스터
  → cal≈img·calibrated=False·reason='forge_disabled'/'no_master'.
- [ ] **S3** framedata detect_stars/photometry에 `arr=None` 옵션(주면 _load 대신 그 배열). background_adu 반환
  포함. 하위호환(기본 None=현행). ✅arr 미전달 동작불변 + arr=합성 동일 산출.
- [ ] **S4** orchestrator `_expose_filter`: 캡처→measure_calibrated→detect_stars(arr=cal)→QualityMetric.add에
  fwhm/star_count/background_adu/calibrated/cal_sources_json. **forge는 callable lazy 주입**(생성순서 무관, 미주입시
  raw 폴백). ✅SIM 캡처1 → QM행에 fwhm/bg 채워지고 calibrated=False·reason='forge_disabled', 기존 시퀀스 보존.
- [ ] **S5** GET `/api/timeseries?target=&session_id=&night=&filter=&metric=&show_raw=` — QM⨝Frame 집계,
  calibrated=true 기본(show_raw=true면 전체). ✅200·영속행 반환·calibrated 필드·빈대상 빈배열.
- [ ] **S6** 프론트 pixview→qualview: 필터바(대상·세션/밤·필터·calibrated/raw 토글·↻)+provenance줄+3캔버스
  (background·fwhm·라이트커브). drawTimeSeries 헬퍼(drawChart 일반화). 픽셀/히스토그램/프로파일 제거.
  PANEL_DEF/그리드 pixview 좌표 유지. ✅분석탭 3차트 렌더·픽셀흔적 없음·콘솔0·데이터없으면 안내.
- [ ] **S7** light_curve는 우선 raw 유지(mag 영속 컬럼 없음)+calibrated 태그 노출. sentinel.evaluate가 영속
  fwhm/star_count 있으면 재계산 회피. 전체 SIM 회귀. ✅/api/photometry·/api/sentinel 형식 불변·캡처/안전 무손상.

## 가드레일
- 추가형(캡처/안전/orchestrator/sentinel 기존 동작 보존). 신규 컬럼 nullable. config.local.json 금지. SIM 검증.
- **노출 미스매치 다크 무음 적용 절대 금지**(180s에 0.2s다크=100x 오차) — 경고+calibrated=false 강등 의무.
- 프론트 NULL=raw 일관 처리(IS NULL OR =0). calibrated=true만 'PP 시리즈', raw는 토글로만.

## 후속(이번 범위 밖, open risk)
- 다크 온도 정밀매칭('가장 가까운 temp') — 현재 노출/비닝만. mag/flux/snr 영속(라이트커브 PP화). detect_stars
  캡처지연 측정→필요시 to_thread. 실 마스터 확보 후 과학정확성 재검증(SIM 합성은 동작검증만).
