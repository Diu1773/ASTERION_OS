# tests — 안전 불변식 회귀 가드

ASTERION의 안전·자율 핵심 불변식을 stdlib `unittest`로 고정한다(추가 의존성 없음).
2026-06-21 3관점 리뷰에서 손으로 검증한 결함 수정들을 영구 테스트로 전환했다.

## 실행

프로젝트 루트에서:

```bash
.venv/Scripts/python -m unittest discover -s tests -t .        # 전체
.venv/Scripts/python -m unittest discover -s tests -t . -v     # 상세
.venv/Scripts/python -m unittest tests.test_safety_evaluate    # 단일 모듈
```

## 커버리지

| 파일 | 불변식 |
|---|---|
| `test_safety_evaluate.py` | fail-closed(데이터 없음/stale=unsafe), 강수·강풍·습도·운량 위험, 주간 보호가 세션에 마스킹되지 않음(F11), 임계 단조성 |
| `test_solar_exclusion.py` | 각이격 계산, 태양 중심 반경 안이면 차단(근처도), 야간 정상대상 오탐 0, 좌표부족 fail-closed |
| `test_status_freshness.py` | 스냅샷 정체 시 `current_safety`가 FAULT로 떨어뜨림(메타 fail-open 방지) |
| `test_dome_guard.py` | 'opening'/미확증 상태 비상닫힘(F2), 닫기 실패 시 재시도 수렴(F3) |
| `test_orchestrator_gates.py` | 카메라 단일점유 대칭(F8), 이중시작 경합 차단(F10), 태양 대상 거부 |
| `test_agent_gate.py` | AI 실행계 안전게이트 상속, 돔 개방 게이트(닫기 허용), 태양 본체·근방 거부, 책임자 override |

`_helpers.py` = 공용 가짜 cfg/장비/버스 + 임시 DB. 비동기 메서드는 `run()`(asyncio.run)로 동기 테스트에서 호출.

## 주의

- 실HW가 아닌 SIM/합성·가짜 주입 기반. 실제 ASCOM/PWI4 하드웨어 거동은 미검증.
- HTTP 엔드포인트의 태양 가드 배선(app.py 클로저)은 이 단위 스위트가 아니라 수동/TestClient로 확인했다(빌딩블록 `solar_exclusion_check`·orchestrator·toolkit precond는 여기서 커버).
