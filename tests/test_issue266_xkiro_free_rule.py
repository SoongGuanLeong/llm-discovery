"""Issue #266 — xkiro falls back to the standard free-model rule.

The provider was previously exempted (keep all) because its free credits were
believed usable on paid models. That belief was wrong, so xkiro now keeps only
models whose provider-declared ``access_tier`` is ``"free"`` (pricing == 0
remains the generic fallback and corroborates it today).

Seams: discovery._normalize_models, free_rule.is_free/split.
The change is provider-scoped: other providers' behaviour must not change.
"""
from llm_discovery.discovery import _normalize_models
from llm_discovery.free_rule import is_free, split


class TestXkiroAccessTierRule:
    def test_access_tier_survives_normalization(self):
        raw = [{"id": "minimax/minimax-m3:free", "access_tier": "free"}]
        out = _normalize_models(raw)
        assert out[0]["access_tier"] == "free"

    def test_free_tier_is_free(self):
        assert is_free({"id": "m", "access_tier": "free"}, provider_name="xkiro") is True

    def test_paid_and_premium_are_not_free(self):
        assert is_free({"id": "m", "access_tier": "paid"}, provider_name="xkiro") is False
        assert is_free({"id": "m", "access_tier": "premium"}, provider_name="xkiro") is False

    def test_split_keeps_only_free_tier(self):
        models = [
            {"id": "minimax/minimax-m3:free", "access_tier": "free"},
            {"id": "openai/gpt-5", "access_tier": "paid"},
            {"id": "anthropic/claude-opus-5", "access_tier": "premium"},
        ]
        keep, dropped = split(models, provider_name="xkiro")
        assert [m["id"] for m in keep] == ["minimax/minimax-m3:free"]
        assert [m["id"] for m in dropped] == ["openai/gpt-5", "anthropic/claude-opus-5"]

    def test_split_pricing_zero_fallback_when_tier_missing(self):
        # access_tier absent -> generic pricing == 0 fallback still applies.
        models = [
            {"id": "free-by-price", "pricing": {"prompt": 0, "completion": 0}},
            {"id": "paid-by-price", "pricing": {"prompt": 1.0, "completion": 2.0}},
        ]
        keep, dropped = split(models, provider_name="xkiro")
        assert [m["id"] for m in keep] == ["free-by-price"]
        assert [m["id"] for m in dropped] == ["paid-by-price"]


class TestProviderScopedRegression:
    def test_non_xkiro_paid_premium_catalogue_kept(self):
        # Same catalogue, no xkiro: no free signal -> keep all, nothing dropped.
        models = [
            {"id": "openai/gpt-5", "access_tier": "paid"},
            {"id": "anthropic/claude-opus-5", "access_tier": "premium"},
        ]
        keep, dropped = split(models, provider_name="groq")
        assert keep == models
        assert dropped == []

    def test_non_xkiro_generic_free_filter_unchanged(self):
        models = [
            {"id": "free-model", "access_tier": "free"},
            {"id": "paid-model", "access_tier": "paid"},
        ]
        keep, dropped = split(models, provider_name="groq")
        assert [m["id"] for m in keep] == ["free-model"]
        assert [m["id"] for m in dropped] == ["paid-model"]
