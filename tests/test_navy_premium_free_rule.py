"""Issue #62 — navy premium-flag regression (ADR 0004).

Seams: discovery._normalize_models, pipeline._is_free_model/_split_by_free_rule
Vertical slices, one test -> one impl already green (ba4c3cd). Permanent tests.
"""
from llm_discovery.discovery import _normalize_models
from llm_discovery.pipeline import _apply_free_model_rule, _has_free_name, _is_free_model, _split_by_free_rule


def _stub_provider_env(monkeypatch, raw_models):
    """Helper to stub discovery + env for discover_provider tests."""
    monkeypatch.setattr("llm_discovery.pipeline.discover_models", lambda base_url, api_key: raw_models)
    monkeypatch.setattr("llm_discovery.pipeline.load_all_secrets", lambda config=None: None)
    monkeypatch.setenv("DISABLE_WEB_SEARCH", "1")


class TestNormalizeModelsPremium:
    def test_preserves_premium_false(self):
        raw = [{"id": "gpt-4", "name": "gpt-4", "premium": False}]
        out = _normalize_models(raw)
        assert out[0]["premium"] is False
        assert out[0]["id"] == "gpt-4"

    def test_preserves_premium_true(self):
        raw = [{"id": "gpt-4", "name": "gpt-4", "premium": True}]
        out = _normalize_models(raw)
        assert out[0]["premium"] is True

    def test_omits_premium_when_missing(self):
        raw = [{"id": "gpt-4", "name": "gpt-4"}]
        out = _normalize_models(raw)
        assert "premium" not in out[0]
        assert out[0]["id"] == "gpt-4"
        assert out[0]["name"] == "gpt-4"

    def test_preserves_premium_zero_and_string(self):
        # limit: zero/string keep as-is, but free rule ignores them
        raw = [{"id": "a", "premium": 0}, {"id": "b", "premium": "false"}]
        out = _normalize_models(raw)
        assert out[0]["premium"] == 0
        assert out[1]["premium"] == "false"

    def test_id_fallback_keeps_premium(self):
        raw = [{"name": "cohere-model", "premium": False}]
        out = _normalize_models(raw)
        assert out[0]["id"] == "cohere-model"
        assert out[0]["premium"] is False
class TestIsFreeModelGeneric:
    def test_free_markers_are_free_generic(self):
        # _is_free_model imported at top
        assert _is_free_model({"id": "agnes:free"}) is True
        assert _is_free_model({"id": "model-free"}) is True
        assert _is_free_model({"id": "model_free"}) is True
        assert _is_free_model({"id": "model/free"}) is True

    def test_non_free_not_free_generic(self):
        # _is_free_model imported at top
        assert _is_free_model({"id": "gpt-4"}) is False
        assert _is_free_model({"id": "llama-3.3-70b"}) is False
        assert _is_free_model({"id": "premium-model"}) is False

    def test_str_fallback_marker_only(self):
        # _is_free_model imported at top
        assert _is_free_model("model:free") is True
        assert _is_free_model("model-free") is True
        assert _is_free_model("gpt-4") is False
        # str with navy name still marker-only (no dict premium)
        assert _is_free_model("gpt-4", provider_name="navy_ai") is False
        assert _is_free_model("gpt-4:free", provider_name="navy_ai") is True

    def test_generic_premium_false_not_free(self):
        # _is_free_model imported at top
        # scoped: premium false only free for navy_ai
        assert _is_free_model({"id": "gpt-4", "premium": False}) is False
        assert _is_free_model({"id": "gpt-4", "premium": False}, provider_name="openai") is False
        assert _is_free_model({"id": "gpt-4", "premium": False}, provider_name="") is False
        assert _is_free_model({"id": "gpt-4", "premium": False}, provider_name=None) is False
