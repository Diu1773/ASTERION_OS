# FORGE UI + 가벼운 라이트커브 PLAN (자율 빌드 계약서)

> **상태: ✅ L1~L4 완료(2026-06-20, /goal 자율빌드).** 조리개측광 백엔드(/api/photometry) →
> Forge UI 카드(ANALYSIS) → 라이트커브 차트(Target Page) → 풀리뷰(통합검토 통과 + 성능 8× 개선).
> A/B/C 로드맵 옵션은 이 다음.

> A/B/C 로드맵 옵션보다 **먼저** 마무리. `/goal` 자율 세션이 따라가는 체크리스트 —
> 한 번에 한 단계, 각 단계 SIM 검증 후 커밋. 막히면 깨끗한 상태로 두고 결정로그 기록.

## 0. 목표
사용자가 "어디갔지?" 한 두 기능을 실제로 붙인다:
1. **Forge UI** — 자동 전처리(bias/dark/flat) 백엔드는 완성·작동하나 **UI 얼굴이 없음**. on/off
   토글 + 적용된 마스터·경고 표시 카드를 붙인다.
2. **가벼운 라이트커브** — 측광은 **계획만**(코드 전무). 조리개 측광(aperture photometry) 한 겹 +
   대상별 시간↔등급 시계열 + 차트. 점광원(변광성: 식쌍성·RR Lyrae·미라류)용 경량 버전.

## 1. 현재 상태 (있는 것)
- **Forge 백엔드 완성**: `analysis/forge.py` 실시간 bias/dark/flat(numpy). API: `GET /api/forge/status`,
  `POST /api/forge/toggle{on,save}`, `POST /api/forge/config`. `/api/status.forge`(enabled/sources/
  warnings/pins/last)로 스냅샷 노출. **UI 패널 없음**(web/에 forge 키워드 0건).
- **FITS 읽기 인프라**: `core/fitsio.py`(have_fits/load_frame→numpy), `analysis/framedata.py`
  (FrameData._load(frame_id)→(data,frame,status); stats/histogram/profile).
- **대상→프레임 링크**: `core/skygraph.py`(T1 plan_id 엣지 + 레거시). dossier에 LIGHT 프레임 목록.
- **차트 UI 패턴**: ANALYSIS 탭 "시계열 플롯"(app.js plots/charts) — 텔레메트리용. Target Page dossier
  (PLAN 탭, 방금 구현)에 패널 추가 가능. [[asterion-frontend-conventions]] [[asterion-skygraph-target]]

## 2. 설계
- **측광(L1)**: `FrameData.photometry(frame_id, r_ap, r_in, r_out)` — FITS 로드 → 중앙영역 최대픽셀 →
  강도가중 centroid → 조리개합 − 배경(annulus 중앙값)×면적 = flux → instrumental mag=−2.5·log10(flux)+ZP,
  SNR. `skygraph.target_light_frames(db,name)`(전체 LIGHT, 캡 없음) + `FrameData.light_curve(frames)` →
  시계열. 점광원 가정 명시(확장천체는 조리개내 총플럭스).
- **API(L1)**: `GET /api/photometry/{name}` → {target, points:[{date_obs,filter,flux,mag,snr,status}], zp}.
- **Forge UI(L2)**: DEVICES 탭에 'Forge 전처리' 카드 — on/off + save-calibrated 토글(/api/forge/toggle),
  적용 마스터(sources)·경고(warnings)·last 표시(/api/status.forge 폴링). PANEL_DEF 등록.
- **라이트커브 UI(L3)**: Target Page dossier에 라이트커브 차트(시간↔등급, y반전) + 필터별. hidpi 캔버스.

## 3. 체크리스트 (각 단계 = 1커밋, 검증 후 다음)
- [x] **L1 — 측광 백엔드**: FrameData.photometry(centroid+조리개−배경annulus) + light_curve +
  skygraph.target_light_frames(+세션링크 헬퍼 추출, dossier와 공유) + `/api/photometry/{name}`.
  ✅검증: 합성 가우시안 별 → flux∝amp(1:2:4 정확), mag차 0.752=2.5log10·2, V자 변광 추종, centroid 정중앙,
  dossier 회귀, create_app 97라우트.
