"""Ollama local-model client (uses the Ollama REST API directly via requests)."""
import requests
from pubmed_screener.llm.base import BaseLLMClient


class OllamaClient(BaseLLMClient):
    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        super().__init__(model)
        self._base_url = base_url.rstrip("/")

    def chat(self, messages: list[dict], max_tokens: int = 512) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        resp = requests.post(
            f"{self._base_url}/api/chat",
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

