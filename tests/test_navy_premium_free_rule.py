"""Issue #62 — navy premium-flag regression (ADR 0004).

Seams: discovery._normalize_models, free_rule.is_free/split
Vertical slices, one test -> one impl already green (ba4c3cd). Permanent tests.
"""
from llm_discovery.discovery import _normalize_models
from llm_discovery.free_rule import has_free, is_free, split
from llm_discovery.evaluator import EvaluatorCoordinator


def _stub_provider_env(monkeypatch, raw_models):
    """Helper to stub discovery + env for discover_provider tests."""
    monkeypatch.setattr("llm_discovery.pipeline.discover_models", lambda base_url, api_key: raw_models)
    monkeypatch.setattr("llm_discovery.pipeline.load_all_secrets", lambda config=None: None)
    monkeypatch.setenv("DISABLE_WEB_SEARCH", "1")


class TestNormalizeModelsTier:
    def test_preserves_tier(self):
        raw = [{"id": "DeepSeek-V4-Flash-0731", "name": "DeepSeek-V4-Flash-0731", "tier": "turbo"}]
        out = _normalize_models(raw)
        assert out[0]["tier"] == "turbo"
        assert out[0]["id"] == "DeepSeek-V4-Flash-0731"

    def test_omits_tier_when_missing(self):
        raw = [{"id": "gpt-4", "name": "gpt-4"}]
        out = _normalize_models(raw)
        assert "tier" not in out[0]
        assert out[0]["id"] == "gpt-4"


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
        # is_free imported at top
        assert is_free({"id": "agnes:free"}) is True
        assert is_free({"id": "model-free"}) is True
        assert is_free({"id": "model_free"}) is True
        assert is_free({"id": "model/free"}) is True

    def test_non_free_not_free_generic(self):
        # is_free imported at top
        assert is_free({"id": "gpt-4"}) is False
        assert is_free({"id": "llama-3.3-70b"}) is False
        assert is_free({"id": "premium-model"}) is False

    def test_str_fallback_marker_only(self):
        # is_free imported at top
        assert is_free("model:free") is True
        assert is_free("model-free") is True
        assert is_free("gpt-4") is False
        # str with navy name still marker-only (no dict premium)
        assert is_free("gpt-4", provider_name="navy_ai") is False
        assert is_free("gpt-4:free", provider_name="navy_ai") is True

    def test_generic_premium_false_not_free(self):
        # is_free imported at top
        # scoped: premium false only free for navy_ai
        assert is_free({"id": "gpt-4", "premium": False}) is False
        assert is_free({"id": "gpt-4", "premium": False}, provider_name="openai") is False
        assert is_free({"id": "gpt-4", "premium": False}, provider_name="") is False
        assert is_free({"id": "gpt-4", "premium": False}, provider_name=None) is False
class TestIsFreeModelNavy:
    def test_navy_premium_false_is_free(self):
        # is_free imported at top
        assert is_free({"id": "gpt-4", "premium": False}, provider_name="navy_ai") is True
        # marker + premium false still free
        assert is_free({"id": "gpt-4-free", "premium": False}, provider_name="navy_ai") is True

    def test_navy_premium_true_not_free(self):
        # is_free imported at top
        assert is_free({"id": "gpt-4", "premium": True}, provider_name="navy_ai") is False

    def test_navy_missing_premium_not_free(self):
        # is_free imported at top
        assert is_free({"id": "gpt-4"}, provider_name="navy_ai") is False
        assert is_free({"id": "gpt-4", "premium": None}, provider_name="navy_ai") is False

    def test_navy_premium_string_and_zero_not_free(self):
        # is_free imported at top
        # identity check: only False (bool) free
        assert is_free({"id": "gpt-4", "premium": "false"}, provider_name="navy_ai") is False
        assert is_free({"id": "gpt-4", "premium": 0}, provider_name="navy_ai") is False
        assert is_free({"id": "gpt-4", "premium": "False"}, provider_name="navy_ai") is False

    def test_navy_marker_wins_even_premium_true(self):
        # is_free imported at top
        assert is_free({"id": "gpt-4:free", "premium": True}, provider_name="navy_ai") is True
        assert is_free({"id": "gpt-4-free", "premium": True}, provider_name="navy_ai") is True
        assert is_free({"id": "model/free", "premium": True}, provider_name="navy_ai") is True

    def test_navy_marker_missing_premium_still_free(self):
        # is_free imported at top
        assert is_free({"id": "model:free"}, provider_name="navy_ai") is True
        assert is_free({"id": "model-free", "premium": None}, provider_name="navy_ai") is True
