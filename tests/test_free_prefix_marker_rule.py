"""Regression: free/ prefix marker (apinex) must trigger the free-model rule.

apinex /models ids are `free/<name>` (prefix), unlike the suffix markers
(:free, -free, _free, /free). Before the fix, `_has_free_name` saw no free
model and `_split_by_free_rule` returned every model, so paid models were
evaluated and written to data/results/apinex.yaml alongside free ones.

Seams: pipeline._is_free_model / _has_free_name / _split_by_free_rule.
"""
from llm_discovery.pipeline import _has_free_name, _is_free_model, _split_by_free_rule


class TestFreePrefixMarker:
    def test_prefix_id_is_free(self):
        assert _is_free_model({"id": "free/claude-opus-4.6"}, provider_name="apinex") is True
        assert _is_free_model("free/claude-opus-4.6", provider_name="apinex") is True

    def test_suffix_ids_still_free(self):
        for model_id in ("gpt-4:free", "gpt-4-free", "gpt-4_free", "model/free"):
            assert _is_free_model({"id": model_id}) is True

    def test_has_free_name_detects_prefix(self):
        models = [{"id": "free/kimi-k3"}, {"id": "grok-4.6"}]
        assert _has_free_name(models, "apinex") is True

    def test_split_keeps_prefix_free_drops_paid(self):
        models = [{"id": "free/kimi-k3"}, {"id": "grok-4.6"}, {"id": "paid-model"}]
        keep, dropped = _split_by_free_rule(models, provider_name="apinex")
        assert [m["id"] for m in keep] == ["free/kimi-k3"]
        assert [m["id"] for m in dropped] == ["grok-4.6", "paid-model"]

    def test_no_free_prefix_keeps_all(self):
        models = [{"id": "grok-4.6"}, {"id": "kimi-k3"}]
        keep, dropped = _split_by_free_rule(models, provider_name="apinex")
        assert keep == models
        assert dropped == []
