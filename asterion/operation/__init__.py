"""Operation 계층 — 관측 계획(Meridian)과 실행 지휘(Orchestrator).

로드맵 §8: Meridian이 '무엇을 관측할지' 결정하고, Orchestrator가 '어떤 순서로
실행할지' 지휘한다. 둘 다 장비를 직접 만지지 않고 core.actions.ActionBus를
통해서만 세계를 바꾼다 (감사·사전조건·안전 게이트 일관 적용).
"""
