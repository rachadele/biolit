"""Abstract base class for LLM clients."""
import os
from abc import ABC, abstractmethod


def resolve_api_key(env_var: str) -> str | None:
    """Return the API key from the env var, falling back to the macOS keychain.

    The keychain lookup uses ``env_var`` as both the service and account name —
    e.g. ``security add-generic-password -s ANTHROPIC_API_KEY -a ANTHROPIC_API_KEY``.
    """
    api_key = os.environ.get(env_var)
    if api_key:
        return api_key
    try:
        import keyring
        return keyring.get_password(env_var, env_var)
    except Exception:
        return None


class BaseLLMClient(ABC):
    """Minimal interface every LLM provider must implement."""

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def chat(self, messages: list[dict], max_tokens: int = 512) -> str:
        """Send *messages* (OpenAI-style list of dicts) and return the reply text."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model!r})"