class TestSplitByFreeRule:
    def test_generic_mixed_keeps_only_free(self):
        # split imported at top
        models = [{"id": "a:free"}, {"id": "b"}, {"id": "c-free"}]
        keep, dropped = split(models)
        assert [m["id"] for m in keep] == ["a:free", "c-free"]
        assert [m["id"] for m in dropped] == ["b"]

    def test_generic_no_free_returns_all(self):
        # split imported at top
        models = [{"id": "a"}, {"id": "b"}]
        keep, dropped = split(models)
        assert keep == models
        assert dropped == []

    def test_generic_all_free_returns_all(self):
        # split imported at top
        models = [{"id": "a:free"}, {"id": "b-free"}]
        keep, dropped = split(models)
        assert len(keep) == 2
        assert dropped == []

    def test_navy_mixed_premium_and_marker(self):
        # split imported at top
        models = [
            {"id": "free-via-premium", "premium": False},
            {"id": "free-via-marker:free", "premium": True},
            {"id": "paid", "premium": True},
            {"id": "paid-no-premium"},
        ]
        keep, dropped = split(models, provider_name="navy_ai")
        # premium False and marker both free, others dropped
        assert [m["id"] for m in keep] == ["free-via-premium", "free-via-marker:free"]
        assert [m["id"] for m in dropped] == ["paid", "paid-no-premium"]

    def test_navy_all_non_free_returns_all(self):
        # split imported at top
        models = [{"id": "a", "premium": True}, {"id": "b"}]
        keep, dropped = split(models, provider_name="navy_ai")
        assert keep == models
        assert dropped == []

    def test_navy_premium_false_only_without_marker(self):
        # split imported at top
        models = [{"id": "gpt-4", "premium": False}, {"id": "gpt-4-paid", "premium": True}]
        keep, dropped = split(models, provider_name="navy_ai")
        assert [m["id"] for m in keep] == ["gpt-4"]
        assert [m["id"] for m in dropped] == ["gpt-4-paid"]

    def test_non_navy_premium_false_ignored_in_split(self):
        # split imported at top
        models = [{"id": "gpt-4", "premium": False}, {"id": "other"}]
        # openai provider should not treat premium false as free -> no free -> keep all
        keep, dropped = split(models, provider_name="openai")
        assert keep == models
        assert dropped == []

    def test_has_free_detects_free_rows(self):
        # has_free imported at top
        assert has_free([{"id": "a:free"}]) is True
        assert has_free([{"id": "a"}]) is False
        assert has_free([{"id": "a", "premium": False}], provider_name="navy_ai") is True
        assert has_free([{"id": "a", "premium": False}], provider_name="openai") is False

    def test_split_empty_provider_string_is_generic(self):
        # split imported at top
        models = [{"id": "a", "premium": False}, {"id": "b"}]
        keep, dropped = split(models, provider_name="")
        assert keep == models  # generic, premium ignored
        assert dropped == []
        keep2, dropped2 = split(models, provider_name=None)
        # split normalizes None to the generic (no provider) rule, same as ""
        assert keep2 == models
class TestIsFreeModelLLM7:
    def test_llm7_turbo_is_free(self):
        assert is_free({"id": "DeepSeek-V4-Flash-0731", "tier": "turbo"}, provider_name="llm7") is True
        assert is_free({"id": "gemma4:31b", "tier": "turbo"}, provider_name="llm7") is True

    def test_llm7_pro_is_not_free(self):
        assert is_free({"id": "claude-opus-5", "tier": "pro"}, provider_name="llm7") is False
        assert is_free({"id": "gpt-5.5", "tier": "pro"}, provider_name="llm7") is False

    def test_llm7_missing_tier_not_free(self):
        assert is_free({"id": "unknown-model"}, provider_name="llm7") is False

    def test_llm7_turbo_without_provider_not_free(self):
        # turbo alone not free without provider_name
        assert is_free({"id": "DeepSeek-V4-Flash-0731", "tier": "turbo"}) is False


