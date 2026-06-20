# B. 스케줄러 잔여 — 측광 merit + 월출몰 PLAN (자율 빌드 계약서)

> **상태: ✅ B1~B3 완료(2026-06-20, /goal 자율빌드).** 측광 merit(단주기 연속/장주기 빈틈) +
> 월출몰 시각(벡터화 11×) + 풀리뷰. 남은 로드맵: C(분산 Weather)/D(Forge 정밀).

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
- [x] **B1 — 측광 merit**: 프로파일 config{photometry_short:(w_win25,dwell window), photometry_long:
  (w_win2,quick), imaging:(6,exposure)}. merit win가중 + dur_for(c) 프로파일별. _t_plan_night이 goal_type
  photometry_*→profile. ✅검증: short 1대상 3.15h 연속 / long 6대상 각 0.2h / imaging 현행 0.5h.
- [x] **B2 — 월출몰 시각**: `_moon_riseset(loc,now,hours,n=24)` — 밤 샘플링→고도 0° 교차 선형보간→
  월출/월몰(KST). moon_sum에 rise/set 추가(plan_night 응답에 노출). ✅검증: 월출 11:52·월몰 23:44,
  독립 5분 샘플링과 ±4분 일치.
- [x] **B3 — 풀리뷰 + 회귀**: 정독 리뷰 — 성능결함 1건 확정·수정: _moon_riseset가 astropy 24회 루프
  호출(1224ms) → Time 배열 벡터화(1회 ephemeris)로 **107ms(11×)**, 결과 동일. 회귀 create_app 98라우트·
  imaging dwell 현행·캠페인 경로·moon키 완전. 남은 저위험: hm 중복(_moon_riseset/dispatch).

## 결정 로그(추가)
- `2026-06-20 B1 — _night_plan 측광 프로파일 dict{w_win,dwell}. short=관측창 연속(min(win,4h)),
  long=quick(노출/3600). merit win가중·dur_for(c) 프로파일화. _t_plan_night profile에 photometry_* 추가.`
- `2026-06-20 B2 — _moon_riseset(고도 0° 교차 선형보간). moon_sum rise/set. 독립검증 ±4분.`
- `2026-06-20 B3 — 리뷰: _moon_riseset를 get_body(Time배열) 벡터화 1224→107ms(11×), 결과불변.
  슬롯 1.5h는 4필터 base_dur(정상). 기본 imaging 현행 유지 확인.`

## 4. 검증 게이트
SIM 스크립트/Fake. DB 경로 asterion/data. 프리뷰 불안정→데이터경로. 콘솔/서버 에러 0, 기존 보존.

## 5. 가드레일
1. SIM 전용. 2. 매 증분 커밋(+Co-Authored-By). 3. 기존 보존(additive·기본 imaging 현행 유지).
4. config.local.json 금지. 5. 레이어. 6. 막히면 멈춤+로그. 7. 범위 B(B1~B3).

## 6. 결정 로그
-
