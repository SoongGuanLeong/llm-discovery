"""Issue #225: global search budget + per-canonical-model search cache, and 7d probe cache."""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from llm_discovery import pipeline
from llm_discovery.model_matching import normalize_model_id
from llm_discovery.search_throttle import (
    SearchAccounting,
    get_search_accounting,
    make_cached_search,
    reset_search_accounting,
)


class CountingSearcher:
    def __init__(self):
        self.calls = 0

    def search(self, query: str):
        self.calls += 1
        return [
            {
                "title": f"r{self.calls}",
                "url": f"https://example.com/{self.calls}",
                "snippet": query,
            }
        ]


class TestCanonicalSearchCache:
    def test_setup_resets_module_state(self):
        reset_search_accounting()
        a1, c1 = get_search_accounting()
        a2, c2 = get_search_accounting()
        assert a1 is a2 and c1 is c2
        assert a1.calls == 0 and a1.cache_hits == 0 and a1.budget_exhausted == 0
        reset_search_accounting()
        a3, _ = get_search_accounting()
        assert a3 is not a1

    def test_canonical_dedup_across_two_provider_ids(self):
        assert normalize_model_id("openai/gpt-4o") == normalize_model_id("gpt-4o")
        reset_search_accounting()
        accounting, cache = get_search_accounting()
        fake = CountingSearcher()
        wrapped = make_cached_search(accounting, cache, fake)
        wrapped.set_model("openai/gpt-4o")
        r1 = wrapped.search("gpt-4o coding benchmark")
        wrapped.set_model("gpt-4o")
        r2 = wrapped.search("gpt-4o coding benchmark")
        assert fake.calls == 1
        assert r2 == r1
        assert accounting.calls == 1
        assert accounting.cache_hits == 1
        assert accounting.budget_exhausted == 0
        wrapped.set_model("openai/gpt-4o-mini")
        r3 = wrapped.search("gpt-4o-mini coding benchmark")
        assert fake.calls == 2
        assert r3[0]["title"] == "r2"

    def test_no_active_model_counts_but_does_not_cache(self):
        reset_search_accounting()
        accounting, cache = get_search_accounting()
        fake = CountingSearcher()
        wrapped = make_cached_search(accounting, cache, fake)
        r1 = wrapped.search("q")
        r2 = wrapped.search("q")
        assert fake.calls == 2
        assert accounting.calls == 2 and accounting.cache_hits == 0
        assert r1 and r2

    def test_thread_safety_same_model_one_underlying_call(self):
        reset_search_accounting()
        accounting, cache = get_search_accounting()
        fake = CountingSearcher()
        wrapped = make_cached_search(accounting, cache, fake)
        barrier = threading.Barrier(8)

        def worker():
            wrapped.set_model("gpt-4o")
            barrier.wait()
            wrapped.search("gpt-4o coding benchmark")

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert fake.calls == 1
        assert accounting.calls == 1
        assert accounting.cache_hits == 7
        assert accounting.budget_exhausted == 0


class TestSearchBudget:
    def test_budget_default_is_100(self, monkeypatch):
        monkeypatch.delenv("SEARCH_GLOBAL_BUDGET", raising=False)
        assert SearchAccounting().budget == 100

    def test_budget_cap_enforced(self, monkeypatch):
        monkeypatch.setenv("SEARCH_GLOBAL_BUDGET", "1")
        reset_search_accounting()
        accounting, cache = get_search_accounting()
        assert accounting.budget == 1
        fake = CountingSearcher()
        wrapped = make_cached_search(accounting, cache, fake)

        wrapped.set_model("model-a")
        res_a = wrapped.search("a")
        assert res_a  # first search consumes the one budget slot

        wrapped.set_model("model-b")
        res_b = wrapped.search("b")
        assert res_b == []  # denied: budget exhausted

        assert fake.calls == 1
        assert accounting.calls == 1
        assert accounting.budget_exhausted == 1

    def test_budget_zero_means_unlimited(self, monkeypatch):
        monkeypatch.setenv("SEARCH_GLOBAL_BUDGET", "0")
        reset_search_accounting()
        accounting, cache = get_search_accounting()
        assert accounting.budget is None  # unlimited
        fake = CountingSearcher()
        wrapped = make_cached_search(accounting, cache, fake)
        for i in range(5):
            wrapped.set_model(f"model-{i}")
            assert wrapped.search(f"q{i}")
        assert fake.calls == 5
        assert accounting.budget_exhausted == 0

    def test_snapshot_shape(self):
        accounting = SearchAccounting(budget=3)
        snap = accounting.snapshot()
        assert snap == {"calls": 0, "cache_hits": 0, "budget_exhausted": 0, "budget": 3}


# --------------------------------------------------------------------------- #
# build_all integration: telemetry carries the search dict                     #
# --------------------------------------------------------------------------- #
class TestBuildAllSearchTelemetry:
    def test_telemetry_contains_search_dict(self, tmp_path, monkeypatch):
        from llm_discovery.build_all import build_all
        from llm_discovery.config import load_config

        monkeypatch.delenv("SEARCH_GLOBAL_BUDGET", raising=False)
        cfg = load_config(Path("config/providers.yaml"))
        names = [p.name for p in cfg.providers[:2]]

        def discover_fn(name, config, aa, models_dev, max_workers, store=None):
            return {"keep": [], "drop": [], "error": []}

        res = build_all(
            data_dir=tmp_path / "data",
            config_path=Path("config/providers.yaml"),
            provider_names=names,
            discover_fn=discover_fn,
        )
        s = res["telemetry"]["search"]
        assert set(s.keys()) == {"calls", "cache_hits", "budget_exhausted", "budget"}
        assert s["budget"] == 100  # default budget
        assert s["calls"] == 0 and s["cache_hits"] == 0 and s["budget_exhausted"] == 0


