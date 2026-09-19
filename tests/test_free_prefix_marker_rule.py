"""Regression: free/ prefix marker (apinex) must trigger the free-model rule.

apinex /models ids are `free/<name>` (prefix), unlike the suffix markers
(:free, -free, _free, /free). Before the fix, the free predicate saw no free
model and the split returned every model, so paid models were evaluated and
written to data/results/apinex.yaml alongside free ones.

Seams: free_rule.is_free / has_free / split.
"""
from llm_discovery.free_rule import has_free, is_free, split


class TestFreePrefixMarker:
    def test_prefix_id_is_free(self):
        assert is_free({"id": "free/claude-opus-4.6"}, provider_name="apinex") is True
        assert is_free("free/claude-opus-4.6", provider_name="apinex") is True

    def test_suffix_ids_still_free(self):
        for model_id in ("gpt-4:free", "gpt-4-free", "gpt-4_free", "model/free"):
            assert is_free({"id": model_id}) is True

    def test_has_free_detects_prefix(self):
        models = [{"id": "free/kimi-k3"}, {"id": "grok-4.6"}]
        assert has_free(models, "apinex") is True

    def test_split_keeps_prefix_free_drops_paid(self):
        models = [{"id": "free/kimi-k3"}, {"id": "grok-4.6"}, {"id": "paid-model"}]
        keep, dropped = split(models, provider_name="apinex")
        assert [m["id"] for m in keep] == ["free/kimi-k3"]
        assert [m["id"] for m in dropped] == ["grok-4.6", "paid-model"]

    def test_no_free_prefix_keeps_all(self):
        models = [{"id": "grok-4.6"}, {"id": "kimi-k3"}]
        keep, dropped = split(models, provider_name="apinex")
        assert keep == models
        assert dropped == []
