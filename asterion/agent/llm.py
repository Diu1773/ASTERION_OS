"""OpenAI 호환 chat+tools 클라이언트 — provider 무관(벤더중립 부품).

base_url/model/api_key만 config로 바꾸면 OpenAI·Groq·OpenRouter·Ollama(로컬)·
Anthropic(호환 엔드포인트) 어디든 붙는다. SDK 없이 httpx로 chat/completions 호출.
"""

from __future__ import annotations

import httpx


def _err_text(r: httpx.Response) -> str:
    """provider 에러 응답에서 사람이 읽을 메시지만 뽑는다(키/스택 노출 없이)."""
    try:
        j = r.json()
        e = j.get("error") if isinstance(j, dict) else None
        if isinstance(e, dict):
            return str(e.get("message") or e.get("code") or e)[:300]
        return str(e or j)[:300]
    except Exception:
        return (r.text or "")[:300]


class LLMError(RuntimeError):
    """provider HTTP 오류 — status와 사람이 읽을 메시지를 담는다."""

    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"{status}: {message}")


class LLM:
    def __init__(self, base_url: str, model: str, api_key: str = "",
                 extra_headers: dict | None = None, timeout: float = 90.0):
        self.base_url = (base_url or "").rstrip("/")
        self.model = model or ""
        self.timeout = timeout
        self._api_key = api_key or ""
        self.extra_headers = extra_headers or {}

    @property
    def headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._api_key:                       # 빈 키면 Authorization 자체를 안 보냄
            h["Authorization"] = f"Bearer {self._api_key}"
        h.update(self.extra_headers)
        return h

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
            # 한 턴에 하나씩만 — 약한 모델의 병렬 도구호출 검증실패(400)를 줄인다.
            payload["parallel_tool_calls"] = False
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.base_url}/chat/completions",
                             headers=self.headers, json=payload)
            if r.status_code >= 400:
                raise LLMError(r.status_code, _err_text(r))
            data = r.json()
        choices = data.get("choices") or []
        if not choices or not choices[0].get("message"):
            raise LLMError(502, f"응답에 choices 없음: {str(data)[:200]}")
        return choices[0]["message"]

    async def list_models(self) -> list[str]:
        """provider의 사용 가능 모델 id 목록(OpenAI 호환 GET /models).
        OpenAI·Groq·Ollama·OpenRouter 모두 지원. 실패하면 LLMError."""
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.get(f"{self.base_url}/models", headers=self.headers)
            if r.status_code >= 400:
                raise LLMError(r.status_code, _err_text(r))
            data = r.json()
        items = data.get("data") or data.get("models") or []
        out: list[str] = []
        for it in items:
            mid = (it.get("id") or it.get("name")) if isinstance(it, dict) else it
            if mid:
                out.append(str(mid))
        return sorted(set(out))
