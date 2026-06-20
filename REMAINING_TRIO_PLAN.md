# 잔여 3종 PLAN — Sentinel FWHM · 기상 안전연동 · 픽셀 뷰어 UI (자율 빌드 계약서)

> 사용자 "다"(1,2,3 모두). `/goal`/직접 지시 — 단계별 SIM 검증 후 커밋. 막히면 깨끗이 두고 로그.
> 검증 친화 순서: W1 Sentinel(백엔드·합성검증) → W2 안전연동(백엔드) → W3 픽셀UI(프론트).

## 0. 목표
1. **Sentinel FWHM·별 개수**(원래 #3): 현재 placeholder(None) → 실제 별 검출(임계 위 피크)·개수·평균 FWHM.
   품질판정이 진짜 의미 가짐. 측광 centroid/조리개 코드와 직결.
2. **원격 기상 → 안전 게이트 연동**(원래 #1): C의 ingestion으로 받은 원격 weather가 Watchtower/safety의
   현재값·신선도에 반영(fail-closed 유지). C에서 남긴 마지막 조각.
3. **픽셀 뷰어 UI**(원래 #2): framedata 백엔드(stats/histogram/profile, /api/analysis/frames/*)는 완성 →
   ANALYSIS 탭에 프레임 선택→히스토그램·라인프로파일·통계 패널.

## 1. 현재 상태
- `analysis/sentinel.py` Sentinel.evaluate → accepted/warning/rejected + metrics(FWHM/star_count=None placeholder).
- `analysis/framedata.py`: stats/histogram/profile + photometry(centroid). `/api/analysis/frames/*` 라우트.
- `watchtower/status.py` StatusSampler: weather를 davis 드라이버에서 읽어 snapshot + _last_weather_ok 신선도.
  `watchtower/ingest.py`(C): WeatherRecord 적재 + latest_per_source. `safety.evaluate`(fail-closed).
- ANALYSIS 탭(plots/frames/actions/forge), PANEL_DEF/PROTO 등록 패턴. [[asterion-weather-ingestion]]

## 2. 설계
- **W1**: framedata에 `detect_stars(data, thresh_sigma)` (배경+노이즈 추정→임계 위 연결성분/로컬맥스 별)
  + 각 별 FWHM(2D 가우시안 근사 또는 반치폭). Sentinel.evaluate가 호출해 star_count·median FWHM 채움.
- **W2**: ingest에 `current_weather(db, max_age_s)`(가장 최신 원격 record가 신선하면 표준 dict 반환).
  StatusSampler에 선택적 ingestion 소스 — 드라이버 weather 없거나 config로 ingestion 우선 시 사용.
  fail-closed: 원격도 stale면 unsafe. (config `[weather].source=local|ingest`)
- **W3**: ANALYSIS 탭 '프레임 뷰어' 패널 — 최근 프레임 선택 → /api/analysis/frames/{id}/histogram·profile·stats →
  히스토그램 막대 + 라인프로파일 곡선 + 통계. hidpi 캔버스.

## 3. 체크리스트
- [x] **W1 — Sentinel FWHM·별 개수**: framedata.detect_stars(강건 배경→임계→3×3 로컬맥스 별 + 반치폭
  면적 FWHM, 순수 numpy) + Sentinel.evaluate가 LIGHT에 반영. ✅검증: 합성 별8 정확·FWHM 5.17(이론 5.18)·
  빈프레임 0·Sentinel placeholder 채움.
- [x] **W2 — 기상 안전연동**: ingest.current_weather(최신 원격이 max_age 내 신선하면 dict+age, 아니면 None)
  + StatusSampler weather_ingest_fn(로컬 기상장치 없을 때만 폴백, 신선도=원격 age 반영, stale/none→
  weather_data None→fail-closed) + app.py 배선(config weather.ingest_fallback 기본 on). ✅검증: current_weather
  신선/stale/없음/잘못된시각, 스냅샷 정상빌드(로컬 경로 보존·fail-closed 유지).
- [ ] **W3 — 픽셀 뷰어 UI**: ANALYSIS 패널 + 히스토그램/프로파일/통계 렌더. 검증: 데이터경로+node-check(+가능시 라이브).
- [ ] **W4 — 풀리뷰 + 회귀**: 리뷰 + create_app/SIM 그린.

## 4~5. 게이트·가드레일
SIM/합성/임시DB. DB 경로 asterion/data. 기존 보존(safety는 fail-closed 유지·추가형). config.local.json 금지.
매 증분 커밋(+Co-Authored-By). 레이어. 막히면 멈춤.

## 6. 결정 로그
-
