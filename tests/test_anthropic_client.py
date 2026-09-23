"""Tests for biolit.llm.anthropic_client."""
from types import SimpleNamespace
from unittest.mock import MagicMock

from biolit.llm.anthropic_client import AnthropicClient


def test_chat_skips_thinking_blocks():
    # Extended-thinking models put a ThinkingBlock (no .text) before the answer.
    client = AnthropicClient("claude-test", api_key="test-key")
    client._client = MagicMock()
    client._client.messages.create.return_value = SimpleNamespace(content=[
        SimpleNamespace(type="thinking", thinking="..."),
        SimpleNamespace(type="text", text="yes"),
    ])
    assert client.chat([{"role": "user", "content": "q"}]) == "yes"