class TestIsFreeModelNavy:
    def test_navy_premium_false_is_free(self):
        # _is_free_model imported at top
        assert _is_free_model({"id": "gpt-4", "premium": False}, provider_name="navy_ai") is True
        # marker + premium false still free
        assert _is_free_model({"id": "gpt-4-free", "premium": False}, provider_name="navy_ai") is True

    def test_navy_premium_true_not_free(self):
        # _is_free_model imported at top
        assert _is_free_model({"id": "gpt-4", "premium": True}, provider_name="navy_ai") is False

    def test_navy_missing_premium_not_free(self):
        # _is_free_model imported at top
        assert _is_free_model({"id": "gpt-4"}, provider_name="navy_ai") is False
        assert _is_free_model({"id": "gpt-4", "premium": None}, provider_name="navy_ai") is False

    def test_navy_premium_string_and_zero_not_free(self):
        # _is_free_model imported at top
        # identity check: only False (bool) free
        assert _is_free_model({"id": "gpt-4", "premium": "false"}, provider_name="navy_ai") is False
        assert _is_free_model({"id": "gpt-4", "premium": 0}, provider_name="navy_ai") is False
        assert _is_free_model({"id": "gpt-4", "premium": "False"}, provider_name="navy_ai") is False

    def test_navy_marker_wins_even_premium_true(self):
        # _is_free_model imported at top
        assert _is_free_model({"id": "gpt-4:free", "premium": True}, provider_name="navy_ai") is True
        assert _is_free_model({"id": "gpt-4-free", "premium": True}, provider_name="navy_ai") is True
        assert _is_free_model({"id": "model/free", "premium": True}, provider_name="navy_ai") is True

    def test_navy_marker_missing_premium_still_free(self):
        # _is_free_model imported at top
        assert _is_free_model({"id": "model:free"}, provider_name="navy_ai") is True
        assert _is_free_model({"id": "model-free", "premium": None}, provider_name="navy_ai") is True
class TestSplitByFreeRule:
    def test_generic_mixed_keeps_only_free(self):
        # _split_by_free_rule imported at top
        models = [{"id": "a:free"}, {"id": "b"}, {"id": "c-free"}]
        keep, dropped = _split_by_free_rule(models)
        assert [m["id"] for m in keep] == ["a:free", "c-free"]
        assert [m["id"] for m in dropped] == ["b"]

    def test_generic_no_free_returns_all(self):
        # _split_by_free_rule imported at top
        models = [{"id": "a"}, {"id": "b"}]
        keep, dropped = _split_by_free_rule(models)
        assert keep == models
        assert dropped == []

    def test_generic_all_free_returns_all(self):
        # _split_by_free_rule imported at top
        models = [{"id": "a:free"}, {"id": "b-free"}]
        keep, dropped = _split_by_free_rule(models)
        assert len(keep) == 2
        assert dropped == []

    def test_navy_mixed_premium_and_marker(self):
        # _split_by_free_rule imported at top
        models = [
            {"id": "free-via-premium", "premium": False},
            {"id": "free-via-marker:free", "premium": True},
            {"id": "paid", "premium": True},
            {"id": "paid-no-premium"},
        ]
        keep, dropped = _split_by_free_rule(models, provider_name="navy_ai")
        # premium False and marker both free, others dropped
        assert [m["id"] for m in keep] == ["free-via-premium", "free-via-marker:free"]
        assert [m["id"] for m in dropped] == ["paid", "paid-no-premium"]

    def test_navy_all_non_free_returns_all(self):
        # _split_by_free_rule imported at top
        models = [{"id": "a", "premium": True}, {"id": "b"}]
        keep, dropped = _split_by_free_rule(models, provider_name="navy_ai")
        assert keep == models
        assert dropped == []

    def test_navy_premium_false_only_without_marker(self):
        # _split_by_free_rule imported at top
        models = [{"id": "gpt-4", "premium": False}, {"id": "gpt-4-paid", "premium": True}]
        keep, dropped = _split_by_free_rule(models, provider_name="navy_ai")
        assert [m["id"] for m in keep] == ["gpt-4"]
        assert [m["id"] for m in dropped] == ["gpt-4-paid"]

    def test_non_navy_premium_false_ignored_in_split(self):
        # _split_by_free_rule imported at top
        models = [{"id": "gpt-4", "premium": False}, {"id": "other"}]
        # openai provider should not treat premium false as free -> no free -> keep all
        keep, dropped = _split_by_free_rule(models, provider_name="openai")
        assert keep == models
        assert dropped == []

    def test_has_free_name_delegates(self):
        # _has_free_name imported at top
        assert _has_free_name([{"id": "a:free"}]) is True
        assert _has_free_name([{"id": "a"}]) is False
        assert _has_free_name([{"id": "a", "premium": False}], provider_name="navy_ai") is True
        assert _has_free_name([{"id": "a", "premium": False}], provider_name="openai") is False

    def test_split_empty_provider_string_is_generic(self):
        # _split_by_free_rule imported at top
        models = [{"id": "a", "premium": False}, {"id": "b"}]
        keep, dropped = _split_by_free_rule(models, provider_name="")
        assert keep == models  # generic, premium ignored
        assert dropped == []
        keep2, dropped2 = _split_by_free_rule(models, provider_name=None)
        # None passed via _has_free_name path internally? _split normalizes to None
        # call directly with None should behave same as generic
        # but signature default is "", test via _is_free_model equivalence
        assert keep2 == models
