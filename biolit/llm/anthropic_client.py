"""Anthropic Claude client."""
import anthropic
from biolit.llm.base import BaseLLMClient, resolve_api_key


class AnthropicClient(BaseLLMClient):
    def __init__(self, model: str, api_key: str | None = None):
        super().__init__(model)
        api_key = api_key or resolve_api_key("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not set in environment or macOS keychain"
            )
        self._client = anthropic.Anthropic(api_key=api_key)

    def chat(self, messages: list[dict], max_tokens: int = 512) -> str:
        # Anthropic separates system messages from user/assistant turns.
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        user_messages = [m for m in messages if m["role"] != "system"]
        kwargs = {}
        if system_parts:
            kwargs["system"] = "\n".join(system_parts)
        response = self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=user_messages,
            **kwargs,
        )
        return response.content[0].text

