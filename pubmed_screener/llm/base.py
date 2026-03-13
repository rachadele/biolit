"""Abstract base class for LLM clients."""
from abc import ABC, abstractmethod


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

