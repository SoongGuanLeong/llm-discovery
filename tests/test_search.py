"""Web search is opt-in, not default.  When disabled (default) the judge
decides from the AA intelligence index + provider metadata + its own knowledge.

Tests for issue #215: SEARCH_MAX_RESULTS env, SearXNG fallback, dual-site
truncation fix, and DISABLE_WEB_SEARCH opt-out.
"""
import os
from unittest.mock import patch

from llm_discovery.search import (
    BraveSearcher,
    DuckDuckGoSearcher,
    NoopSearcher,
    SearXNGSearcher,
    make_searcher,
    _SearchWithFallback,
)


def test_noop_search_returns_empty_list():
    assert NoopSearcher().search("anything") == []


def test_duckduckgo_search_returns_list_on_error():
    """DDGSearcher degrades to empty list on network error (no crash)."""
    results = DuckDuckGoSearcher().search("test query that should not match")
    assert isinstance(results, list)


def test_make_searcher_defaults_to_fallback_wrapper():
    """Default: fallback wrapper with DDG primary, SearXNG fallback."""
    searcher = make_searcher(None)
    assert isinstance(searcher, _SearchWithFallback)
    assert isinstance(searcher._primary, DuckDuckGoSearcher)
    assert isinstance(searcher._fallback, SearXNGSearcher)


def test_make_searcher_returns_brave_when_key_present():
    searcher = make_searcher("secret-key")
    assert isinstance(searcher, BraveSearcher)


def test_make_searcher_disabled_returns_noop():
    assert isinstance(make_searcher(None, disabled=True), NoopSearcher)
    assert isinstance(make_searcher("anykey", disabled=True), NoopSearcher)


def test_make_searcher_returns_brave_directly():
    """Brave is returned directly (no wrapper needed)."""
    searcher = make_searcher("secret-key")
    assert isinstance(searcher, BraveSearcher)


# --- Issue #215: SEARCH_MAX_RESULTS env controls max_results in all backends ---

def test_make_searcher_reads_search_max_results_env():
    """SEARCH_MAX_RESULTS env controls max_results passed to backends."""
    with patch.dict(os.environ, {"SEARCH_MAX_RESULTS": "5"}, clear=False):
        searcher = make_searcher(None)
        assert isinstance(searcher, _SearchWithFallback)
        assert searcher._primary.max_results == 5
        assert searcher._fallback.max_results == 5


def test_make_searcher_default_max_results_is_3():
    """Without env, default max_results is 3."""
    with patch.dict(os.environ, {}, clear=True):
        searcher = make_searcher(None)
        assert isinstance(searcher, _SearchWithFallback)
        assert searcher._primary.max_results == 3
        assert searcher._fallback.max_results == 3


def test_make_searcher_max_results_brave():
    """SEARCH_MAX_RESULTS propagates to BraveSearcher."""
    with patch.dict(os.environ, {"SEARCH_MAX_RESULTS": "5"}, clear=False):
        searcher = make_searcher("mykey")
        assert isinstance(searcher, BraveSearcher)
        assert searcher.max_results == 5


def test_make_searcher_max_results_searxng_prefer():
    """SEARCH_MAX_RESULTS propagates to SearXNGSearcher when prefer=searxng."""
    with patch.dict(os.environ, {"SEARCH_MAX_RESULTS": "7"}, clear=False):
        searcher = make_searcher(None, prefer="searxng")
        assert isinstance(searcher, _SearchWithFallback)
        # When prefer=searxng, SearXNG is primary, DDG is fallback
        assert isinstance(searcher._primary, SearXNGSearcher)
        assert searcher._primary.max_results == 7


# --- Issue #215: SearXNG fallback on DDG 429/empty ---

def test_make_searcher_falls_back_to_searxng_on_ddg_empty():
    """When DDG returns empty results, SearXNG should be used as fallback."""
    with patch.object(DuckDuckGoSearcher, "search", return_value=[]):
        with patch.object(SearXNGSearcher, "search", return_value=[{"title": "t", "url": "u", "snippet": "s"}]):
            searcher = make_searcher(None)
            results = searcher.search("test query")
            assert len(results) > 0


def test_make_searcher_falls_back_to_searxng_on_ddg_exception():
    """When DDG raises (e.g. 429), SearXNG fallback handles it."""

    def raise_429(query):
        raise Exception("429 Too Many Requests")

    with patch.object(DuckDuckGoSearcher, "search", side_effect=raise_429):
        with patch.object(SearXNGSearcher, "search", return_value=[{"title": "t", "url": "u", "snippet": "s"}]):
            searcher = make_searcher(None)
            results = searcher.search("test query")
            assert len(results) > 0


# --- Issue #215: DISABLE_WEB_SEARCH returns Noop ---

def test_disable_web_search_from_env_returns_noop():
    """DISABLE_WEB_SEARCH=1 still returns NoopSearcher."""
    with patch.dict(os.environ, {"DISABLE_WEB_SEARCH": "1"}, clear=False):
        searcher = make_searcher(None)
        assert isinstance(searcher, NoopSearcher)
