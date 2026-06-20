# FORGE UI + 가벼운 라이트커브 PLAN (자율 빌드 계약서)

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
- [ ] **L1 — 측광 백엔드**: FrameData.photometry + skygraph.target_light_frames + FrameData.light_curve +
  `/api/photometry/{name}`. 검증: 합성 FITS(가우시안 별, 밝기 변화) → 측광이 입력 플럭스 추종(밝을수록
  mag↓), 배경/SNR 타당, 빈/누락 프레임 status 처리.
- [ ] **L2 — Forge UI 카드**: DEVICES 탭 Forge 카드(토글+sources/warnings/last). 검증: /api/forge/toggle
  왕복(on→off→on) + status 반영, preview_eval(가능 범위).
- [ ] **L3 — 라이트커브 UI**: Target Page에 라이트커브 차트(시간↔등급). tnPick/검색 대상의 /api/photometry.
  검증: 데이터 바인딩/렌더(preview 불안정 시 데이터경로+node check).
- [ ] **L4 — 풀리뷰 + 회귀**: review-full(변경분) + create_app/기존 status 키/SIM 그린.

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
-
