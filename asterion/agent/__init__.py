"""AI 에이전트 계층 (§12 입구) — 대시보드 임베디드 대화 제어.

원칙(사용자 합의): **AI는 벤더중립 부품**. LLM은 OpenAI 호환 인터페이스로 끼우고
(Claude/GPT/Groq/Ollama/자체 — config로 교체), 도구는 ASTERION 서비스를 인프로세스로
부른다. 모델이 뭘 하든 실행계는 ActionBus 안전게이트를 그대로 통과한다(우회 불가).

  llm.py     — OpenAI 호환 chat+tools 클라이언트 (provider 스왑)
  toolkit.py — 도구 정의(JSON 스키마) + 인프로세스 디스패치 (status·행성·계획·goto·돔)
  core.py    — 에이전트 루프 (LLM ↔ 도구, 바운드 반복)
  api.py     — /api/agent/chat·/status 라우터
"""
