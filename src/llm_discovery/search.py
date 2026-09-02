"""Web search backends for the LLM judge.

Tavily is **not** required.  Web search is **opt-in** — by default the judge
operates on deterministic facts (AA catalog + models.dev metadata + its own
training knowledge).  When ``ENABLE_WEB_SEARCH=1`` is set the pipeline activates
DuckDuckGo (no key) or Brave (optional ``BRAVE_API_KEY``, $5 free credits/mo).

The judge always runs: when search is disabled or unavailable it gets `[]` and
falls back to the AA intelligence index plus provider metadata and its own
training knowledge.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

SEARCH_HEADERS = {"User-Agent": "llm-discovery/1.0 (contact@example.com)"}
TIMEOUT = 30.0


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

    def __init__(self, max_results: int = 3, timeout: float = TIMEOUT) -> None:
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
        except Exception:  # noqa: BLE001 — any transport/parse error → empty
            return []

        return self._parse_html(resp.text)[: self.max_results]

    @staticmethod
    def _parse_html(html: str) -> list[dict[str, Any]]:
        # DuckDuckGo HTML wraps each result in a <div class="result ...">
        result_re = re.compile(
            r'<div class="result\s+.*?".*?>.*?<a rel="nofollow" class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?'
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

    def __init__(self, api_key: str, max_results: int = 3, timeout: float = TIMEOUT) -> None:
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
        except Exception:  # noqa: BLE001 — degrade to empty
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
# Factory                                                                      #
# --------------------------------------------------------------------------- #
def make_searcher(
    brave_api_key: str | None = None,
    disabled: bool = False,
) -> Any:
    """Return a search backend.

    Default is DuckDuckGo (no key, works out of the box).  Set
    ``disabled=True`` (``DISABLE_WEB_SEARCH=1``) to use NoopSearcher instead.

    Priority:
    1. BraveSearcher  — if ``BRAVE_API_KEY`` is present (higher quality).
    2. DuckDuckGoSearcher — no key required, degrades to empty on error.
    3. NoopSearcher  — when disabled.

    The returned object has a ``search(query) -> list[dict]`` method matching
    the shape the judge loop expects.
    """
    if disabled:
        return NoopSearcher()

    if brave_api_key:
        return BraveSearcher(brave_api_key)

    return DuckDuckGoSearcher()
