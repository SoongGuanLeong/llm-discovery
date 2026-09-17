"""Judge LLM HTTP transport with bounded retry/backoff and categorized errors (issue #233).

post_chat POSTs a chat completion and retries *transient* failures with
bounded exponential backoff (10s -> 20s -> 40s, cap 60s, per-attempt jitter
to keep concurrent workers from thundering-herding).  429 responses honor
the Retry-After header (exact wait, capped at 60s) so the server's own
rate-limit window is respected instead of re-colliding.

Every failure that escapes the transport is raised as a JudgeError carrying:
  - category   : per-category classification (timeout, connection failure,
                 rate-limit 429, server 5xx, auth, validation, malformed json,
                 tool failure, unknown)
  - retry_count: how many retries were performed before giving up
  - retryable  : whether the failure class is transient (server-side)

Persistent failures stay explicit retryable-or-not errors; callers never
conflate them with a weak evaluation (decision=error, not weak/drop/uncertain).
Applies to all judges - local (LM Studio/Ollama/vLLM) and remote.
"""

import random
import time
from typing import Any

import httpx

# --- Per-category classification (issue #233) --------------------------------
# Category constants are the canonical error_category values persisted on
# judge-failure records.  They are stable names, not free-text.
CAT_TIMEOUT = "judge_timeout"
CAT_CONNECTION = "judge_connection_failure"
CAT_RATE_LIMIT = "judge_429"
CAT_SERVER_5XX = "judge_5xx"
CAT_AUTH = "judge_auth"
CAT_VALIDATION = "judge_validation_failure"
CAT_MALFORMED_JSON = "judge_malformed_json"
CAT_TOOL = "judge_tool_failure"
CAT_UNKNOWN = "judge_unknown"

# Categories a failure in is transient (server-side) and worth an alternate
# judge route when one is configured (LocalLLMEvaluator.alternate).
RETRYABLE_CATEGORIES = {CAT_TIMEOUT, CAT_CONNECTION, CAT_RATE_LIMIT, CAT_SERVER_5XX}

# Bounded backoff: 10s -> 20s -> 40s, capped at 60s (issue #233 AC: no retry
# storms).  Total attempts = initial + 3 retries.
BACKOFF_BASE = 10
BACKOFF_CAP = 60
MAX_RETRIES = 3

# Transient HTTP statuses that trigger a retry (rate-limit / overloaded / gateway).
RETRY_STATUSES = {429, 408, 500, 502, 503, 504, 529}
# Auth failures are non-retryable: retrying will not fix a bad credential.
AUTH_STATUSES = {401, 403}

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


