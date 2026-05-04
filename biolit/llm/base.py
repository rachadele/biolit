"""Abstract base class for LLM clients."""
import os
import subprocess
import sys
from abc import ABC, abstractmethod


def resolve_api_key(env_var: str) -> str | None:
    """Return the API key from the macOS keychain, falling back to the env var.

    Keychain is preferred so a stale ``.env`` value (loaded with
    ``override=True``) cannot mask a working keychain entry. The keychain
    is queried by service name only, so an entry stored with
    ``security add-generic-password -s <env_var> -w`` works as-is.
    On non-darwin platforms, only the env var is consulted.
    """
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
    return os.environ.get(env_var)


class BaseLLMClient(ABC):
    """Minimal interface every LLM provider must implement."""

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def chat(self, messages: list[dict], max_tokens: int = 512) -> str:
        """Send *messages* (OpenAI-style list of dicts) and return the reply text."""
        ...

    def chat_batch(self, messages_list: list[list[dict]], max_tokens: int = 512) -> list[str]:
        """Send multiple independent message lists and return a reply for each.

        Default implementation falls back to sequential ``chat()`` calls.
        Subclasses that support a native batch API should override this to
        reduce cost and latency.  Results are returned in the same order as
        *messages_list*; failed requests return an empty string.
        """
        return [self.chat(messages, max_tokens) for messages in messages_list]

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model!r})"

