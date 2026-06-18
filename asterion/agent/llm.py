"""OpenAI 호환 chat+tools 클라이언트 — provider 무관(벤더중립 부품).

base_url/model/api_key만 config로 바꾸면 OpenAI·Groq·OpenRouter·Ollama(로컬)·
Anthropic(호환 엔드포인트) 어디든 붙는다. SDK 없이 httpx로 chat/completions 호출.
"""

from __future__ import annotations

import httpx


class LLM:
    def __init__(self, base_url: str, model: str, api_key: str = "",
                 extra_headers: dict | None = None, timeout: float = 90.0):
        self.base_url = (base_url or "").rstrip("/")
        self.model = model or ""
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
        if extra_headers:
            self.headers.update(extra_headers)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """messages(+tools)로 한 번 호출하고 assistant 메시지(dict)를 돌려준다.
        content 또는 tool_calls를 담는다 (OpenAI chat-completions 규격)."""
        payload: dict = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.base_url}/chat/completions",
                             headers=self.headers, json=payload)
            r.raise_for_status()
            data = r.json()
        return data["choices"][0]["message"]
