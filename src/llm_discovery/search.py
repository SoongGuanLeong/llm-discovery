"""Web search backends for the LLM judge.

Tavily is **not** required.  Web search is **opt-in** — by default the judge
operates on deterministic facts (AA catalog + models.dev metadata + its own
training knowledge).  When `ENABLE_WEB_SEARCH=1` is set the pipeline activates
DuckDuckGo (no key) or Brave (optional `BRAVE_API_KEY`, $5 free credits/mo).

When DuckDuckGo rate-limits, SearXNG is tried as fallback.
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

SEARCH_HEADERS = {"User-Agent": "llm-discovery/1.0 (contact@example.com)"}
TIMEOUT = 30.0

# Default max_results; can be overridden via SEARCH_MAX_RESULTS env var
DEFAULT_MAX_RESULTS = 3


# --------------------------------------------------------------------------- #
# No-key backend: DuckDuckGo HTML                                                #
# --------------------------------------------------------------------------- #
class DuckDuckGoSearcher:
    """Free, no-key web search via DuckDuckGo's HTML endpoint.

    Returns up to *max_results* results in the standard dict shape
    (title / url / snippet).  Failures degrade gracefully to an empty list
    so the judge never crashes on a network or parsing error.
    """

    URL = "https://html.duckduckgo.com/html/"

    def __init__(self, max_results: int = DEFAULT_MAX_RESULTS, timeout: float = TIMEOUT) -> None:
        self.max_results = max_results
        self.timeout = timeout

    def search(self, query: str) -> list[dict[str, Any]]:
        try:
            resp = httpx.get(
                self.URL,
                params={"q": query, "kl": "us-en"},
                headers=SEARCH_HEADERS,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except Exception:  # noqa: BLE001 - any transport/parse error -> empty
            return []

        return self._parse_html(resp.text)[: self.max_results]

    @staticmethod
    def _parse_html(html: str) -> list[dict[str, Any]]:
        # DuckDuckGo HTML wraps each result in a <div class="result ...">
        result_re = re.compile(
            r'<div class="result\\s+.*?".*?>.*?<a rel="nofollow" class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'<a class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        results: list[dict[str, Any]] = []
        for match in result_re.finditer(html):
            url = match.group(1)
            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()
            results.append({"title": title, "url": url, "snippet": snippet[:1000]})
        return results


# --------------------------------------------------------------------------- #
# Optional paid backend: Brave                                                 #
# --------------------------------------------------------------------------- #
class BraveSearcher:
    """Web search via the Brave Search API (free $5/mo credits)."""

    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, max_results: int = DEFAULT_MAX_RESULTS, timeout: float = TIMEOUT) -> None:
        self.api_key = api_key
        self.max_results = max_results
        self.timeout = timeout

    def search(self, query: str) -> list[dict[str, Any]]:
        try:
            resp = httpx.get(
                self.URL,
                params={"q": query, "count": self.max_results},
                headers={"X-Subscription-Token": self.api_key},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:  # noqa: BLE001 - degrade to empty
            return []

        return [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("description", "")[:1000],
            }
            for r in data.get("web", {}).get("results", [])
        ]


# --------------------------------------------------------------------------- #
# Free metasearch backend: SearXNG                                             #
# --------------------------------------------------------------------------- #
class SearXNGSearcher:
    """Free metasearch via public SearXNG instances.

    Uses a rotating list of public SearXNG instances for redundancy.
    Failures degrade gracefully to an empty list so the judge never crashes.
    """

    INSTANCES = [
        "https://searx.tiekoetter.com",
        "https://search.sapti.me",
        "https://searx.epicyle.dev",
        "https://searx.be",
    ]

    def __init__(self, max_results: int = DEFAULT_MAX_RESULTS, timeout: float = TIMEOUT) -> None:
        self.max_results = max_results
        self.timeout = timeout
        self._instance_index = 0

    def _get_next_instance(self) -> str:
        """Get next SearXNG instance with simple round-robin."""
        instance = self.INSTANCES[self._instance_index]
        self._instance_index = (self._instance_index + 1) % len(self.INSTANCES)
        return instance

    def search(self, query: str) -> list[dict[str, Any]]:
        # Try each instance until one works
        for _ in range(len(self.INSTANCES)):
            instance = self._get_next_instance()
            try:
                resp = httpx.get(
                    f"{instance}/search",
                    params={"q": query, "format": "json"},
                    headers=SEARCH_HEADERS,
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()

                results = []
                for result in data.get("results", [])[:self.max_results]:
                    results.append({
                        "title": result.get("title", ""),
                        "url": result.get("url", ""),
                        "snippet": result.get("content", "")[:1000],
                    })

                if results:
                    return results

            except Exception:  # noqa: BLE001 - try next instance
                continue

        # All instances failed
        return []


# --------------------------------------------------------------------------- #
# No-op backend (offline fallback)                                             #
# --------------------------------------------------------------------------- #
class NoopSearcher:
    """No web-search backend is configured.

    The judge still runs: it falls back to the Artificial Analysis intelligence
    index (already supplied in the prompt) plus the provider model metadata and
    its own training knowledge.
    """

    def search(self, query: str) -> list[dict[str, Any]]:
        return []


# --------------------------------------------------------------------------- #
# Fallback wrapper                                                              #
# --------------------------------------------------------------------------- #
class _SearchWithFallback:
    """Wrapper that tries primary, falls back to secondary on empty/error."""

    def __init__(self, primary: Any, fallback: Any) -> None:
        self._primary = primary
        self._fallback = fallback

    def search(self, query: str) -> list[dict[str, Any]]:
        try:
            results = self._primary.search(query)
            if results:
                return results
        except Exception:  # noqa: BLE001 - any error triggers fallback
            pass
        return self._fallback.search(query)


# --------------------------------------------------------------------------- #
# Factory                                                                      #
# --------------------------------------------------------------------------- #
def make_searcher(
    brave_api_key: str | None = None,
    disabled: bool | None = None,
    prefer: str | None = None,
) -> Any:
    """Return a search backend.

    Priority (unless disabled=True):
    1. BraveSearcher -- if BRAVE_API_KEY is set (higher quality).
    2. DuckDuckGoSearcher -- no key required, SearXNG fallback on empty/429.

    When disabled=True (or DISABLE_WEB_SEARCH=1 in env), returns NoopSearcher.
    When prefer="searxng", SearXNG is used initially with DDG as fallback.

    The returned object has a search(query) -> list[dict] method matching
    the shape the judge loop expects.

    Environment:
    - SEARCH_MAX_RESULTS: default 3, controls backend output size (1-10).
    - DISABLE_WEB_SEARCH=1: forces NoopSearcher.
    """
    # Check DISABLE_WEB_SEARCH env first
    if disabled is None:
        disabled = os.environ.get("DISABLE_WEB_SEARCH") == "1"

    if disabled:
        return NoopSearcher()

    # Read max_results from env
    try:
        max_results = int(os.environ.get("SEARCH_MAX_RESULTS", str(DEFAULT_MAX_RESULTS)))
    except (ValueError, TypeError):
        max_results = DEFAULT_MAX_RESULTS

    # Clamp to reasonable bounds
    max_results = min(max(1, max_results), 10)

    # If SearXNG is preferred, use it with DDG as fallback
    if prefer == "searxng":
        searxng = SearXNGSearcher(max_results=max_results)
        ddg = DuckDuckGoSearcher(max_results=max_results)
        return _SearchWithFallback(searxng, ddg)

    # Brave wins when key present
    if brave_api_key:
        return BraveSearcher(brave_api_key, max_results=max_results)

    # Default: DuckDuckGo with SearXNG fallback
    ddg = DuckDuckGoSearcher(max_results=max_results)
    searxng = SearXNGSearcher(max_results=max_results)
    return _SearchWithFallback(ddg, searxng)
