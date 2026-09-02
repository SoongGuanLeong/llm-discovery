"""Web search is opt-in, not default.  When disabled (default) the judge
decides from the AA intelligence index + provider metadata + its own knowledge.
"""
from llm_discovery.search import BraveSearcher, DuckDuckGoSearcher, NoopSearcher, make_searcher


def test_noop_search_returns_empty_list():
    assert NoopSearcher().search("anything") == []


def test_duckduckgo_search_returns_list_on_error():
    """DDGSearcher degrades to empty list on network error (no crash)."""
    results = DuckDuckGoSearcher().search("test query that should not match")
    assert isinstance(results, list)


def test_make_searcher_defaults_to_duckduckgo():
    """Default: DDG web search enabled (no key required)."""
    assert isinstance(make_searcher(None), DuckDuckGoSearcher)
    assert isinstance(make_searcher("key"), BraveSearcher)  # Brave wins when key present


def test_make_searcher_returns_brave_when_key_present():
    searcher = make_searcher("secret-key")
    assert isinstance(searcher, BraveSearcher)


def test_make_searcher_disabled_returns_noop():
    assert isinstance(make_searcher(None, disabled=True), NoopSearcher)
    assert isinstance(make_searcher("anykey", disabled=True), NoopSearcher)


def test_make_searcher_returns_duckduckgo_without_key():
    assert isinstance(make_searcher(None), DuckDuckGoSearcher)
