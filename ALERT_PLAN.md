# Alert System PLAN — 위험 상황 알림 (자율 빌드 계약서)

> 워크플로 추천(점수 뒤집음): 무인 운영(Night Runner) 안전 루프의 빠진 고리. 지금은 비상폐쇄+수동셔터가
> 콘솔 로그 1줄뿐 → 운영자가 화면 안 보면 망원경이 비에 젖음. **추가형**(기존 safety/dome 무수정), SIM 100%.

## 0. 목표
안전 스냅샷(이미 구조화된 safety.evaluate 출력)을 룰로 평가해 **Alert**를 발화 → DB 적재 + WebSocket
브로드캐스트 → 대시보드 배지/토스트 + CRITICAL 경보음. 외부 채널(SMS/SMTP)은 범위 밖(MVP).

## 1. 현재 상태 (재사용할 입력 — 신규 계산 없음)
- `watchtower/safety.py` evaluate → {state, reasons, weather_stale?, weather_warn?}. 상태: SAFE_CLOSED/
  READY_CHECK/OPEN_ALLOWED/OBSERVING/WEATHER_HOLD/EMERGENCY_CLOSE/FAULT.
- `core/events.py` EventHub: emit/log/frame/action/status (alert는 1:1 복제로 추가).
- `watchtower/status.py` StatusSampler: 매 틱 snapshot 생성(safety 포함). dome/mount 등 장비 키.
- `watchtower/dome_guard.py`: 비상 시 신호. `web/app.js` logLine: WS 로그 표시. [[asterion-weather-ingestion]]

## 2. 설계
- **E1 온톨로지 + AlertManager 코어**: `Alert` 테이블(rule_id·level·title·detail·state·acknowledged·acked_by/utc) +
  `watchtower/alert.py` RULES(snap→alert|None) + AlertManager.evaluate(snap)→발화분(쿨다운: 같은 rule_id가
  최근 N초 내 있으면 스킵) + DB 적재. 룰: emergency_close(CRIT)·safety_fault(CRIT)·dome_unsafe_open(CRIT)·
  weather_stale(WARN)·weather_hold(WARN). **외부채널 0, 기존 경로 읽기만.**
- **E2 EventHub.alert() + 샘플러 배선**: events.alert(rec)(emit type:"alert"+버퍼) + status.py가 스냅샷 직후
  evaluate_alerts → 발화분 emit. 기존 safety/dome 무수정.
- **E3 대시보드 전달**: app.js WS type:"alert" → 토스트 + CRITICAL 경보음(WebAudio, mp3 불필요) + 미확인 배지.
  `GET /api/alerts`·`/api/alerts/active`·`POST /api/alerts/acknowledge`.
- **E4 풀리뷰 + 회귀**.

## 3. 체크리스트
- [ ] **E1 — 온톨로지 + AlertManager**: Alert 테이블 + alert.py 룰/쿨다운/적재. 검증: 스냅샷 3종(정상/풍속25/
  stale) → 발화 개수·level, 쿨다운 내 재호출 빈 리스트.
- [ ] **E2 — EventHub.alert + 배선**: events.alert + status.py 호출. 검증: 합성 스냅샷 1틱 → DB Alert 행 +
  가짜 WS 클라이언트 type:"alert" 수신.
- [ ] **E3 — 대시보드 전달**: WS 핸들러·토스트·경보음·배지 + /api/alerts(active/acknowledge). 검증: ack→
  acknowledged/acked_by DB, /active는 미ack만 (소리/토스트 육안 1회).
- [ ] **E4 — 풀리뷰 + 회귀**: 리뷰 + create_app/SIM 그린.

## 4~5. 게이트·가드레일
SIM/합성/임시DB/Fake WS. DB 경로 asterion/data. **기존 safety/dome/sampler 경로 읽기만(무수정)·fail-closed 보존.**
config.local.json 금지. 매 증분 커밋(+Co-Authored-By). 외부 I/O(SMS/SMTP) 제외. 막히면 멈춤.

## 6. 결정 로그
-
