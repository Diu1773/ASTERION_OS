# ASTERION 자율 관측 스케줄러 설계 (Autonomous Scheduler)

> 고수준 목표 한 줄("메시에 채워", "오늘 밤 은하 이미징")을 던지면, OS가
> 사이트·환경·하드웨어·천체 조건 **+ 이미 찍은 것**을 모두 분석해 → **비겹침
> 야간 시간표**(ObservationPlan 시퀀스)를 만들고 → 사용자 승인 → 실행한다.
> 핵심 원칙: **설명 가능**(왜 이 대상·이 순서인지) · **증분**(매 단계가 그 자체로 작동)
> · **정직**(불가능한 건 불가능하다고 말함).

관련: [[AI_AGENT_DIRECTION.md]] · [[PH7_ORCHESTRATOR_PLAN.md]] · plan_night(현재 "대상 선별"
수준, 이 설계의 1단계 씨앗) · PLAN 탭(스케줄 표시 그릇, 구현됨).

---

## 1. 파이프라인 (3단)

```
목표(goal_type) + 사이트 프로파일 + 환경(기상·달·태양) + 상태(Frame DB)
        │
  ① 후보 필터  ─ 하드 제약(관측 불가) 제외
        │
  ② 점수      ─ 목적별 merit + 긴급도(기회비용)
        │
  ③ 시간 배분  ─ dispatch(비겹침 슬롯 시퀀스 + 오버헤드 삽입)
        │
  ObservationPlan[] (draft) → PLAN 탭 → 승인 → Orchestrator 실행(ActionBus 안전게이트)
```

## 2. 모든 요소는 3가지 방식으로만 들어간다 (확장성의 핵심)

| 방식 | 의미 | 예 |
|---|---|---|
| **하드 제약** | binary, 후보에서 제외 | 비·돌풍·먼지, 지평선 아래, 위도상 한계 미달, 달 너무 가까움(광대역), 흐림 |
| **소프트 페널티** | 점수 가감 | 고도 낮음(airmass↑), 달이격 줄음, 보틀 높음, 투명도 낮음 |
| **오버헤드** | 시간 비용 | 자오선 플립, 재초점, 필터 교체, 트와일라잇 플랫 |

→ 새 요소 추가 = **모듈 하나**. "설계는 전부 수용, 구현은 핵심부터"가 동시에 됨.

## 3. 사이트 프로파일 (site별 1회 설정 — 청람천문대 기준으로 실제 값만)

```jsonc
site_profile = {
  lat, lon, elevation_m,
  bortle,                       // 광공해 등급 (광대역 한계·노출에 영향)
  horizon_mask: [{az, min_alt}],// 방위별 가림(산·건물) — 고도 0°↑여도 막힘
  dome_type: "open"|"dome"|"rolloff",
  limits: { wind_ms, humidity_pct, dewpoint_margin_c, alt_min_deg },  // 하드 제약 임계
  filters: ["L","R","G","B","Ha","OIII","SII"],
  flip_meridian: true, flip_limit_deg,
}
```
- **위도** → 영영 안 뜨는 대상 결정(정직성 §8). **dome_type** → 개방형이면 풍속 한계↓·결로 민감.
- ASTERION엔 이미 **site 차원**(ontology) 존재 → 여기에 프로파일 필드 추가.

## 4. 목적 프로파일 (goal_type → merit) — 점수는 보편이 아니라 목적별

| goal_type | 점수가 키우는 것 | 하드 제약 / 특이 |
|---|---|---|
| `imaging_broadband` (은하·LRGB) | 고도·**총적분**·다크 | **달이격 큼 필수**, 보틀 민감 |
| `imaging_narrowband` (성운·Ha/OIII) | 고도·총적분 | **달에 관대**(보름 OK), 광공해 덜 탐 |
| `photometry_short` (식쌍성·RR Lyrae·δSct) | **이벤트(식/극대) 전구간 연속** | 시간임계 — 고정 블록 먼저, 못 끊음 |
| `photometry_long` (미라류) | 최근 미관측일↑ | 타이밍 자유, **빈틈 채우는 저우선** |
| `campaign_completion` (메시에·콜드웰·커스텀) | **미촬영 + 긴급도** | 위도 불가 대상 명시 제외(§8) |

각 프로파일 = `{terms 가중치, hard_constraints, exposure/cadence, filter_set}`.
`UserGoal.goal_type`이 이 프로파일을 선택하는 **seam**(이미 온톨로지에 있음).

## 5. 상태(state) — "OS가 자기가 뭘 찍었는지 안다"

