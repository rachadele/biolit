"""Anthropic Claude client."""
import time
import anthropic
from biolit.llm.base import BaseLLMClient, resolve_api_key

_BATCH_POLL_INITIAL = 5    # seconds between first polls
_BATCH_POLL_MAX     = 30   # seconds between polls once stable
_BATCH_TIMEOUT      = 6 * 3600  # 6 hours


class AnthropicClient(BaseLLMClient):
    def __init__(self, model: str, api_key: str | None = None):
        super().__init__(model)
        api_key = api_key or resolve_api_key("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "ANTHROPIC_API_KEY not found in macOS keychain or environment"
            )
        self._client = anthropic.Anthropic(api_key=api_key)

    def _messages_kwargs(self, messages: list[dict], max_tokens: int) -> dict:
        """Build keyword args for messages.create / batch params."""
        system_parts = [m["content"] for m in messages if m["role"] == "system"]
        user_messages = [m for m in messages if m["role"] != "system"]
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": user_messages,
        }
        if system_parts:
            kwargs["system"] = "\n".join(system_parts)
        return kwargs

    def chat(self, messages: list[dict], max_tokens: int = 512) -> str:
        kwargs = self._messages_kwargs(messages, max_tokens)
        response = self._client.messages.create(**kwargs)
        return response.content[0].text

    def chat_batch(self, messages_list: list[list[dict]], max_tokens: int = 512) -> list[str]:
        """Submit all messages as a single Anthropic Message Batch (50 % cheaper).

        Blocks until the batch completes or *_BATCH_TIMEOUT* is reached.
        Failed / expired individual requests return an empty string.
        """
        if not messages_list:
            return []

        requests = [
            {
                "custom_id": str(i),
                "params": self._messages_kwargs(msgs, max_tokens),
            }
            for i, msgs in enumerate(messages_list)
        ]

        batch = self._client.messages.batches.create(requests=requests)
        batch_id = batch.id
        print(f"  [batch] submitted {len(requests)} requests → {batch_id}", flush=True)

        deadline = time.monotonic() + _BATCH_TIMEOUT
        interval = _BATCH_POLL_INITIAL
        while time.monotonic() < deadline:
            time.sleep(interval)
            batch = self._client.messages.batches.retrieve(batch_id)
            counts = batch.request_counts
            print(
                f"  [batch] {batch_id}: processing={counts.processing} "
                f"succeeded={counts.succeeded} errored={counts.errored}",
                flush=True,
            )
            if batch.processing_status == "ended":
                break
            interval = min(interval * 1.5, _BATCH_POLL_MAX)
        else:
            raise TimeoutError(f"Batch {batch_id} did not complete within {_BATCH_TIMEOUT}s")

        results = [""] * len(messages_list)
        for result in self._client.messages.batches.results(batch_id):
            idx = int(result.custom_id)
            if result.result.type == "succeeded":
                results[idx] = result.result.message.content[0].text
        return results

