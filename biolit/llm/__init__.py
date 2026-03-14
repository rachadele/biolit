"""LLM abstraction layer."""
from biolit.llm.base import BaseLLMClient
from biolit.llm.anthropic_client import AnthropicClient
from biolit.llm.openai_client import OpenAIClient
from biolit.llm.ollama_client import OllamaClient

_DEFAULTS = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "ollama": "llama3",
}


def get_llm_client(provider: str, model: str | None = None, **kwargs) -> BaseLLMClient:
    """Factory: return the right LLMClient for the given provider."""
    provider = provider.lower()
    model = model or _DEFAULTS.get(provider)
    if provider == "anthropic":
        return AnthropicClient(model=model, **kwargs)
    if provider == "openai":
        return OpenAIClient(model=model, **kwargs)
    if provider == "ollama":
        return OllamaClient(model=model, **kwargs)
    raise ValueError(f"Unknown LLM provider: {provider!r}. Choose from: anthropic, openai, ollama")


__all__ = ["BaseLLMClient", "AnthropicClient", "OpenAIClient", "OllamaClient", "get_llm_client"]

