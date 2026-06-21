# 시뮬레이터 데이터 격리 + 보존 + Grafana PLAN (구현 완료)

> **상태: ✅ 구현·테스트 완료(2026-06-22), 미커밋.** /goal: "시뮬레이터 데이터는 한 3달로만
> 제한해서 따로 둬서 테스팅까지 다해서, 패널들 연결해서 캡처까지하고 grafana까지 붙일 수 있도록".
> 전부 **추가형** — 안전계층·기존 텔레메트리 0줄 의미변경. 전체 스위트 97 그린.

## 0. 목표 → 산출물

| 목표 | 산출물 |
|---|---|
| 시뮬 데이터 3개월 제한·격리 | sim 모드 → `data/sim/` 분리 + 90일 자동 보존 정리 |
| 테스팅 | `test_retention`(3) · `test_capture_e2e`(1) · `test_metrics`(8) |
| 패널 연결 캡처까지 | 캡처 e2e(연결→노출→Frame+FITS+프리뷰) 실부품 검증 |
| Grafana 연동 | `/metrics`(Prometheus) + `tools/grafana/`(scrape·dashboard·README) |

## 1. 시뮬 데이터 격리 (Task 1)
- [config.py](asterion/config.py) `data_dir`를 **모드 인식형**으로 — sim이면 `paths.sim_subdir`(기본
  `sim`) 하위로 분기(`data/sim/`). 실측 모드는 `data/` 그대로 → DB·FITS가 절대 안 섞임.
- [config.toml](asterion/config.toml): `[paths].sim_subdir`, `[sim_retention]`(enabled/days/sweep) 추가.

## 2. 90일 보존 정리 (Task 2)
- [core/retention.py](asterion/core/retention.py) `Retention.prune()` — cutoff(기본 90일)보다 오래된
  프레임 **파일(frames_dir 하위 한정, 경로탈출 방지) + DB행**(frame·session·weather·action·alert·
  telescope_state·decision·focus·telemetry·calibration) + **고아 품질지표** 정리, 빈 날짜폴더 제거.
- [app.py](asterion/app.py): sim+enabled일 때만 생성 → 기동 시 1회 + `sweep_interval_hours` 주기 루프.
  `GET /api/sim/retention`(상태)·`POST /api/sim/retention/sweep`(즉시 실행).
- **실측 데이터 미적용**(sim 모드에서만 배선).

## 3. 캡처 end-to-end (Task 3)
- [tests/test_capture_e2e.py](tests/test_capture_e2e.py): 실제 sim 드라이버(ConnectionManager)·
  ActionBus·fitsio로 `CaptureService.start` 구동 → Frame 적재 + FITS가 격리 `sim/frames`에 저장 +
  프리뷰 콜백까지 검증(패널 캡처 버튼이 타는 경로 = `/api/actions/camera/capture`).
- 라이브 스모크로 전체 앱(`create_app` + TestClient)에서도 캡처→프레임 적재 확인.

## 4. Grafana / Prometheus (Task 4)
- [watchtower/metrics.py](asterion/watchtower/metrics.py) `render_metrics(snapshot)` — 샘플러
  스냅샷 → Prometheus 텍스트. `telemetry_last` 평탄 채널을 `asterion_<채널>` 게이지로, 상태/모드는
  라벨 게이지로(`asterion_safety_state{state=...}`), 세션 플래그 0/1, +업타임·디스크.
- [app.py](asterion/app.py) `GET /metrics`(`text/plain; version=0.0.4`). 인증 켜짐 시 **scope=metrics
  토큰(Prometheus Bearer) 또는 로그인 사용자** 접근(roles/auth에 `metrics` 권한 추가).
- [tools/grafana/](tools/grafana/): `prometheus.yml`(scrape)·`asterion-dashboard.json`(임포트용)·
  `README.md`(설치 단계).

## 5. 검증 게이트
- stdlib unittest 전체 **97 그린**(회귀 0). `create_app` 빌드 + 라이브 /metrics·캡처·retention 스모크.
- SIM 전용, DB는 임시폴더, 기존 안전 불변식 스위트 보존.

## 6. 결정 로그
- `2026-06-22 sim 격리=data_dir 모드인식(data/sim/) — cfg.data_dir 한 곳만 바꿔 DB·frames 동시
  격리(호출부 0수정). 보존=Retention(파일 frames_dir 한정 삭제 + 시각컬럼<cutoff 행 + 고아 QM),
  sim+enabled만 배선해 실측 데이터 불가침. Grafana=Prometheus /metrics(snapshot→게이지), token:
  metrics 또는 로그인 사용자. 전부 추가형, 97 그린, 미커밋(다른 세션에서 이어 커밋 예정).`