- [x] **L2 — Forge UI 카드**: ANALYSIS 탭 'Forge 전처리' 카드(전처리/보정저장 토글 + 마스터 sources·
  경고·최근보정 표시). applyStatus가 /api/status.forge로 렌더, 토글은 /api/forge/toggle{on,save}.
  PANEL_DEF/PROTO 등록, CSS .fg-*, v=137/168. ✅검증: TestClient 토글 왕복(ON→True/OFF→False) +
  node --check. 라이브 DOM은 프리뷰 불안정으로 미검증(데이터경로+패턴으로 갈음).
- [x] **L3 — 라이트커브 UI**: Target Page dossier에 라이트커브 캔버스(시간↔등급, y반전=위가 밝음, 코랄
  점+선). loadDossier가 n_lights>0이면 loadLightCurve→/api/photometry→drawLightCurve. CSS .tp-lc, v=138/169.
  ✅검증: 실DB REGRESS /api/photometry → 실FITS 측광 성공(mag 18.575/flux 371.5/snr 1.56), node --check.
  라이브 DOM은 프리뷰 불안정으로 미검증(데이터경로 실데이터로 갈음).
- [x] **L4 — 풀리뷰 + 회귀**: ✅review-full 4차원 — 치명 0. 실측으로 성능결함 #1 확정·수정: 측광
  거리맵을 전체이미지→centroid 윈도우(r_out+1) + 전체 float64변환 제거(슬라이스만 캐스팅).
  결과 100% 동일, 대형센서 336ms→42ms(I/O 지배), 100프레임 라이트커브 4.3s. 회귀 create_app 97라우트·
  측광 재검 그린. 남은 #2(gain SNR)·#3(LIKE 이스케이프)는 저위험.

## 4. 검증 게이트
- 측광은 **합성 FITS**(astropy.io.fits로 가우시안 별 작성)로 정확도 검증 — 실파일 의존 X.
- DB 경로 `asterion/data/asterion.db`. 임시 DB/합성파일은 테스트 후 정리.
- 프리뷰 매니저 포트 바인딩 불안정(이 환경 반복) → uvicorn 직접 또는 데이터경로+node --check로 갈음.
- 콘솔/서버 에러 0, 기존 동작 보존.

## 5. 가드레일
1. **SIM 전용**, 실하드웨어 금지.
2. **매 증분 커밋** — 메시지 한국어 + `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
3. **기존 동작 보존** — 회귀 깨지면 롤백, additive.
4. **`config.local.json` 금지**.
5. **레이어** — core(fitsio/skygraph) → analysis(framedata) → app 라우트. 측광은 analysis.
6. **막히면 멈춤** — 추측 금지, 결정로그.
7. 범위 = Forge UI + 라이트커브(L1~L4). A/B/C로 새지 말 것.

## 6. 결정 로그 (루프가 추가)
- `2026-06-20 L4 — review-full 통합검토 통과(치명 0). 실측으로 성능결함 확정·수정: (a) 조리개 거리맵을
  전체 h×w → centroid 주변 (r_out+1) 박스 윈도우(결과 동일, 4195× 빠름), (b) d=data.astype(float64)
  전체변환 제거 → box/window 슬라이스만 캐스팅. 합산 대형센서(24M) 336ms→42ms(이제 FITS 디스크 I/O
  지배), 100프레임 라이트커브 4.3s. 결과 100% 불변(V자 mag/flux 동일). 남은 저위험: SNR gain 미보정,
  .contains LIKE 비이스케이프(json 정확매칭이 거름).`
- `2026-06-20 L2/L3 — Forge UI 카드(ANALYSIS, applyStatus 렌더 + /api/forge/toggle 왕복 TestClient
  검증) + 라이트커브 차트(Target Page, /api/photometry, 실DB REGRESS 실FITS 측광 mag 18.575). UI 라이브
  DOM은 프리뷰 매니저 불안정으로 미검증 — 데이터경로/node-check로 갈음.`
- `2026-06-20 L1 — 조리개 측광(framedata, analysis층): 중앙1/3 피크→강도가중 centroid→조리개합−
  배경annulus중앙값×면적=flux→mag=−2.5log10(flux)+25. 점광원 가정(goto/platesolve로 중앙). skygraph에
  _target_session_ids 헬퍼 추출(dossier+target_light_frames 공유, T4 중복 제거). light_curve는 실패
  프레임도 status로 보고(빠짐없이). 검증: 합성 가우시안에서 flux∝amp 정량 일치, mag 로그관계 정확.
  ZP=25 임의(상대광도). WCS/절대측광 아님 — 변광성 곡선 형태용.`
