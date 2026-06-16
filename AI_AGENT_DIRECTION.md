# ASTERION AI Agent — 방향 설계 (§10/§12)

> 근거: deep-research(2026-06-16). 자율 관측소의 *기본기*(스케줄링·안전·품질평가)는 이미
> 표준이라 차별화가 안 된다. **빈틈 = (1) 범용 대화 제어("화성 보고싶어"→실행) + (2) 운영자
> 피드백 학습.** 선례 StarWhisper Telescope(NINA에 LLM 덧붙임, 초신성 서베이 한정·검토게이트·
> function-call 70%)가 개념을 입증했고, ASTERION은 더 깨끗한 기반(ActionBus·온톨로지)을 가졌다.
> 이 문서는 그 차별화 층을 *어떻게* 얹을지의 북극성이다. (구현 시점: UI 리팩토링 + LLM 연동 후)

## 핵심 원칙 — AI에게 새 권한을 주지 않는다
AI는 사람/대시보드와 **똑같은 API만** 호출한다. 장비를 직접 만지지 않고 `ActionBus`를 통과하므로:
- **fail-closed 안전 게이트가 AI에게도 그대로 적용** → AI가 주간 슬루·기상 불안정 촬영 같은
  *위험*을 저지를 수 없다. **별도 AI 안전장치 불필요** — 이게 우리 아키텍처의 결정적 이점.
- 모든 AI 행동이 `ActionLog`/`Decision`에 감사 기록 → 학습·설명·롤백의 기반.
- 따라서 남은 리스크는 *위험*이 아니라 **정확도**(SWT의 70% 문제) — 아래로 완화.

## 아키텍처
```
사용자 (대화/음성, 초보~심화)
        │  자연어
   ┌────▼─────────────────────────┐
   │  AI Agent (LLM + tool-use)    │  ← Claude (최신, tool use) 또는 MCP 서버로 노출
   │  - 대화·의도 파악·설명         │     (pixinsight-mcp 선례: Claude+MCP)
   │  - 온톨로지 기억/학습(RAG)      │
   └────┬─────────────────────────┘
        │  구조화 도구 호출 (기존 엔드포인트의 얇은 래퍼)
   ┌────▼───────────────────────────────────────────┐
   │ resolve_target(이름/행성→ra,dec; ephemeris/Sesame)│
   │ check_visibility/safety (status·고도·박명)        │
   │ propose_plan → Meridian.create_plan (draft)      │
   │ start_observation → Orchestrator.start_plan      │  ← 전부 이미 구현됨
   │ get_status / list_plans / query_frames+quality   │
   │ stop / explain_decision                          │
   └────┬───────────────────────────────────────────┘
        │  ActionBus (감사·사전조건·안전게이트) — AI도 예외 없음
   ┌────▼────┐
   │ 장비/온톨로지 │
   └─────────┘
```
**MCP 옵션**: ASTERION API를 MCP 서버로 노출하면 어떤 LLM 클라이언트(Claude Desktop 등)든
드라이버가 된다 — 저결합, pixinsight-mcp가 인접 선례.

## 단계 (각 단계가 독립적으로 유용 — 점진적 신뢰 확대)
- **A. 읽기전용 대화** — "화성 지금 볼 수 있어?", "오늘 뭐 찍었어?", "이 프레임 왜 rejected야?"
  → 상태/온톨로지/ephemeris 조회만. 실행 0. *즉시 유용·무위험.* (UI 전에 API로도 가능)
- **B. 계획 제안 + 확인** — "화성 R/G/B로 찍어줘" → 에이전트가 `ObservationPlan` 초안 제시
  ("고도 35°, R/G/B 30s×10, 실행할까요?") → 사용자 승인 → Orchestrator 실행. **확인 게이트 필수**
  (SWT식 human-in-the-loop이되 *최종사용자*용). 초보=에이전트가 설정, 심화=파라미터 직접.
- **C. 피드백 학습 (차별점 ②)** — RL 말고 **RAG-over-온톨로지부터**: 운영자 수정/평가를
  `Feedback`에 적재 → 다음 제안 시 과거 `Decision`/`QualityMetric`/`Feedback`를 검색해 컨텍스트로
  주입("지난번 이 대상은 노출 60s가 과했음"). RL 스케줄링은 연구단계라 후순위.
- **D. (후속) 점진 자율** — 신뢰 쌓이면 확인 마찰 축소, 일부 결정 위임. RL/연속학습은 D에서.

## 정확도 리스크 완화 (SWT 70% 교훈)
1. **작고 명확한 도구셋** + 엄격한 JSON 스키마(function-calling 신뢰도↑).
2. **세계를 바꾸는 행동(goto/expose) 전 항상 확인 단계** — 오실행을 사람이 차단.
3. **안전 게이트가 위험 오실행을 물리적으로 차단**(정확도와 독립).
4. 모든 호출을 `ActionLog`에 기록 → 실패 패턴 분석·도구 개선 루프.

## 지금 당장 할 수 있는 준비 (구현 아님, 기반)
- 위 "도구"는 전부 기존 엔드포인트(`/api/meridian`·`/api/orchestrator`·`/api/status`·
  `/api/sentinel`·`/api/analysis`)의 얇은 래퍼다. **행성 ephemeris(화성 등) 해석**만 추가 필요
  (현재 Sesame 이름해석은 항성/딥스카이용; 행성은 JPL Horizons/skyfield).
- 단계 A(읽기전용 대화)는 LLM 도구 정의 + 1개 chat 엔드포인트로 가장 먼저 가능.

## 한계 인지
- (b)빈틈은 '부재의 증거' — 비공개 시스템 존재 가능성 배제 못함. 시점 2026-06 스냅샷.
- function-calling 신뢰도가 진짜 난관. 안전은 게이트로 막히지만 "의도대로"는 공들일 것.
