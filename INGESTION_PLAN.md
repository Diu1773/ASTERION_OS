# C. 분산 Weather / Ingestion API PLAN (자율 빌드 계약서)

> **상태: ✅ C1~C3 완료(2026-06-20, /goal 자율빌드).** Ingestion API + 스트림 재정렬(중복제거) +
> 소스별 최신 + 풀리뷰(백필 N+1→단일쿼리). 안전 게이트 연동은 범위 밖(사용자 셋업 맥락 필요).
> 남은 로드맵: D(Forge 정밀/AstralImage — 별도 레포 필요).

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
- [x] **C1 — Ingestion + 재정렬**: WeatherRecord.source_id(자동 마이그레이션) + `watchtower/ingest.py`
  ingest_records(§7 매핑·검증·(source,utc) 중복제거 배치내+DB) + `POST /api/weather/ingest`(단일/배열).
  ✅검증: 단일 accepted1 / 배열 accepted1·중복2·거부1 / 매핑(humidity_percent→humidity 등).
- [x] **C2 — 소스별 최신**: latest_per_source(source별 max id) + `GET /api/weather/sources`.
  ✅검증: 2소스(pc01/pc02) 소스별 최신. create_app 100라우트.
- [x] **C3 — 풀리뷰 + 회귀**: 정독 리뷰 — 성능결함 1건 확정·수정: _existing가 배치 쌍마다 first()
  (N+1, §7.4 대량 백필서 치명) → 단일 IN 쿼리. ✅검증: 중복제거 동일, 백필 1000건(500중복+500신규)
  accepted500·255ms(단일쿼리), create_app 100라우트. 남은 저위험: rain 문자열 'false'→True(§7은 bool),
  utc TZ혼재(쿼리는 id정렬이라 무관).

## 결정 로그(추가)
- `2026-06-20 C1·C2 — WeatherRecord.source_id(자동마이그). ingest_records(§7 매핑·(source,utc) 중복제거
  배치내+DB)·latest_per_source(source별 max id). POST /ingest, GET /sources. 추가형(davis/safety 무수정).`
- `2026-06-20 C3 — _existing N+1(쌍마다 first) → 단일 IN 쿼리(소스·시각). IN 교차곱 초과는 배치 키로만
  교집합 판정해 무해. 백필 1000건 단일쿼리. 안전 게이트 연동(원격 weather→Watchtower)은 사용자 셋업
  맥락 필요해 범위 밖으로 남김.`

## 4. 검증 게이트
SIM/TestClient/임시DB. DB 경로 asterion/data. 기존 weather 흐름·safety 보존(추가형). 콘솔/서버 에러 0.

## 5. 가드레일
1. SIM 전용. 2. 매 증분 커밋(+Co-Authored-By). 3. 기존 보존(추가형, davis/sampler/safety 무수정).
4. config.local.json 금지. 5. 레이어. 6. 막히면 멈춤+로그. 7. 범위 C(수신·저장·조회 / 안전연동 제외).

## 6. 결정 로그
-
