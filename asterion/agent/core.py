"""에이전트 루프 — LLM ↔ 도구 (바운드 반복). 모델이 도구를 호출하면 실행해 결과를
되먹이고, 도구 호출이 없으면 그 답을 최종으로 돌려준다. 미설정이면 안내 메시지."""

from __future__ import annotations

import json
from typing import Any

from .llm import LLMError

DEFAULT_SYSTEM = (
    "당신은 청람천문대 ASTERION OS의 관측 어시스턴트입니다. 도구로 관측소 상태·행성 "
    "위치·관측 계획을 확인하고 실행합니다. 사용자가 천체를 '보여달라'고 하면 먼저 "
    "planet_position으로 가시성을 확인하고, 지평선/한계 아래면 그 이유를 설명한 뒤 "
    "visible_planets로 대안을 제시하세요. 실행계(goto/run/dome)는 안전게이트를 통과하며, "
    "거부되면 그 사유를 사용자에게 그대로 전하세요. 한국어로 간결하고 친근하게, 추측하지 "
    "말고 도구 결과에 근거해 답하세요."
)


class Agent:
    def __init__(self, llm, toolkit, system_prompt: str = DEFAULT_SYSTEM,
                 max_iters: int = 6):
        self.llm = llm
        self.tk = toolkit
        self.sys = system_prompt or DEFAULT_SYSTEM
        self.max_iters = max_iters

    @property
    def configured(self) -> bool:
        return self.llm.configured

    async def run(self, message: str, history: list[dict] | None = None) -> dict[str, Any]:
        if not self.llm.configured:
            return {"configured": False, "transcript": [],
                    "reply": "AI 모델이 아직 안 붙었어요. config [agent]에 base_url·model"
                             "(+토큰) 또는 로컬 Ollama를 넣으면 바로 대화로 관측소를 몰 수 "
                             "있습니다. (예: Ollama base_url=http://127.0.0.1:11434/v1)"}
        msgs: list[dict] = [{"role": "system", "content": self.sys}]
        for h in (history or [])[-12:]:
            if h.get("role") in ("user", "assistant") and h.get("content"):
                msgs.append({"role": h["role"], "content": h["content"]})
        msgs.append({"role": "user", "content": message})

        transcript: list[dict] = []
        try:
            for _ in range(self.max_iters):
                m = await self.llm.chat(msgs, tools=self.tk.specs)
                msgs.append(m)
                tool_calls = m.get("tool_calls") or []
                if not tool_calls:
                    return {"configured": True, "reply": m.get("content") or "",
                            "transcript": transcript}
                for tc in tool_calls:
                    fnc = tc.get("function", {})
                    name = fnc.get("name", "")
                    try:
                        args = json.loads(fnc.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    result = await self.tk.call(name, args)
                    transcript.append({"tool": name, "args": args, "result": result})
                    msgs.append({"role": "tool", "tool_call_id": tc.get("id"),
                                 "content": json.dumps(result, ensure_ascii=False)})
        except LLMError as e:
            return {"configured": True, "transcript": transcript,
                    "reply": self._explain(e), "error": True}
        except Exception as e:   # 도구/네트워크 등 예기치 못한 오류도 채팅에 곱게
            return {"configured": True, "transcript": transcript,
                    "reply": f"⚠ 처리 중 오류: {e}", "error": True}
        return {"configured": True, "transcript": transcript,
                "reply": "(도구 호출이 너무 많아 멈췄어요 — 질문을 더 좁혀줄래요?)"}

    def _explain(self, e: LLMError) -> str:
        """provider 오류를 사용자 행동으로 옮길 수 있는 한국어 안내로."""
        if e.status in (401, 403):
            return ("⚠ AI 키가 없거나 거부됐어요. asterion/config.local.json 의 "
                    f"agent.api_key 를 확인해줘. (provider: {e.message})")
        if e.status == 404 or "model" in e.message.lower():
            return (f"⚠ 모델 '{self.llm.model}' 을(를) 못 찾았어요 — 헤더의 모델 선택에서 "
                    f"다른 걸 골라줘. (provider: {e.message})")
        if e.status == 429:
            return f"⚠ 사용량/속도 한도(429) — 잠시 뒤 다시. (provider: {e.message})"
        return f"⚠ AI 오류({e.status}): {e.message}"
