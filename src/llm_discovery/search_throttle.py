"""Global search budget + per-canonical-model search cache for the LLM judge (issue #225).

The judge may run web searches for many models across many providers.  This module
adds two guards around the underlying searcher object (from .search.make_searcher):

1. SearchAccounting -- process-wide budget cap (env SEARCH_GLOBAL_BUDGET, default
   100; 0 or negative = unlimited) with thread-safe counters:
   calls / cache_hits / budget_exhausted.
2. CanonicalSearchCache -- deduplicates results by canonical model id
   (normalize_model_id), so a model reached via two providers costs ONE search.

make_cached_search() returns a CachedSearcher that resolves
  cache hit -> budget -> underlying searcher
in that order, and is both .search(query)-callable and __call__-callable so it can
be passed straight to LocalLLMEvaluator(search_web=...).  Plain callables (lambdas,
bound methods from tests) keep working unchanged: the evaluator only uses
set_model() when the attribute exists.

Active model is tracked THREAD-LOCALLY via set_model(): one shared evaluator serves
the per-model thread pool, where one thread evaluates one model at a time but
several threads evaluate different models at once.  A single slot would let thread
A's model bleed into thread B's searches; thread-local matches the access pattern.

Single-flight: searches for the same canonical key serialize on a per-key lock
(double-checked under the lock), so "two providers, one model = one search" holds
even when both provider threads search simultaneously.

build_all() calls reset_search_accounting() at build start and reports
accounting.snapshot() under telemetry["search"].

The probe-cache default path (PROBE_CACHE_DEFAULT_PATH) also lives here: pipeline.py
must not reference derived artifacts (boundary: tests/test_cache_db.py).
"""
from __future__ import annotations

import os
import threading
from datetime import UTC, datetime
from typing import Any

DEFAULT_SEARCH_BUDGET = 100
ENV_SEARCH_BUDGET = "SEARCH_GLOBAL_BUDGET"

# Default probe cache location (issue #225). Lives here instead of pipeline.py because
# pipeline must not reference derived artifacts (boundary: tests/test_cache_db.py).
PROBE_CACHE_DEFAULT_PATH = "data/derived/probe_cache.json"


def _budget_from_env() -> int | None:
    """Cap from env SEARCH_GLOBAL_BUDGET; default 100; 0 or negative = unlimited (None)."""
    raw = os.environ.get(ENV_SEARCH_BUDGET)
    if raw is None:
        return DEFAULT_SEARCH_BUDGET
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return DEFAULT_SEARCH_BUDGET
    return value if value > 0 else None


class SearchAccounting:
    """Thread-safe search counters + budget cap for one build."""

    def __init__(self, budget: int | None = None) -> None:
        if budget is None:
            budget = _budget_from_env()
        if budget is not None and budget <= 0:
            budget = None
        self.budget = budget  # None = unlimited
        self.calls = 0
        self.cache_hits = 0
        self.budget_exhausted = 0
        self._lock = threading.Lock()
        self._remaining = budget if budget is not None and budget > 0 else None

    def acquire(self) -> bool:
        """Consume one budget slot; always True when unlimited."""
        with self._lock:
            if self._remaining is None:
                return True
            if self._remaining <= 0:
                return False
            self._remaining -= 1
            return True

    def bump(self, name: str) -> None:
        """Increment one counter by 1 (name in calls|cache_hits|budget_exhausted)."""
        with self._lock:
            if name == "calls":
                self.calls += 1
            elif name == "cache_hits":
                self.cache_hits += 1
            elif name == "budget_exhausted":
                self.budget_exhausted += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": self.calls,
                "cache_hits": self.cache_hits,
                "budget_exhausted": self.budget_exhausted,
                "budget": self.budget,
            }


class CanonicalSearchCache:
    """Thread-safe dict canonical_id -> (results, ts_iso)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[list[dict[str, Any]], str]] = {}

    @staticmethod
    def canonical_key(model_id: str) -> str:
        # Lazy import keeps the import graph shallow (model_matching is heavy).
        from .model_matching import normalize_model_id

        return normalize_model_id(str(model_id))

    def get(self, canonical_id: str) -> list[dict[str, Any]] | None:
        with self._lock:
            entry = self._entries.get(canonical_id)
        return entry[0] if entry is not None else None

    def put(self, canonical_id: str, results: list[dict[str, Any]]) -> None:
        ts = datetime.now(UTC).isoformat()
        with self._lock:
            self._entries[canonical_id] = (results, ts)



class CachedSearcher:
    """search(query) wrapper: canonical cache -> budget -> underlying searcher."""

    def __init__(self, accounting: SearchAccounting, cache: CanonicalSearchCache, searcher: Any) -> None:
        self.accounting = accounting
        self.cache = cache
        self._searcher = searcher
        self._local = threading.local()  # active model per thread
        self._master_lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def set_model(self, model_id: str | None) -> None:
        """Set the active model for THIS thread (thread-local)."""
        self._local.model = model_id

    def _active_model(self) -> str | None:
        return getattr(self._local, "model", None)

    def _lock_for(self, key: str) -> threading.Lock:
        with self._master_lock:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._key_locks[key] = lock
            return lock

    def _search_uncached(self, query: str) -> list[dict[str, Any]]:
        if not self.accounting.acquire():
            self.accounting.bump("budget_exhausted")
            return []
        results = self._searcher.search(query)
        self.accounting.bump("calls")
        return results

    def search(self, query: str) -> list[dict[str, Any]]:
        model = self._active_model()
        if model is None:
            # No model context (should not happen in the pipeline) -> pass through.
            return self._search_uncached(query)
        key = self.cache.canonical_key(model)
        cached = self.cache.get(key)
        if cached is not None:
            self.accounting.bump("cache_hits")
            return cached
        # Single-flight: one underlying call per canonical key per build.
        with self._lock_for(key):
            cached = self.cache.get(key)
            if cached is not None:
                self.accounting.bump("cache_hits")
                return cached
            if not self.accounting.acquire():
                self.accounting.bump("budget_exhausted")
                return []
            results = self._searcher.search(query)
            self.accounting.bump("calls")
            self.cache.put(key, results)
            return results

    def __call__(self, query: str) -> list[dict[str, Any]]:
        return self.search(query)


def make_cached_search(accounting: SearchAccounting, cache: CanonicalSearchCache, searcher: Any) -> CachedSearcher:
    """Wrap a searcher object (having .search) with the budget + canonical cache."""
    return CachedSearcher(accounting, cache, searcher)


# Module-global pair for the current build (mirrors the _secrets_loaded pattern in
# secrets.py: one shared state per process, explicit reset at build start / tests).
_state: tuple[SearchAccounting, CanonicalSearchCache] | None = None
_state_lock = threading.Lock()


def get_search_accounting() -> tuple[SearchAccounting, CanonicalSearchCache]:
    """Module-global (accounting, cache) pair; created on first use."""
    global _state
    with _state_lock:
        if _state is None:
            _state = (SearchAccounting(), CanonicalSearchCache())
        return _state


def reset_search_accounting() -> None:
    """Fresh (accounting, cache) pair — build start and tests."""
    global _state
    with _state_lock:
        _state = (SearchAccounting(), CanonicalSearchCache())
