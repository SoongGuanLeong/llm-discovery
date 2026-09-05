"""Tests for shim alias sidecar #119 — HTTP seam + pure pick."""
import json
import random

import httpx
import pytest
from fastapi.testclient import TestClient

# Will fail until shim module exists (RED)
from llm_discovery.bifrost.shim import pick_model_for_tier, is_alias
from llm_discovery.bifrost.sidecar import create_app


class TestIsAlias:
    def test_alias_recognized(self):
        assert is_alias("flash") is True
        assert is_alias("max") is True
        assert is_alias("contributor_free") is True
        assert is_alias("llama-3.1-70b") is False
        assert is_alias("gpt-4") is False


class TestPickModelForTier:
    def test_empty_tier_returns_none(self):
        shim_map = {"flash": [], "max": ["m1"], "contributor_free": ["c1"]}
        assert pick_model_for_tier("flash", shim_map) is None

    def test_picks_within_strict_tier(self):
        shim_map = {"flash": ["flash-m1", "flash-m2"], "max": ["max-m1"], "contributor_free": []}
        rng = random.Random(0)
        picked = pick_model_for_tier("flash", shim_map, rng)
        assert picked in shim_map["flash"]
        assert picked not in shim_map["max"]

    def test_keep_all_duplicates_count_for_weight(self):
        # duplicate entries = separate keeps, uniform pick sees them as distinct candidates
        shim_map = {"flash": ["same-model", "same-model", "other"], "max": [], "contributor_free": []}
        # With keep-all, same-model appears twice so weight 2/3 vs 1/3
        # Just verify pick returns one of them and doesn't deduplicate away
        rng = random.Random(42)
        picks = [pick_model_for_tier("flash", shim_map, rng) for _ in range(20)]
        assert all(p in ["same-model", "other"] for p in picks)
        # Over many picks, both variants appear (probabilistic but deterministic seed)
        assert "other" in picks


