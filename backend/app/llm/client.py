import json
from typing import Any, Protocol

import anthropic
import httpx


class JsonMessageClient(Protocol):
    def json_message(self, system: str, prompt: str, max_tokens: int = 1200) -> tuple[dict[str, Any], int, int]: ...


class ClaudeClient:
    def __init__(self, api_key: str, model: str, timeout: float, retries: int) -> None:
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=retries)

    def json_message(self, system: str, prompt: str, max_tokens: int = 1200) -> tuple[dict[str, Any], int, int]:
        response = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": prompt}])
        text = "".join(block.text for block in response.content if block.type == "text")
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise TypeError("model response must be a JSON object")
        return payload, response.usage.input_tokens, response.usage.output_tokens


class GroqClient:
    def __init__(self, api_key: str, endpoint: str, model: str, timeout: float) -> None:
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.timeout = timeout

    def json_message(self, system: str, prompt: str, max_tokens: int = 1200) -> tuple[dict[str, Any], int, int]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.model.startswith("openai/gpt-oss-"):
            payload["reasoning_effort"] = "low"

        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.endpoint}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        text = data["choices"][0]["message"]["content"]
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise TypeError("model response must be a JSON object")
        usage = data.get("usage", {})
        return payload, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
