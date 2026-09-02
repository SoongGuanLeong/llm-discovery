import time
from typing import Any

import httpx

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": (
                "Search the web for information about an LLM, including model identity, capabilities, and benchmarks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The web search query."
                    }
                },
                "required": ["query"],
            },
        },
    }
]


class JudgeTransport:
    """HTTP transport for judge LLM with retry/backoff."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout: int = 120,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def post_chat(
        self,
        messages: list[dict[str, Any]],
        disable_tools: bool = False,
    ) -> httpx.Response:
        """POST chat completion with retry for transient 429/503.

        Honors Retry-After header when present, otherwise exponential backoff
        (10 -> 20 -> 40s, capped at 60s). After exhausting retries the final
        429/503 response is returned.
        """
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "temperature": 0,
            "max_tokens": 1200,
            "tool_choice": "none" if disable_tools else "auto",
        }

        backoff = 10
        for attempt in range(4):
            response = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
            if response.status_code not in (429, 503):
                return response
            retry_after = response.headers.get("retry-after")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else backoff
            if attempt < 3:
                time.sleep(min(wait, 60))
            backoff = min(backoff * 2, 60)
        return response