class TestProviderWiringAndNaraRouter:
    def test_apply_free_rule_delegates(self):
        from llm_discovery.pipeline import _apply_free_model_rule, _split_by_free_rule
        models = [{"id": "a:free"}, {"id": "b"}]
        out = _apply_free_model_rule(models.copy(), provider_name="")
        # legacy mutates but returns same list; should mark dropped
        # check b got drop marker
        # use fresh copy to avoid cross-contamination
        models2 = [{"id": "a:free"}, {"id": "b"}]
        keep, dropped = _split_by_free_rule(models2)
        assert len(keep) == 1
        assert len(dropped) == 1
        # _apply should have mutated non-free
        # b is dropped -> has _drop_reason
        mutated = [m for m in out if m["id"] == "b"]
        assert mutated[0].get("_drop_reason") is not None

    def test_nararouter_not_affected_by_premium_rule(self, monkeypatch):
        # NaraRouter uses allowlist, not free markers/premium. Ensure navy premium logic doesn't leak.
        from llm_discovery.discovery import discover_nararouter_models, NARAROUTER_FREE_SNAPSHOT
        raw = [{"id": "a", "premium": False}, {"id": "agnes-2.0-flash", "premium": False}]
        monkeypatch.setattr("llm_discovery.discovery.discover_models", lambda base_url, api_key: raw)
        # injected allowlist empty vs snapshot
        out = discover_nararouter_models("https://router.bynara.id/v1", "fake", allowlist={"agnes-2.0-flash"})
        assert [m["id"] for m in out] == ["agnes-2.0-flash"]
        # even though both have premium False, only allowlist matters

    def test_discover_provider_navy_uses_premium_split(self, monkeypatch):
        from llm_discovery.pipeline import discover_provider
        from llm_discovery.config import load_config
        navy_raw = [
            {"id": "free-via-premium", "premium": False, "name": "free-via-premium", "object": "model"},
            {"id": "paid", "premium": True, "name": "paid", "object": "model"},
            {"id": "paid-no-premium", "name": "paid-no-premium", "object": "model"},
        ]
        _stub_provider_env(monkeypatch, navy_raw)
        # stub LLM to avoid real calls - evaluate_model will be called for keep only (1 model)
        # we need to stub evaluate_model to return keep directly without LLM
        called_ids = []
        def fake_evaluate(model, provider_name, aa, models_dev, evaluator, min_score, max_score, cache=None):
            called_ids.append(model["id"])
            return {"provider_model_id": model["id"], "decision": "keep", "tier": "low", "aa_score": 30}

        monkeypatch.setattr("llm_discovery.pipeline.evaluate_model", fake_evaluate)
        monkeypatch.setattr("llm_discovery.benchmarks.BenchmarkDataCache", lambda: type("C", (), {"collect_from_local": lambda s,a,b: None, "_data": {}})())
        monkeypatch.setattr("llm_discovery.llm.LocalLLMEvaluator", lambda **kw: object())
        monkeypatch.setenv("NAVY_AI_API_KEY", "fake-navy")
        monkeypatch.setenv("AGNES_AI_API_KEY", "fake-judge")

        config = load_config()
        result = discover_provider("navy_ai", config, aa=[], models_dev=[], max_workers=1)
        # only premium False should be evaluated
        assert called_ids == ["free-via-premium"]
        assert len(result["keep"]) == 1
        assert result["keep"][0]["provider_model_id"] == "free-via-premium"

    def test_discover_provider_generic_premium_false_not_split(self, monkeypatch):
        from llm_discovery.pipeline import discover_provider
        from llm_discovery.config import load_config
        generic_raw = [
            {"id": "gpt-4", "premium": False, "name": "gpt-4", "object": "model"},
            {"id": "other", "name": "other", "object": "model"},
        ]
        _stub_provider_env(monkeypatch, generic_raw)
        called = []
        def fake_evaluate(model, provider_name, aa, models_dev, evaluator, min_score, max_score, cache=None):
            called.append(model["id"])
            return {"provider_model_id": model["id"], "decision": "keep", "tier": "low"}
        monkeypatch.setattr("llm_discovery.pipeline.evaluate_model", fake_evaluate)
        monkeypatch.setattr("llm_discovery.benchmarks.BenchmarkDataCache", lambda: type("C", (), {"collect_from_local": lambda s,a,b: None, "_data": {}})())
        monkeypatch.setattr("llm_discovery.llm.LocalLLMEvaluator", lambda **kw: object())
        monkeypatch.setenv("GROQ_API_KEY", "fake-groq")
        monkeypatch.setenv("AGNES_AI_API_KEY", "fake-judge")

        config = load_config()
        result = discover_provider("groq", config, aa=[], models_dev=[], max_workers=1)
        # generic: no free marker => keep all, both evaluated, premium ignored
        assert set(called) == {"gpt-4", "other"}
        assert len(result["keep"]) == 2