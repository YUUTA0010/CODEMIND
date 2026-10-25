"""Provider-independent language-model interface. The agent never executes commands; it only exchanges text.

API keys come from the process environment (Settings) and are never stored in the database or returned by the API.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.config import Settings, get_settings


class ProviderUnavailable(Exception):
    """Raised when no model is configured or the remote call fails; callers fall back to deterministic behavior."""


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.0) -> LLMResponse: ...


class NullProvider:
    name = "none"
    model = ""

    def complete(self, system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.0) -> LLMResponse:
        raise ProviderUnavailable("no language-model provider configured (CODEMIND_LLM_PROVIDER=none)")


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 90.0):
        self.base_url, self.model, self.api_key, self.timeout = base_url.rstrip("/"), model, api_key, timeout

    def complete(self, system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.0) -> LLMResponse:
        import httpx

        try:
            r = httpx.post(f"{self.base_url}/chat/completions", timeout=self.timeout, headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"model": self.model, "temperature": temperature, "max_tokens": max_tokens,
                                 "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            r.raise_for_status()
            d = r.json()
            u = d.get("usage", {})
            return LLMResponse(d["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0), self.model)
        except Exception as e:  # noqa: BLE001
            raise ProviderUnavailable(f"{type(e).__name__}: {e}") from e


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str, base_url: str = "", timeout: float = 90.0):
        self.model, self.api_key, self.timeout = model, api_key, timeout
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")

    def complete(self, system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.0) -> LLMResponse:
        import httpx

        try:
            r = httpx.post(f"{self.base_url}/v1/messages", timeout=self.timeout,
                           headers={"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
                           json={"model": self.model, "max_tokens": max_tokens, "temperature": temperature, "system": system,
                                 "messages": [{"role": "user", "content": user}]})
            r.raise_for_status()
            d = r.json()
            text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
            u = d.get("usage", {})
            return LLMResponse(text, u.get("input_tokens", 0), u.get("output_tokens", 0), self.model)
        except Exception as e:  # noqa: BLE001
            raise ProviderUnavailable(f"{type(e).__name__}: {e}") from e


class ScriptedProvider:
    """TEST DOUBLE: returns pre-written responses in order. Used only by automated tests; never by production paths."""

    name = "scripted-test-double"
    model = "scripted"

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str, *, max_tokens: int = 2000, temperature: float = 0.0) -> LLMResponse:
        self.calls.append((system, user))
        if not self.responses:
            raise ProviderUnavailable("scripted provider exhausted")
        return LLMResponse(self.responses.pop(0), 0, 0, self.model)


def get_provider(settings: Settings | None = None) -> LLMProvider:
    s = settings or get_settings()
    if s.llm_provider == "openai_compatible" and s.llm_api_key and s.llm_base_url and s.llm_model:
        return OpenAICompatibleProvider(s.llm_base_url, s.llm_model, s.llm_api_key)
    if s.llm_provider == "anthropic" and s.llm_api_key and s.llm_model:
        return AnthropicProvider(s.llm_model, s.llm_api_key, s.llm_base_url)
    return NullProvider()