자율 OS의 핵심 원칙. **Frame / ObservationSession DB**에서:
- 대상별 **촬영 여부**(캠페인 완성용) · **누적 적분시간**(목표 SNR 도달까지) · **마지막 관측일**(측광 cadence).
- → "메시에 채워" 하면 사용자가 목록 관리 안 해도 **안 찍은 것만** 자동 후보.

## 6. merit(점수) 일반형

```
score(target) = Σ wᵢ · termᵢ        # wᵢ는 goal 프로파일이 정함
```
공통 term: `고도(airmass)` · `관측창 길이` · `달이격` · `투명도` · `우선순위` ·
**`긴급도(opportunity_cost)`** · `미촬영/cadence`.

- **긴급도** = "놓치면 손해"의 정량화: 최고고도 낮음 · **계절상 곧 짐** · 관측창 짧음 → ↑.
  (즉 "고도 높은 순"이 아니라 **"지금 아니면 못 잡는 순"** 으로 정렬이 뒤집힘 — 사용자 핵심 통찰.)

## 7. 시간 배분 (dispatch scheduler)

1. **시간임계 먼저** — 단주기 측광/엄폐/식 = 그 시각에 고정 블록 박음.
2. **나머지 greedy** — 각 시점에서 현재 최고점수 대상을 **필요 노출만큼** 배치, **비겹침**,
   자오선(최고고도) 근처 선호. (한 대상 슬롯 끝 → 다음 대상 곡선이 이어짐 = 스케줄 시퀀스)
3. **오버헤드 삽입** — 플립·재초점·필터교체·플랫 시간 끼움.
4. **안전 마진** — 박명 시작 전·기상전선 전 종료. (DomeGuard와 연동)

출력 = 비겹침 슬롯 시퀀스 → PLAN 탭 마스터 타임라인(슬롯별 곡선 핸드오프) + 순서표.

## 8. 정직성 / 설명가능 (자율의 신뢰)

- **위도상 불가** 대상 명시: "M6·M7은 청람에서 최고 ~19°라 30° 한계 못 넘음 — 제외/한계조정?"
- **불가능한 목표**: "메시에 전부"는 위도상 N개 불가 → 솔직히 알림.
- 각 배치에 **근거**(점수 기여 항) 부착 → `Decision` 테이블에 기록(왜 이 순서인지 설명).

## 9. 온톨로지 매핑 (이미 있는 것 위에)

| 개념 | 테이블/필드 |
|---|---|
| 목적 | `UserGoal.goal_type` · `required_filters` · `quality_thresholds_json` · `priority` |
| 대상 | `Target`(+ 백엔드 DSO 카탈로그 확장: 현재 ~30 → 메시에110·콜드웰) |
| 상태 | `Frame` · `ObservationSession`(촬영여부·누적적분·마지막관측) |
| 사이트 | `site` 차원 + site_profile 필드(§3) |
| 환경 | `WeatherRecord`(실측) + 예보 API(추가 필요) + astropy(달·태양) |
| 출력 | `ObservationPlan`(draft 시퀀스) |
| 근거 | `Decision`(점수·사유 로그) |

## 10. 증분 로드맵 (매 단계 작동 — 좌초 방지)

- **Phase 1 (vital few)** — `imaging_broadband` 1프로파일 + 하드(고도·다크·기상안전) +
  점수(고도·관측창·달이격) + greedy 시간배분 → 비겹침 시퀀스 UI. *이거면 이미 쓸만.*
- **Phase 2** — `campaign_completion` + Frame 상태(미촬영) + **긴급도**(곧 지는 것 먼저). ← "메시에 채워" 데모
- **Phase 3** — 달(astropy 정밀 이격·월출몰·위상) + `imaging_narrowband`(달 관대) 필터 로직
- **Phase 4** — 기상 **예보** 게이팅(맑은 창만)
- **Phase 5** — 보틀/밝기 상호작용 + 측광 프로파일(`photometry_short/long`)
- **Phase 6** — 멀티나잇 캠페인(누적 적분 이월) + 전역 최적화

## 11. 확보 필요한 데이터/입력

- 달·태양: **astropy(있음)** · 지평선 마스크/보틀/돔형태: **site 설정 입력** ·
  기상 **예보 API**(현재 WeatherRecord는 실측만) · **카탈로그 확장**(메시에 110·콜드웰).

---

### 즉 다음 실행 = Phase 1
`site_profile`(청람천문대) 정의 → `imaging_broadband` 프로파일 → 하드3(고도·다크·기상) +
점수3(고도·관측창·달이격) + greedy dispatch → PLAN 탭 스케줄 시퀀스 표시. 돌아가면 Phase 2(캠페인)로.
