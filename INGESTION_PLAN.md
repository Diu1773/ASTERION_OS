# C. 분산 Weather / Ingestion API PLAN (자율 빌드 계약서)

> 로드맵 §7. **중앙 수신부만** — 원격 PC의 Weather Agent(사용자 기존 자산)가 POST할 엔드포인트 +
> 스트림 재정렬 + Weather Store. 기존 로컬 davis/sampler·safety 흐름은 안 건드림(추가형).

## 0. 목표
원격 PC들이 표준 JSON으로 기상을 중앙에 올리고(Ingestion API), 중복/순서역전을 정리(Stream Reorder)해
WeatherRecord(Weather Store)에 적재. 소스별 최신 조회. **안전 게이트 연동(현재 weather 대체)은 범위 밖**
(사용자 셋업 맥락 필요 — C에서는 수신·저장·조회까지, 노트로 남김).

## 1. 현재 상태
- `WeatherRecord`(utc·temp_c·humidity·dew_point_c·wind_ms·wind_dir_deg·cloud_score·rain·safe·site) —
  **source_id 없음**. `GET /api/weather/history`(db.recent). 로컬 weather는 davis 드라이버→sampler→
  WeatherRecord. _sync_columns 자동 마이그레이션. [[asterion-roadmap-status]]
- §7 JSON: {source_id,sensor_id,timestamp(ISO+TZ),temperature_c,humidity_percent,wind_speed_ms,rain,cloud_index}.

## 2. 설계
- **C1 Ingestion + 재정렬**: WeatherRecord에 `source_id` 추가. `watchtower/ingest.py` ingest_records(db,payload)
  — §7 필드 매핑(humidity_percent→humidity, wind_speed_ms→wind_ms, cloud_index→cloud_score, timestamp→utc),
  검증, **중복제거**(같은 source_id+utc 스킵), 적재. `POST /api/weather/ingest`(단일/배열). 반환
  {accepted,duplicates,rejected}.
- **C2 소스별 최신**: latest_per_source(db) — source_id별 최신 1건. `GET /api/weather/sources`.
- **C3 풀리뷰 + 회귀**.

## 3. 체크리스트
- [ ] **C1 — Ingestion + 재정렬**: WeatherRecord.source_id + ingest_records(매핑·검증·중복제거) + POST route.
  검증: 단일/배열 적재, 중복 timestamp 스킵, 잘못된 payload 거부, 자동 마이그레이션, history 회귀.
- [ ] **C2 — 소스별 최신**: latest_per_source + GET /api/weather/sources. 검증: 다중 소스 → 소스별 최신.
- [ ] **C3 — 풀리뷰 + 회귀**: 리뷰 + create_app/SIM 그린.

## 4. 검증 게이트
SIM/TestClient/임시DB. DB 경로 asterion/data. 기존 weather 흐름·safety 보존(추가형). 콘솔/서버 에러 0.

## 5. 가드레일
1. SIM 전용. 2. 매 증분 커밋(+Co-Authored-By). 3. 기존 보존(추가형, davis/sampler/safety 무수정).
4. config.local.json 금지. 5. 레이어. 6. 막히면 멈춤+로그. 7. 범위 C(수신·저장·조회 / 안전연동 제외).

## 6. 결정 로그
-
