"""Abstract base class for LLM clients."""
import os
import subprocess
import sys
from abc import ABC, abstractmethod


def resolve_api_key(env_var: str) -> str | None:
    """Return the API key from the env var, falling back to the macOS keychain.

    On macOS, the keychain is queried by service name only (no account
    required), so an entry stored with the conventional ``security
    add-generic-password -s <env_var> -w`` works as-is.
    """
    api_key = os.environ.get(env_var)
    if api_key:
        return api_key
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", env_var, "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                value = r.stdout.strip()
                if value:
                    return value
        except Exception:
            pass
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

