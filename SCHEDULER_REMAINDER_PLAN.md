# B. 스케줄러 잔여 — 측광 merit + 월출몰 PLAN (자율 빌드 계약서)

> 로드맵 옵션 B. 방금 만든 라이트커브(변광성 측광)와 직결. `/goal` 자율 세션 체크리스트.

## 0. 목표
1. **측광 merit 프로파일**: 단주기(식쌍성·RR Lyrae·δSct=photometry_short)는 *이벤트 전 구간 연속*,
   장주기(미라류=photometry_long)는 *빈틈 1방문*. 목적별 merit/슬롯배분.
2. **월출몰 시각**: 달이 밤 중 언제 뜨고 지는지 계산 → 달밝힘을 관측창 동안으로 정밀화 + 노출.

## 1. 현재 상태 (있는 것)
- `toolkit._night_plan(profile=None)`: merit=`peak + win_len*6 + moon*w_moon*m_bright`, dur=노출기반 고정,
  campaign은 긴급도 정렬. 프로파일 imaging_broadband/narrowband만(Phase3). `_t_plan_night`이 goal_type→profile.
- `_moon_metrics(t,loc)`: 위상·고도·밝힘(중간시각 1점). set_goal goal_type에 photometry_short/long 정의만(미사용).
- astropy(get_body/AltAz). [[asterion-scheduler-design]] [[asterion-photometry-forge-ui]]

## 2. 설계
- **B1 측광 merit**: 프로파일 config — photometry_short{w_win 25, dwell 'window'(관측창 전체 연속, 최대 4h)},
  photometry_long{w_win 2, dwell 'quick'(빠른 1방문)}, imaging{w_win 6, dwell 'exposure'(현재)}.
  merit의 win 가중·슬롯 dur을 프로파일로. _t_plan_night이 goal_type photometry_*→profile.
- **B2 월출몰**: `_moon_riseset(loc, now, hours)` — 밤 구간을 샘플링해 달 고도 부호변화로 rise/set 시각.
  moon_sum에 rise/set 추가. (밝힘 정밀화는 작게: rise/set 노출만, 가중은 Phase3 유지.)
- **B3 풀리뷰 + 회귀**.

## 3. 체크리스트
- [ ] **B1 — 측광 merit**: 프로파일별 w_win + dwell(dur). 검증: photometry_short는 긴 관측창 대상 상위·
  슬롯이 관측창만큼 길게 / photometry_long은 짧은 슬롯·여러 대상 / imaging 현행 유지. 회귀.
- [ ] **B2 — 월출몰 시각**: _moon_riseset + moon_sum 노출. 검증: 합성/실제로 rise<set 또는 부호변화 시각 타당.
- [ ] **B3 — 풀리뷰 + 회귀**: review-full + create_app/SIM 그린.

## 4. 검증 게이트
SIM 스크립트/Fake. DB 경로 asterion/data. 프리뷰 불안정→데이터경로. 콘솔/서버 에러 0, 기존 보존.

## 5. 가드레일
1. SIM 전용. 2. 매 증분 커밋(+Co-Authored-By). 3. 기존 보존(additive·기본 imaging 현행 유지).
4. config.local.json 금지. 5. 레이어. 6. 막히면 멈춤+로그. 7. 범위 B(B1~B3).

## 6. 결정 로그
-