class JudgeError(Exception):
    """Categorized judge failure (issue #233).

    Raised by JudgeTransport and LocalLLMEvaluator.  Carries machine-readable
    classification so records can persist error_category / retry_count
    without re-parsing the message.
    """

    def __init__(
        self,
        message: str,
        *,
        category: str = CAT_UNKNOWN,
        retry_count: int = 0,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.retry_count = int(retry_count)
        self.retryable = bool(retryable)
        self.status_code = status_code
        self.retry_after = retry_after
        # Set by LocalLLMEvaluator when the configured alternate judge route
        # was also attempted (issue #233: consider alternate judge route).
        self.alternate_attempted: bool = False
        self.alternate_category: str | None = None


def _classify_status(status_code: int) -> str:
    """Map an HTTP status to its judge-failure category."""
    if status_code in (408, 429):
        return CAT_RATE_LIMIT
    if status_code in AUTH_STATUSES:
        return CAT_AUTH
    if 500 <= status_code < 600:
        return CAT_SERVER_5XX
    if status_code == 404:
        # Unknown route/model on the judge: not transient.
        return CAT_UNKNOWN
    return CAT_VALIDATION


def _classify_transport_exception(exc: Exception) -> str:
    """Map an httpx transport exception to its judge-failure category."""
    if isinstance(exc, httpx.TimeoutException):
        return CAT_TIMEOUT
    return CAT_CONNECTION


def _log(category: str, message: str) -> None:
    """Per-category logging for judge failures (issue #233 AC: classification + logging)."""
    try:
        print("[judge:%s] %s" % (category, message))
    except Exception:
        pass


class JudgeTransport:
    """HTTP transport for judge LLM with bounded retry/backoff (issue #233)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def post_chat(
        self,
        messages: list[dict[str, Any]],
        disable_tools: bool = False,
    ) -> httpx.Response:
        """POST chat completion with bounded retry for transient failures.

        - Transient statuses (429/408/5xx) and httpx timeout/transport errors
          are retried with exponential backoff (10 -> 20 -> 40s, cap 60s,
          +/-10% jitter), honoring Retry-After on rate-limit responses.
        - Non-retryable statuses (401/403 auth, other 4xx validation, 404)
          abort immediately and raise a categorized JudgeError.
        - After exhausting retries, raises a JudgeError carrying category and
          retry_count so callers persist an explicit error record
          (decision=error, tier=error, evidence_level=none) instead of
          conflating the failure with a weak verdict.

        Returns the 2xx httpx.Response on success.
        """
        url = "%s/chat/completions" % self.base_url
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer %s" % self.api_key
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": TOOLS,
            "temperature": 0,
            "max_tokens": 4096,
            "tool_choice": "none" if disable_tools else "auto",
        }
        # When tools disabled we are demanding final JSON - hint strongly
        # for models that support response_format (ignored by those that do not).
        if disable_tools:
            payload["response_format"] = {"type": "json_object"}

        backoff = BACKOFF_BASE
        attempts = MAX_RETRIES + 1  # initial + retries
        last_status: int | None = None
        last_retry_after: float | None = None

        for attempt in range(attempts):
            category: str
            status_code: int | None = None
            retry_after: float | None = None
            try:
                response = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.NetworkError, httpx.TransportError) as exc:
                # Timeout / connection / transport error - transient, retry with backoff.
                category = _classify_transport_exception(exc)
                if attempt < MAX_RETRIES:
                    wait = self._jitter(backoff)
                    _log(
                        category,
                        "transient transport error attempt %d/%d: %s: %s -> retry in %.1fs"
                        % (attempt + 1, attempts, type(exc).__name__, exc, wait),
                    )
                    time.sleep(wait)
                    backoff = min(backoff * 2, BACKOFF_CAP)
                    continue
                _log(
                    category,
                    "exhausted %d retries on transport error: %s: %s"
                    % (MAX_RETRIES, type(exc).__name__, exc),
                )
                raise JudgeError(
                    "Judge transport error after %d retries: %s: %s"
                    % (MAX_RETRIES, type(exc).__name__, exc),
                    category=category,
                    retry_count=MAX_RETRIES,
                    retryable=True,
                ) from exc

            status_code = response.status_code
            last_status = status_code
            if 200 <= status_code < 300:
                return response

            category = _classify_status(status_code)
            if status_code in AUTH_STATUSES:
                # Auth failure is not transient - retrying will not fix it.
                _log(category, "HTTP %d auth failure (no retry): %s" % (status_code, self._short_body(response)))
                raise JudgeError(
                    "Judge HTTP %d authentication failure" % status_code,
                    category=category,
                    retry_count=attempt,
                    retryable=False,
                    status_code=status_code,
                )

            if status_code in RETRY_STATUSES:
                # Honor Retry-After exactly when the server sends a numeric one;
                # otherwise fall back to the jittered exponential schedule.
                retry_after = self._retry_after_seconds(response)
                wait = retry_after if retry_after is not None else self._jitter(backoff)
                if attempt < MAX_RETRIES:
                    last_retry_after = retry_after
                    _log(
                        category,
                        "HTTP %d attempt %d/%d (retry-after=%s) -> retry in %.1fs"
                        % (status_code, attempt + 1, attempts, retry_after, min(wait, BACKOFF_CAP)),
                    )
                    time.sleep(min(wait, BACKOFF_CAP))
                    backoff = min(backoff * 2, BACKOFF_CAP)
                    continue
                _log(category, "exhausted %d retries on HTTP %d (retry-after=%s)" % (MAX_RETRIES, status_code, last_retry_after))
                raise JudgeError(
                    "Judge HTTP %d after %d retries" % (status_code, MAX_RETRIES),
                    category=category,
                    retry_count=MAX_RETRIES,
                    retryable=True,
                    status_code=status_code,
                    retry_after=last_retry_after,
                )

            # Other non-retryable statuses (4xx validation / 404 / 3xx surprise).
            _log(category, "HTTP %d non-retryable failure: %s" % (status_code, self._short_body(response)))
            raise JudgeError(
                "Judge HTTP %d: %s" % (status_code, self._short_body(response)),
                category=category,
                retry_count=attempt,
                retryable=False,
                status_code=status_code,
            )

        # Unreachable: every loop path either returns or raises.
        raise JudgeError(
            "Judge HTTP %s after %d retries" % (last_status, MAX_RETRIES),
            category=_classify_status(last_status or 500),
            retry_count=MAX_RETRIES,
            retryable=True,
            status_code=last_status,
            retry_after=last_retry_after,
        )

    @staticmethod
    def _jitter(wait: float) -> float:
        """Per-attempt +/-10% jitter so concurrent workers do not thundering-herd."""
        return wait * random.uniform(0.9, 1.1)

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> float | None:
        """Numeric Retry-After header in seconds (capped at BACKOFF_CAP), else None."""
        raw = response.headers.get("retry-after")
        if raw and raw.strip().isdigit():
            return min(float(int(raw.strip())), BACKOFF_CAP)
        return None

    @staticmethod
    def _short_body(response: httpx.Response, limit: int = 200) -> str:
        try:
            body = (response.text or "").strip()
        except Exception:
            body = ""
        return body[:limit] or "(empty body)"