# --------------------------------------------------------------------------- #
# AC3: 7d probe cache (httpx monkeypatched, counting fake)                     #
# --------------------------------------------------------------------------- #
class FakeProbeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


@pytest.fixture()
def probe_setup(monkeypatch, tmp_path):
    """Shared probe harness: counting httpx.post + cache file under tmp data/derived."""
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return FakeProbeResponse(402, "payment required")

    monkeypatch.setattr(httpx, "post", fake_post)
    cache_path = tmp_path / "data" / "derived" / "probe_cache.json"
    return SimpleNamespace(calls=calls, cache_path=cache_path)


class TestProbeCache:
    def test_default_path_under_data_derived(self):
        store = pipeline.ProbeCache()
        assert store.path == Path("data/derived/probe_cache.json")
        assert store.path.parts[:2] == ("data", "derived")

    def test_second_call_within_7d_makes_zero_http_calls(self, probe_setup):
        store = pipeline.ProbeCache(probe_setup.cache_path)
        r1 = pipeline._probe_model_is_free("https://api.example.com/v1", "key", "model-a", probe_cache=store)
        assert r1 is False  # 402 -> paid
        assert probe_setup.calls["n"] == 1
        assert probe_setup.cache_path.exists()  # persisted under data/derived

        r2 = pipeline._probe_model_is_free("https://api.example.com/v1", "key", "model-a", probe_cache=store)
        assert r2 is False  # served from cache
        assert probe_setup.calls["n"] == 1  # no new httpx call

    def test_entry_expires_after_7d(self, probe_setup):
        store = pipeline.ProbeCache(probe_setup.cache_path)
        pipeline._probe_model_is_free("https://api.example.com/v1", "key", "model-a", probe_cache=store)
        assert probe_setup.calls["n"] == 1

        # Tamper the stored ts to 8 days ago -> entry is stale -> re-probe
        data = json.loads(probe_setup.cache_path.read_text())
        assert len(data) == 1
        for entry in data.values():
            entry["ts"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
        probe_setup.cache_path.write_text(json.dumps(data))

        fresh_store = pipeline.ProbeCache(probe_setup.cache_path)
        r = pipeline._probe_model_is_free("https://api.example.com/v1", "key", "model-a", probe_cache=fresh_store)
        assert r is False
        assert probe_setup.calls["n"] == 2  # re-probed after TTL expiry

    def test_free_result_cached_and_scoped_per_model(self, probe_setup, monkeypatch):
        def ok_post(url, **kwargs):
            probe_setup.calls["n"] += 1
            return FakeProbeResponse(200, "ok")

        monkeypatch.setattr(httpx, "post", ok_post)
        store = pipeline.ProbeCache(probe_setup.cache_path)
        r1 = pipeline._probe_model_is_free("https://api.example.com/v1", "key", "model-a", probe_cache=store)
        assert r1 is True  # 200 -> free
        assert probe_setup.calls["n"] == 1
        r2 = pipeline._probe_model_is_free("https://api.example.com/v1", "key", "model-a", probe_cache=store)
        assert r2 is True
        assert probe_setup.calls["n"] == 1  # cached
        r3 = pipeline._probe_model_is_free("https://api.example.com/v1", "key", "model-b", probe_cache=store)
        assert r3 is True
        assert probe_setup.calls["n"] == 2  # different model_id -> its own entry
        data = json.loads(probe_setup.cache_path.read_text())
        assert len(data) == 2

    def test_unknown_result_cached_as_null(self, probe_setup, monkeypatch):
        def notfound_post(url, **kwargs):
            probe_setup.calls["n"] += 1
            return FakeProbeResponse(404, "not found")

        monkeypatch.setattr(httpx, "post", notfound_post)
        store = pipeline.ProbeCache(probe_setup.cache_path)
        r1 = pipeline._probe_model_is_free("https://api.example.com/v1", "key", "ghost-model", probe_cache=store)
        assert r1 is None  # 404 -> unknown
        assert probe_setup.calls["n"] == 1
        r2 = pipeline._probe_model_is_free("https://api.example.com/v1", "key", "ghost-model", probe_cache=store)
        assert r2 is None
        assert probe_setup.calls["n"] == 1  # None is cached too
        data = json.loads(probe_setup.cache_path.read_text())
        assert list(data.values())[0]["is_free"] is None

    def test_corrupt_cache_file_warns_but_never_fails(self, probe_setup):
        probe_setup.cache_path.parent.mkdir(parents=True, exist_ok=True)
        probe_setup.cache_path.write_text("{not valid json")
        store = pipeline.ProbeCache(probe_setup.cache_path)
        r = pipeline._probe_model_is_free("https://api.example.com/v1", "key", "model-a", probe_cache=store)
        assert r is False  # proceeded to a real probe
        assert probe_setup.calls["n"] == 1