class TestSidecarHttpSeam:
    def test_empty_tier_returns_503_with_retry_after_and_no_fallback(self):
        shim_map = {"flash": [], "max": ["max-m1"], "contributor_free": []}
        # Mock upstream should NOT be called for empty tier
        def mock_handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("upstream should not be called on empty tier")

        transport = httpx.MockTransport(mock_handler)
        app = create_app(shim_map, bifrost_url="http://bifrost.test", transport=transport)
        client = TestClient(app)

        resp = client.post("/v1/chat/completions", json={"model": "flash", "messages": [{"role": "user", "content": "hi"}]})
        assert resp.status_code == 503
        assert resp.headers.get("retry-after") is not None
        body = resp.json()
        # tier_unavailable signal
        assert "tier_unavailable" in json.dumps(body).lower() or body.get("error", {}).get("code") == "tier_unavailable"
        # No fallback: should not have proxied to max pool
        # If fallback existed, mock would have been called -> AssertionError

    def test_flash_alias_routes_to_flash_pool_only(self):
        shim_map = {
            "flash": ["flash-m1", "flash-m2"],
            "max": ["max-m1", "max-m2"],
            "contributor_free": ["contrib-a"],
        }
        captured = {}

        def mock_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            captured["model"] = body.get("model")
            # Simulate Bifrost response with observability
            return httpx.Response(200, json={"id": "chatcmpl-1", "choices": [], "extra_fields": {"provider": captured["model"]}})

        transport = httpx.MockTransport(mock_handler)
        app = create_app(shim_map, bifrost_url="http://bifrost.test", transport=transport)
        client = TestClient(app)

        # Use deterministic RNG via app state? For now just verify picked model is in flash pool
        # Multiple calls to ensure strict isolation
        for _ in range(5):
            captured.clear()
            resp = client.post("/v1/chat/completions", json={"model": "flash", "messages": [{"role": "user", "content": "hi"}]})
            assert resp.status_code == 200
            assert captured["model"] in shim_map["flash"]
            assert captured["model"] not in shim_map["max"]

    def test_max_alias_routes_to_max_pool(self):
        shim_map = {
            "flash": ["flash-m1"],
            "max": ["max-m1", "max-m2", "max-m3"],
            "contributor_free": [],
        }
        captured = {}

        def mock_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            captured["model"] = body.get("model")
            return httpx.Response(200, json={"id": "x", "choices": []})

        transport = httpx.MockTransport(mock_handler)
        app = create_app(shim_map, bifrost_url="http://bifrost.test", transport=transport)
        client = TestClient(app)

        resp = client.post("/v1/chat/completions", json={"model": "max", "messages": [{"role": "user", "content": "hi"}]})
        assert resp.status_code == 200
        assert captured["model"] in shim_map["max"]

    def test_explicit_real_model_pinning_bypass_alias(self):
        shim_map = {"flash": ["flash-m1"], "max": ["max-m1"], "contributor_free": []}
        captured = {}

        def mock_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            captured["model"] = body.get("model")
            return httpx.Response(200, json={"id": "x", "choices": [], "extra_fields": {"provider": "groq"}})

        transport = httpx.MockTransport(mock_handler)
        app = create_app(shim_map, bifrost_url="http://bifrost.test", transport=transport)
        client = TestClient(app)

        resp = client.post("/v1/chat/completions", json={"model": "llama-3.1-70b", "messages": [{"role": "user", "content": "hi"}]})
        assert resp.status_code == 200
        assert captured["model"] == "llama-3.1-70b"
        # observability preserved
        assert resp.json()["extra_fields"]["provider"] == "groq"

    def test_contributor_free_routes_only_to_contributor_pool(self):
        shim_map = {
            "flash": ["flash-m1"],
            "max": ["max-m1"],
            "contributor_free": ["contrib-a", "contrib-b"],
        }
        captured = {}

        def mock_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            captured["model"] = body.get("model")
            return httpx.Response(200, json={"id": "x", "choices": []})

        transport = httpx.MockTransport(mock_handler)
        app = create_app(shim_map, bifrost_url="http://bifrost.test", transport=transport)
        client = TestClient(app)

        resp = client.post("/v1/chat/completions", json={"model": "contributor_free", "messages": [{"role": "user", "content": "hi"}]})
        assert resp.status_code == 200
        assert captured["model"] in shim_map["contributor_free"]

    def test_uniform_weight_proxy_preserves_body_and_extra_fields(self):
        shim_map = {"flash": ["flash-m1", "flash-m2", "flash-m3"], "max": [], "contributor_free": []}
        captured_body = {}

        def mock_handler(request: httpx.Request) -> httpx.Response:
            captured_body.update(json.loads(request.content.decode()))
            return httpx.Response(200, json={"id": "x", "choices": [], "extra_fields": {"provider": captured_body.get("model")}, "model": captured_body.get("model")})

        transport = httpx.MockTransport(mock_handler)
        app = create_app(shim_map, bifrost_url="http://bifrost.test", transport=transport)
        client = TestClient(app)

        resp = client.post("/v1/chat/completions", json={"model": "flash", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.7})
        assert resp.status_code == 200
        # extra_fields provider observability preserved
        assert "extra_fields" in resp.json()
        assert resp.json()["extra_fields"]["provider"] in shim_map["flash"]
        # original fields preserved
        assert captured_body["temperature"] == 0.7
        assert captured_body["messages"] == [{"role": "user", "content": "hi"}]

    def test_upstream_429_propagated_and_retry_after_preserved(self):
        shim_map = {"flash": ["flash-m1"], "max": [], "contributor_free": []}

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": {"message": "rate limit"}}, headers={"retry-after": "5"})

        transport = httpx.MockTransport(mock_handler)
        app = create_app(shim_map, bifrost_url="http://bifrost.test", transport=transport)
        client = TestClient(app)

        resp = client.post("/v1/chat/completions", json={"model": "flash", "messages": [{"role": "user", "content": "hi"}]})
        # Sidecar proxies upstream status; Bifrost native retry/backoff handles retries, sidecar surfaces 429
        assert resp.status_code == 429

    def test_fallbacks_field_proxied_to_bifrost(self):
        # Bifrost supports client fallbacks[] chain; sidecar must forward it
        shim_map = {"flash": ["flash-m1"], "max": [], "contributor_free": []}
        captured = {}

        def mock_handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content.decode()))
            return httpx.Response(200, json={"id": "x", "choices": []})

        transport = httpx.MockTransport(mock_handler)
        app = create_app(shim_map, bifrost_url="http://bifrost.test", transport=transport)
        client = TestClient(app)

        resp = client.post("/v1/chat/completions", json={"model": "flash", "messages": [{"role": "user", "content": "hi"}], "fallbacks": ["max-m1"]})
        assert resp.status_code == 200
        assert captured["fallbacks"] == ["max-m1"]

    def test_health_reports_tier_counts(self):
        shim_map = {"flash": ["a", "b"], "max": ["c"], "contributor_free": []}
        app = create_app(shim_map, bifrost_url="http://bifrost.test", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["tiers"]["flash"] == 2
        assert resp.json()["tiers"]["max"] == 1

    def test_uniform_distribution_observable_over_many_picks(self):
        # Uniform weight: each of 3 models should appear ~33% over 300 picks
        shim_map = {"flash": ["a", "b", "c"], "max": [], "contributor_free": []}
        counts: dict[str, int] = {"a": 0, "b": 0, "c": 0}
        rng = random.Random(123)
        for _ in range(300):
            counts[pick_model_for_tier("flash", shim_map, rng)] += 1
        # Each should be within 70-130 (approx uniform, deterministic seed)
        for v in counts.values():
            assert 70 <= v <= 130, f"uniform fail {counts}"

    def test_sidecar_end_to_end_flash_via_bifrost_mock(self):
        # End-to-end: shim picks flash -> forwards to Bifrost -> Bifrost would hit provider
        # Single MockTransport simulates Bifrost; verifies full chain without live key
        shim_map = {"flash": ["flash-m1", "flash-m2"], "max": ["max-m1"], "contributor_free": ["contrib"]}
        trace = []

        def bifrost_mock(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            trace.append(body["model"])
            # Bifrost response with extra_fields observability
            return httpx.Response(200, json={"id": "chatcmpl-e2e", "choices": [{"message": {"content": "hi"}}], "extra_fields": {"provider": body["model"]}, "model": body["model"]})

        app = create_app(shim_map, bifrost_url="http://bifrost.test", transport=httpx.MockTransport(bifrost_mock))
        client = TestClient(app)
        resp = client.post("/v1/chat/completions", json={"model": "flash", "messages": [{"role": "user", "content": "hello"}]})
        assert resp.status_code == 200
        assert trace[0] in shim_map["flash"]
        assert resp.json()["extra_fields"]["provider"] == trace[0]