class TestSplitByFreeRuleLLM7:
    def test_llm7_mixed_keeps_only_turbo(self):
        models = [
            {"id": "DeepSeek-V4-Flash-0731", "tier": "turbo"},
            {"id": "claude-opus-5", "tier": "pro"},
            {"id": "gemma4:31b", "tier": "turbo"},
        ]
        keep, dropped = split(models, provider_name="llm7")
        assert [m["id"] for m in keep] == ["DeepSeek-V4-Flash-0731", "gemma4:31b"]
        assert [m["id"] for m in dropped] == ["claude-opus-5"]

    def test_llm7_all_turbo_returns_all(self):
        models = [
            {"id": "codestral-latest", "tier": "turbo"},
            {"id": "minimax-m2.7", "tier": "turbo"},
        ]
        keep, dropped = split(models, provider_name="llm7")
        assert len(keep) == 2
        assert dropped == []

    def test_llm7_all_pro_returns_all(self):
        models = [
            {"id": "claude-opus-5", "tier": "pro"},
            {"id": "gpt-5.5", "tier": "pro"},
        ]
        keep, dropped = split(models, provider_name="llm7")
        assert keep == models
        assert dropped == []

    def test_llm7_no_tier_field_returns_all(self):
        models = [
            {"id": "unknown-model"},
            {"id": "another-model"},
        ]
        keep, dropped = split(models, provider_name="llm7")
        assert keep == models
        assert dropped == []


class TestProviderWiringAndNaraRouter:
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
        # stub LLM to avoid real calls - Coordinator.evaluate is called for keep only (1 model)
        # we need to stub EvaluatorCoordinator.evaluate to return keep directly without LLM
        called_ids = []
        def fake_evaluate(self, model):
            called_ids.append(model["id"])
            return {"provider_model_id": model["id"], "decision": "keep", "tier": "low", "aa_score": 30}

        monkeypatch.setattr(EvaluatorCoordinator, "evaluate", fake_evaluate)
        monkeypatch.setattr("llm_discovery.benchmarks.BenchmarkDataCache", lambda: type("C", (), {"collect_from_local": lambda s,a,b: None, "_data": {}})())
        monkeypatch.setattr("llm_discovery.llm.LocalLLMEvaluator", lambda **kw: object())
        monkeypatch.setenv("NAVY_AI_API_KEY", "fake-navy")
        monkeypatch.setenv("KILO_AI_API_KEY", "fake-judge")
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
        def fake_evaluate(self, model):
            called.append(model["id"])
            return {"provider_model_id": model["id"], "decision": "keep", "tier": "low"}
        monkeypatch.setattr(EvaluatorCoordinator, "evaluate", fake_evaluate)
        monkeypatch.setattr("llm_discovery.benchmarks.BenchmarkDataCache", lambda: type("C", (), {"collect_from_local": lambda s,a,b: None, "_data": {}})())
        monkeypatch.setattr("llm_discovery.llm.LocalLLMEvaluator", lambda **kw: object())
        monkeypatch.setenv("GROQ_API_KEY", "fake-groq")
        monkeypatch.setenv("KILO_AI_API_KEY", "fake-judge")
        monkeypatch.setenv("AGNES_AI_API_KEY", "fake-judge")

        config = load_config()
        result = discover_provider("groq", config, aa=[], models_dev=[], max_workers=1)
        # generic: no free marker => keep all, both evaluated, premium ignored
        assert set(called) == {"gpt-4", "other"}
        assert len(result["keep"]) == 2

    def test_discover_provider_llm7_uses_tier_split(self, monkeypatch):
        from llm_discovery.pipeline import discover_provider
        from llm_discovery.config import load_config
        llm7_raw = [
            {"id": "DeepSeek-V4-Flash-0731", "tier": "turbo", "name": "DeepSeek-V4-Flash-0731", "object": "model"},
            {"id": "claude-opus-5", "tier": "pro", "name": "claude-opus-5", "object": "model"},
            {"id": "gemma4:31b", "tier": "turbo", "name": "gemma4:31b", "object": "model"},
        ]
        _stub_provider_env(monkeypatch, llm7_raw)
        called = []
        def fake_evaluate(self, model):
            called.append(model["id"])
            return {"provider_model_id": model["id"], "decision": "keep", "tier": "low"}
        monkeypatch.setattr(EvaluatorCoordinator, "evaluate", fake_evaluate)
        monkeypatch.setattr("llm_discovery.benchmarks.BenchmarkDataCache", lambda: type("C", (), {"collect_from_local": lambda s,a,b: None, "_data": {}})())
        monkeypatch.setattr("llm_discovery.llm.LocalLLMEvaluator", lambda **kw: object())
        monkeypatch.setenv("LLM7_API_KEY", "fake-llm7")
        monkeypatch.setenv("KILO_AI_API_KEY", "fake-judge")
        monkeypatch.setenv("AGNES_AI_API_KEY", "fake-judge")

        config = load_config()
        result = discover_provider("llm7", config, aa=[], models_dev=[], max_workers=1)
        # only turbo models should be evaluated
        assert set(called) == {"DeepSeek-V4-Flash-0731", "gemma4:31b"}
        assert len(result["keep"]) == 2