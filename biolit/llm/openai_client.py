"""OpenAI (and OpenAI-compatible) client."""
import io
import json
import time
from biolit.llm.base import BaseLLMClient, resolve_api_key

_BATCH_POLL_INITIAL = 5
_BATCH_POLL_MAX     = 30
_BATCH_TIMEOUT      = 6 * 3600


class OpenAIClient(BaseLLMClient):
    def __init__(self, model: str, api_key: str | None = None, base_url: str | None = None):
        super().__init__(model)
        try:
            import openai
        except ImportError:
            raise ImportError("Install openai: pip install openai")
        api_key = api_key or resolve_api_key("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "OPENAI_API_KEY not found in macOS keychain or environment"
            )
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.OpenAI(**kwargs)
        self._base_url = base_url  # non-standard endpoints may not support batch API

    def chat(self, messages: list[dict], max_tokens: int = 512) -> str:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content

    def chat_batch(self, messages_list: list[list[dict]], max_tokens: int = 512) -> list[str]:
        """Submit all messages as an OpenAI batch job (50 % cheaper).

        Falls back to sequential calls if a non-standard base_url is set
        (e.g. Azure, local vLLM), since those may not support the batch API.
        Blocks until the batch completes or *_BATCH_TIMEOUT* is reached.
        Failed / expired individual requests return an empty string.
        """
        if not messages_list:
            return []
        if self._base_url:
            # Non-standard endpoints likely don't support batch API
            return super().chat_batch(messages_list, max_tokens)

        lines = [
            json.dumps({
                "custom_id": str(i),
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": self.model,
                    "messages": msgs,
                    "max_tokens": max_tokens,
                },
            })
            for i, msgs in enumerate(messages_list)
        ]
        jsonl_bytes = "\n".join(lines).encode()

        file_obj = self._client.files.create(
            file=("batch.jsonl", io.BytesIO(jsonl_bytes), "application/jsonl"),
            purpose="batch",
        )
        batch = self._client.batches.create(
            input_file_id=file_obj.id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
        )
        batch_id = batch.id
        print(f"  [batch] submitted {len(lines)} requests → {batch_id}", flush=True)

        deadline = time.monotonic() + _BATCH_TIMEOUT
        interval = _BATCH_POLL_INITIAL
        terminal = {"completed", "failed", "cancelled", "expired"}
        while time.monotonic() < deadline:
            time.sleep(interval)
            batch = self._client.batches.retrieve(batch_id)
            print(
                f"  [batch] {batch_id}: status={batch.status} "
                f"completed={batch.request_counts.completed}/"
                f"{batch.request_counts.total}",
                flush=True,
            )
            if batch.status in terminal:
                break
            interval = min(interval * 1.5, _BATCH_POLL_MAX)
        else:
            raise TimeoutError(f"Batch {batch_id} did not complete within {_BATCH_TIMEOUT}s")

        results = [""] * len(messages_list)
        if batch.output_file_id:
            content = self._client.files.content(batch.output_file_id)
            for line in content.text.splitlines():
                obj = json.loads(line)
                idx = int(obj["custom_id"])
                if obj["response"]["status_code"] == 200:
                    results[idx] = obj["response"]["body"]["choices"][0]["message"]["content"]
        return results

