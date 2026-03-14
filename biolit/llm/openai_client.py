"""OpenAI (and OpenAI-compatible) client."""
import os
from biolit.llm.base import BaseLLMClient


class OpenAIClient(BaseLLMClient):
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        super().__init__(model)
        try:
            import openai
        except ImportError:
            raise ImportError("Install openai: pip install openai")
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY not set")
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)

    def chat(self, messages: list[dict], max_tokens: int = 512) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content

