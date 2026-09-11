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
        api_key: str | None = None,
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
        """POST chat completion with retry for transient failures.

        Retries 429/503/502/529 plus httpx timeout/transport errors with
        exponential backoff (10 -> 20 -> 40s, capped at 60s, honors
        Retry-After). After exhausting retries the final response is
        returned; transport errors are re-raised as the last exception.
        Applies to all judges — local (LM Studio/Ollama/vLLM) and remote.
        """
        url = f"{self.base_url}/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "temperature": 0,
            "max_tokens": 4096,
            "tool_choice": "none" if disable_tools else "auto",
        }
        # When tools disabled we are demanding final JSON — hint strongly
        # for models that support response_format (ignored by those that do not).
        if disable_tools:
            payload["response_format"] = {"type": "json_object"}

        # Retryable HTTP statuses (rate-limit / overloaded / gateway)
        retry_statuses = (429, 503, 502, 529, 408)
        backoff = 10
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                response = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.NetworkError, httpx.TransportError) as exc:
                last_exc = exc
                # Timeout / transport error — retry with backoff
                if attempt < 3:
                    wait = backoff
                    # small jitter so concurrent workers don't thundering-herd
                    try:
                        print(f"[judge] transient transport error attempt {attempt+1}/4: {type(exc).__name__}: {exc} -> retry in {wait}s")
                    except Exception:
                        pass
                    time.sleep(min(wait, 60))
                    backoff = min(backoff * 2, 60)
                    continue
                raise
            # HTTP-level retry
            if response.status_code not in retry_statuses:
                return response
            retry_after = response.headers.get("retry-after")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else backoff
            if attempt < 3:
                try:
                    print(f"[judge] HTTP {response.status_code} attempt {attempt+1}/4 -> retry in {min(wait,60)}s")
                except Exception:
                    pass
                time.sleep(min(wait, 60))
            backoff = min(backoff * 2, 60)
        # Exhausted HTTP retries — return last response (caller handles 429/503)
        # If we exhausted transport retries, last_exc would have been raised.
        return response
