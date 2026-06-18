"""다중 공급자 허브 — named provider(각자 base_url/api_key/model)를 들고,
active 하나를 LLM처럼 노출한다. Agent는 이 객체를 LLM으로 보고(.chat/.configured/
.model), api 라우트는 provider/model 전환에 쓴다. 벤더중립 원칙의 운영면.

config 구조(둘 다 지원):
  신규 — agent.active + agent.providers.<name>.{base_url,api_key,model}
  레거시 — agent.{base_url,model,api_key} (단일 'default' provider로 감쌈)
"""

from __future__ import annotations

from .llm import LLM


class ProviderHub:
    def __init__(self, cfg):
        self.cfg = cfg
        self.reload()

    def reload(self) -> None:
        agent = self.cfg.get("agent", {}) or {}
        provs = {k: dict(v) for k, v in (agent.get("providers") or {}).items()
                 if isinstance(v, dict)}
        if not provs and (agent.get("base_url") or agent.get("model")):
            # 레거시 평면 구조 → base_url로 이름 추론한 단일 provider
            provs = {self._infer_name(str(agent.get("base_url", ""))): {
                "base_url": agent.get("base_url", ""),
                "model": agent.get("model", ""),
                "api_key": agent.get("api_key", "")}}
        self.providers = provs
        active = agent.get("active")
        if active not in provs:
            active = next(iter(provs), "")
        self.active = active

    @staticmethod
    def _infer_name(base_url: str) -> str:
        u = base_url.lower()
        if "groq" in u:
            return "groq"
        if "openai" in u:
            return "openai"
        if "11434" in u or "ollama" in u:
            return "ollama"
        if "openrouter" in u:
            return "openrouter"
        return "default"

    def _spec(self) -> dict:
        return self.providers.get(self.active, {}) if self.active else {}

    @property
    def _llm(self) -> LLM:
        s = self._spec()
        return LLM(base_url=str(s.get("base_url", "")),
                   model=str(s.get("model", "")),
                   api_key=str(s.get("api_key", "")))

    # ── Agent가 보는 LLM 인터페이스 ─────────────────────────────
    @property
    def configured(self) -> bool:
        return self._llm.configured

    @property
    def model(self) -> str:
        return str(self._spec().get("model", ""))

    async def chat(self, messages, tools=None):
        return await self._llm.chat(messages, tools)

    async def list_models(self):
        return await self._llm.list_models()

    # ── provider/model 관리(라우트가 사용) ──────────────────────
    def list_providers(self) -> list[dict]:
        """키 값은 절대 노출하지 않고 메타만(이름/모델/주소/키유무/active)."""
        return [{"name": n,
                 "model": str(s.get("model", "")),
                 "base_url": str(s.get("base_url", "")),
                 "has_key": bool(s.get("api_key")),
                 "active": n == self.active}
                for n, s in self.providers.items()]

    def set_provider(self, name: str) -> bool:
        if name not in self.providers:
            return False
        self.active = name
        self.cfg.set("agent.active", name)
        return True

    def set_model(self, model: str) -> bool:
        if not self.active:
            return False
        self.providers.setdefault(self.active, {})["model"] = model
        self.cfg.set(f"agent.providers.{self.active}.model", model)
        return True
