"""T2 (issue #3) tests: pure logic only, no network, no secrets.

The LLM judge + provider keys live behind the user local Infisical and the
judge endpoint; per the repo secrets ban we never request them. The judge
call is the integration seam. Everything below is deterministic and offline.
"""
import os

import pytest
import yaml

from llm_discovery.categorize import categorize_model
from llm_discovery.config import AppConfig, load_config
from llm_discovery.evaluation import ModelEvaluation, ModelEvaluationRequest
from llm_discovery.pipeline import (
    classify_provider_error,
    provider_error_result,
    discover_all_providers,
    discover_single,
    evaluate_model,
    pick_tracer_model,
)
from llm_discovery.results import save_yaml_result, YAML_SCHEMA_KEYS
from llm_discovery.resolver import normalize_model_id, resolve_model
from scripts.discover import parse_args


# T1: max / flash / drop / error tiering (pure)
class TestCategorizeModel:
    def test_max_band(self):
        assert categorize_model(aa_score=55.0, coding=True) == "max"
        assert categorize_model(aa_score=45.0, coding=True) == "max"  # boundary inclusive

    def test_flash_band(self):
        assert categorize_model(aa_score=35.0, coding=True) == "flash"
        assert categorize_model(aa_score=24.0, coding=True) == "flash"  # boundary inclusive

    def test_drop_below_min(self):
        assert categorize_model(aa_score=23.0, coding=True) == "drop"
        assert categorize_model(aa_score=0.0, coding=True) == "drop"

    def test_boundary_min_inclusive(self):
        """AA score of exactly min_score must be kept (flash)."""
        assert categorize_model(aa_score=24.0, coding=True, min_score=24.0) == "flash"

    def test_boundary_just_above_min(self):
        assert categorize_model(aa_score=24.1, coding=True, min_score=24.0) == "flash"

    def test_boundary_just_below_min(self):
        assert categorize_model(aa_score=23.9, coding=True, min_score=24.0) == "drop"

    def test_boundary_no_score(self):
        """No AA score + coding=true + no coding_score → uncertain (insufficient evidence)."""
        assert categorize_model(aa_score=None, coding=True, coding_score=None) == "uncertain"

    def test_boundary_no_score_not_coding(self):
        assert categorize_model(aa_score=None, coding=False) == "drop"

    def test_drop_when_not_coding(self):
        assert categorize_model(aa_score=55.0, coding=False) == "drop"
        assert categorize_model(aa_score=35.0, coding=False) == "drop"

    def test_custom_min_score(self):
        assert categorize_model(aa_score=28.0, coding=True, min_score=30.0) == "drop"

    def test_custom_max_score(self):
        assert categorize_model(aa_score=55.0, coding=True, max_score=60.0) == "flash"

    def test_not_coding_forces_drop(self):
        """A high AA score cannot rescue a non-coding model."""
        assert categorize_model(aa_score=99.0, coding=False) == "drop"

    def test_error_judge_decision(self):
        """Judge failure surfaces as 'error', not 'drop'."""
        assert categorize_model(coding=True, aa_score=55.0, judge_decision="error") == "error"
        assert categorize_model(coding=False, aa_score=None, judge_decision="error") == "error"

    def test_error_judge_overrides_hard_gate(self):
        """Error must win even for a model that would otherwise drop."""
        assert categorize_model(coding=False, aa_score=10.0, judge_decision="error") == "error"


    def test_flagship_exact_token_match(self):
        """Flagship patterns must match whole tokens, not substrings."""
        # minimax must NOT match "max" — the core bug from #14
        assert categorize_model(model_id="minimax/minimax-m2.7:free", coding=True, aa_score=30.0) != "max"
        assert categorize_model(model_id="minimax/minimax-m2.7:free", coding=True, aa_score=30.0) == "flash"
        # Other substring edge cases — must not match
        assert categorize_model(model_id="gpt-prod-v2", coding=True, aa_score=30.0) != "max"
        assert categorize_model(model_id="openai/proxy-model", coding=True, aa_score=30.0) != "max"
        assert categorize_model(model_id="deepseek/super-user-chat", coding=True, aa_score=30.0) == "max"
        assert categorize_model(model_id="test-flagship-model", coding=True, aa_score=30.0) == "max"
        # Existing flagship names must still match correctly
        assert categorize_model(model_id="deepseek/deepseek-chat", coding=True, aa_score=50.0) == "max"
        assert categorize_model(model_id="deepseek/deepseek-coder", coding=True, aa_score=50.0) == "max"  # "deep" not flagship
        assert categorize_model(model_id="openai/gpt-4o", coding=True, aa_score=50.0) == "max"  # no flagship
        assert categorize_model(model_id="anthropic/claude-opus-4", coding=True, aa_score=50.0) == "max"
        assert categorize_model(model_id="google/gemma-ultra", coding=True, aa_score=50.0) == "max"
        assert categorize_model(model_id="meta/llama-pro", coding=True, aa_score=50.0) == "max"
        assert categorize_model(model_id="foo/max", coding=True, aa_score=50.0) == "max"
        assert categorize_model(model_id="foo-premium-bar", coding=True, aa_score=50.0) == "max"

    def test_flagship_exact_token_match(self):
        """Flagship patterns must match whole tokens, not substrings."""
        # minimax must NOT match "max" — the core bug from #14
        assert categorize_model(model_id="minimax/minimax-m2.7:free", coding=True, aa_score=30.0) != "max"
        assert categorize_model(model_id="minimax/minimax-m2.7:free", coding=True, aa_score=30.0) == "flash"
        # Other substring edge cases — must not match
        assert categorize_model(model_id="gpt-prod-v2", coding=True, aa_score=30.0) != "max"
        assert categorize_model(model_id="openai/proxy-model", coding=True, aa_score=30.0) != "max"
        assert categorize_model(model_id="deepseek/super-user-chat", coding=True, aa_score=30.0) == "max"
        assert categorize_model(model_id="test-flagship-model", coding=True, aa_score=30.0) == "max"
        # Existing flagship names must still match correctly
        assert categorize_model(model_id="deepseek/deepseek-chat", coding=True, aa_score=50.0) == "max"
        assert categorize_model(model_id="deepseek/deepseek-coder", coding=True, aa_score=50.0) == "max"  # "deep" not flagship
        assert categorize_model(model_id="openai/gpt-4o", coding=True, aa_score=50.0) == "max"  # no flagship
        assert categorize_model(model_id="anthropic/claude-opus-4", coding=True, aa_score=50.0) == "max"
        assert categorize_model(model_id="google/gemma-ultra", coding=True, aa_score=50.0) == "max"
        assert categorize_model(model_id="meta/llama-pro", coding=True, aa_score=50.0) == "max"
        assert categorize_model(model_id="foo/max", coding=True, aa_score=50.0) == "max"
        assert categorize_model(model_id="foo-premium-bar", coding=True, aa_score=50.0) == "max"


# Provider model selection (deterministic tracer pick)
class TestPickTracerModel:
    def test_picks_highest_aa_score(self, aa_catalog, sample_models):
        chosen = pick_tracer_model(sample_models, aa_catalog, min_score=24.0)
        assert chosen["id"] == "llama-3.3-70b-versatile"

    def test_deterministic_order_independent(self, aa_catalog, sample_models):
        reordered = list(reversed(sample_models))
        chosen = pick_tracer_model(reordered, aa_catalog, min_score=24.0)
        assert chosen["id"] == "llama-3.3-70b-versatile"

    def test_fallback_to_first_when_unresolved(self, aa_catalog):
        no_match = [{"id": "zzz-unknown-99"}, {"id": "aaa-unknown-11"}]
        chosen = pick_tracer_model(no_match, aa_catalog, min_score=24.0)
        assert chosen["id"] == "aaa-unknown-11"

    def test_prefers_scored_over_unscored(self, aa_catalog):
        models = [{"id": "allam-2-7b"}, {"id": "llama-3.3-70b-versatile"}]
        chosen = pick_tracer_model(models, aa_catalog, min_score=24.0)
        assert chosen["id"] == "llama-3.3-70b-versatile"


# Per-model evaluation record wiring (uses a fake evaluator, no LLM)
class _FakeEvaluator:
    def __init__(self, result: ModelEvaluation):
        self._result = result
        self.last_request = None

    def evaluate(self, request: ModelEvaluationRequest, evidence_packet=None) -> ModelEvaluation:
        self.last_request = request
        return self._result


class TestEvaluateModel:
    def test_keeps_max_model_wires_tier(self, aa_catalog, models_dev):
        ev = _FakeEvaluator(
            ModelEvaluation(
                canonical_name="Llama 3.3 70B",
                coding=True,
                aa_relevance="strong",
                confidence=0.95,
                decision="keep",
                evidence_level="strong",
                evidence=["coding benchmark", "docs"],
                coding_assessment=None,
            )
        )
        rec = evaluate_model(
            model={"id": "llama-3.3-70b-versatile"},
            provider_name="groq",
            aa=aa_catalog,
            models_dev=models_dev,
            evaluator=ev,
            min_score=24.0,
            max_score=45.0,
        )
        assert rec["provider_model_id"] == "llama-3.3-70b-versatile"
        assert rec["source"] == "llm"
        assert rec["decision"] == "keep"
        assert rec["tier"] == "max"  # aa_score 55 >= 45
        assert rec["aa_model_id"] == "aa-llama-3.3-70b-versatile"
        assert rec["aa_score"] == 55.0
        assert rec["aa_name"] == "Llama 3.3 70B"
        assert rec["confidence"] == 0.95
        assert rec["evidence"] == ["coding benchmark", "docs"]

    def test_flash_model_kept(self, aa_catalog, models_dev):
        ev = _FakeEvaluator(
            ModelEvaluation(
                coding=True,
                aa_relevance="moderate",
                confidence=0.9,
                decision="keep",
                evidence_level="moderate",
                evidence=["e1", "e2"],
                coding_assessment=None,
            )
        )
        rec = evaluate_model(
            {"id": "llama-3.1-8b-instant"}, "groq", aa_catalog, models_dev, ev, 24.0, 45.0
        )
        assert rec["decision"] == "keep"
        assert rec["tier"] == "flash"  # 35 in [24, 45)

    def test_hard_gate_overrides_judge_keep_to_drop(self, aa_catalog, models_dev):
        ev = _FakeEvaluator(
            ModelEvaluation(
                coding=True,
                aa_relevance="weak",
                confidence=0.9,
                decision="keep",
                evidence_level="moderate",
                evidence=["x"],
                coding_assessment=None,
            )
        )
        rec = evaluate_model(
            {"id": "qwen-72b"}, "groq", aa_catalog, models_dev, ev, 24.0, 45.0
        )
        assert rec["decision"] == "drop"
        assert rec["tier"] == "drop"
        assert rec["aa_score"] == 15.0

    def test_coding_false_forced_drop(self, aa_catalog, models_dev):
        ev = _FakeEvaluator(
            ModelEvaluation(
                coding=False,
                aa_relevance="strong",
                confidence=0.9,
                decision="keep",
                evidence_level="strong",
                evidence=["x"],
                coding_assessment=None,
            )
        )
        rec = evaluate_model(
            {"id": "llama-3.3-70b-versatile"}, "groq", aa_catalog, models_dev, ev, 24.0, 45.0
        )
        assert rec["decision"] == "drop"
        assert rec["tier"] == "drop"

    def test_judge_drop_stays_drop_with_max_tier(self, aa_catalog, models_dev):
        ev = _FakeEvaluator(
            ModelEvaluation(
                coding=True,
                aa_relevance="strong",
                confidence=0.4,
                decision="drop",
                evidence_level="strong",
                evidence=["unsuitable"],
                coding_assessment=None,
            )
        )
        rec = evaluate_model(
            {"id": "llama-3.3-70b-versatile"}, "groq", aa_catalog, models_dev, ev, 24.0, 45.0
        )
        assert rec["decision"] == "drop"
        assert rec["tier"] == "max"

    def test_no_aa_match_record_has_empty_aa_fields(self, aa_catalog, models_dev):
        ev = _FakeEvaluator(
            ModelEvaluation(
                coding=True,
                aa_relevance="none",
                confidence=0.8,
                decision="unknown",
                evidence_level="weak",
                evidence=["docs say coding"],
                coding_assessment=None,
            )
        )
        rec = evaluate_model(
            {"id": "allam-2-7b"}, "groq", aa_catalog, models_dev, ev, 24.0, 45.0
        )
        assert rec["aa_model_id"] is None
        assert rec["aa_score"] is None
        # No benchmark data -> unknown -> DROP for final catalog
        assert rec["tier"] == "drop"
        assert rec["decision"] == "drop"
        # Check that evidence mentions insufficient evidence
        evidence_str = " ".join(rec.get("evidence", []))
        assert "Insufficient evidence" in evidence_str or "insufficient evidence" in evidence_str.lower()

    def test_judge_unknown_aa_id_is_nullified(self, aa_catalog, models_dev):
        """Test that LLM aa_relevance doesn't override deterministic AA match."""
        ev = _FakeEvaluator(
            ModelEvaluation(
                coding=True,
                aa_relevance="strong",
                confidence=0.9,
                decision="keep",
                evidence_level="strong",
                evidence=["docs"],
                coding_assessment=None,
            )
        )
        rec = evaluate_model(
            {"id": "llama-3.3-70b-versatile"}, "groq", aa_catalog, models_dev, ev, 24.0, 45.0
        )
        # Deterministic AA match should be used, not LLM's assessment
        assert rec["aa_model_id"] is not None
        assert rec["aa_score"] is not None

    def test_specialized_model_deterministic_drop(self, aa_catalog, models_dev):
        """whisper / tts / safety models are dropped before the judge is called."""
        ev = _FailEvaluator()
        rec = evaluate_model(
            {"id": "openai/whisper-large-v3", "owned_by": "openai"},
            "groq", aa_catalog, models_dev, ev, 24.0, 45.0,
        )
        assert rec["source"] == "deterministic"
        assert rec["decision"] == "drop"
        assert rec["tier"] == "drop"
        assert rec["confidence"] == 1.0
        assert ev.called is False  # judge was NOT invoked

    def test_specialized_stt_model_dropped(self, aa_catalog, models_dev):
        ev = _FailEvaluator()
        rec = evaluate_model(
            {"id": "openai/whisper-base", "owned_by": "openai"},
            "groq", aa_catalog, models_dev, ev, 24.0, 45.0,
        )
        assert rec["decision"] == "drop"
        assert ev.called is False

    def test_request_carries_provider_and_candidates(self, aa_catalog, models_dev):
        ev = _FakeEvaluator(
            ModelEvaluation(
                coding=True,
                aa_relevance="none",
                confidence=0.5,
                decision="drop",
                evidence_level="weak",
                evidence=["x"],
                coding_assessment=None,
            )
        )
        evaluate_model(
            {"id": "llama-3.3-70b-versatile"}, "groq", aa_catalog, models_dev, ev, 24.0, 45.0
        )
        req = ev.last_request
        assert req.provider == "groq"
        assert req.model_id == "llama-3.3-70b-versatile"
        assert req.aa_match is not None
        assert req.aa_match["matched"] is True
        assert req.aa_match["model_id"] == "aa-llama-3.3-70b-versatile"


class _FailEvaluator:
    """Always-failing evaluator to prove the judge was never called."""
    def __init__(self):
        self.called = False

    def evaluate(self, request: ModelEvaluationRequest, evidence_packet=None) -> ModelEvaluation:
        self.called = True
        raise RuntimeError("judge should not have been called")


# YAML result writer: exact schema + round-trip
class TestYamlResult:
    def test_schema_keys_exact(self, tmp_path):
        record = {
            "provider_model_id": "llama-3.3-70b-versatile",
            "source": "llm",
            "canonical_name": "Llama 3.3 70B",
            "coding": True,
            "aa_model_id": "aa-llama-3.3-70b-versatile",
            "aa_name": "Llama 3.3 70B",
            "aa_slug": "llama-3.3-70b-versatile",
            "aa_score": 55.0,
            "confidence": 0.95,
            "decision": "keep",
            "tier": "max",
            "evidence_level": "strong",
            "evidence": ["coding benchmark", "docs"],
            "coding_assessment": None,
        }
        path = save_yaml_result(record, provider="groq", output_dir=tmp_path)
        data = yaml.safe_load(path.read_text())
        assert set(data.keys()) == set(YAML_SCHEMA_KEYS)
        assert list(data.keys()) == [
            "provider", "model_id", "decision", "tier",
            "aa_model_id", "aa_score", "confidence", "evidence_level", "evidence", "coding_assessment",
        ]
        assert data["provider"] == "groq"
        assert data["model_id"] == "llama-3.3-70b-versatile"
        assert data["decision"] == "keep"
        assert data["tier"] == "max"
        assert data["aa_model_id"] == "aa-llama-3.3-70b-versatile"
        assert data["aa_score"] == 55.0
        assert data["confidence"] == 0.95
        assert data["evidence_level"] == "strong"
        assert data["evidence"] == ["coding benchmark", "docs"]

    def test_round_trip_nulls(self, tmp_path):
        record = {
            "provider_model_id": "allam-2-7b",
            "source": "llm",
            "coding": False,
            "aa_model_id": None,
            "aa_score": None,
            "confidence": 0.0,
            "decision": "drop",
            "tier": "drop",
            "evidence_level": "weak",
            "evidence": ["no coding capability"],
            "coding_assessment": None,
        }
        path = save_yaml_result(record, provider="groq", output_dir=tmp_path)
        data = yaml.safe_load(path.read_text())
        assert set(data.keys()) == set(YAML_SCHEMA_KEYS)
        assert data["aa_model_id"] is None
        assert data["aa_score"] is None
        assert data["decision"] == "drop"
        assert data["tier"] == "drop"
        assert data["evidence_level"] == "weak"

    def test_error_record_schema(self, tmp_path):
        record = {
            "provider_model_id": "broken-model",
            "source": "llm_error",
            "coding": False,
            "aa_model_id": None,
            "aa_score": None,
            "confidence": 0.0,
            "decision": "error",
            "tier": "error",
            "evidence_level": "none",
            "evidence": ["LLM evaluation failed: timeout"],
            "coding_assessment": None,
        }
        path = save_yaml_result(record, provider="groq", output_dir=tmp_path)
        data = yaml.safe_load(path.read_text())
        assert set(data.keys()) == set(YAML_SCHEMA_KEYS)
        assert data["decision"] == "error"
        assert data["tier"] == "error"
        assert data["evidence_level"] == "none"
        assert data["aa_score"] is None

    def test_filename_is_provider_yaml(self, tmp_path):
        record = {
            "provider_model_id": "x", "decision": "error", "tier": "error",
            "aa_model_id": None, "aa_score": None, "confidence": 0.0,
            "evidence": ["x"],
        }
        path = save_yaml_result(record, provider="groq", output_dir=tmp_path)
        assert path.name == "groq.yaml"


# Config: max_score added, shape intact
class TestConfig:
    def test_loads_shape(self):
        config = load_config()
        assert isinstance(config, AppConfig)
        assert config.artificial_analysis.min_score == 24
        assert config.artificial_analysis.max_score == 45
        # Judge model is environment-managed (may be agnes-2.0-flash, etc.);
        # we only assert the wiring is correct.
        assert config.judge_llm.base_url == "https://apihub.agnes-ai.com/v1"
        assert config.judge_llm.secret == "AGNES_AI_API_KEY"
        assert isinstance(config.judge_llm.model, str) and config.judge_llm.model
        groq = next(p for p in config.providers if p.name == "groq")
        assert groq.base_url == "https://api.groq.com/openai/v1"
        assert groq.secret == "GROQ_API_KEY"


# discover.py argv parsing (pure)
class TestParseArgs:
    def test_takes_provider_arg(self):
        config = load_config()
        args = parse_args(["groq"], config)
        assert args.provider == "groq"
        assert args.all is False
        assert args.all_providers is False

    def test_defaults_to_first_provider(self):
        config = load_config()
        args = parse_args([], config)
        assert args.provider == config.providers[0].name

    def test_rejects_unknown_provider(self):
        config = load_config()
        with pytest.raises(SystemExit):
            parse_args(["nonexistent-provider"], config)

    def test_all_flag(self):
        config = load_config()
        args = parse_args(["groq", "--all"], config)
        assert args.all is True

    def test_all_providers_flag(self):
        config = load_config()
        args = parse_args(["--all-providers"], config)
        assert args.all_providers is True

    def test_workers_default(self):
        config = load_config()
        args = parse_args(["groq"], config)
        assert args.workers == 4


# Resolver characterization (pure)
class TestResolver:
    def test_normalize(self):
        assert normalize_model_id("groq/mix") == "mix"
        assert normalize_model_id("Meta.Llama-3.3 70B") == "meta-llama-3-3-70b"
        assert normalize_model_id("Llama---3.3") == "llama-3-3"

    def test_exact_slug_match(self, aa_catalog):
        res = resolve_model("llama-3.3-70b-versatile", aa_catalog)
        assert res.aa_model is not None
        assert res.method == "exact_slug"
        assert res.aa_model["id"] == "aa-llama-3.3-70b-versatile"

    def test_normalized_match(self, aa_catalog):
        res = resolve_model("Llama.3.3-70B-Versatile", aa_catalog)
        assert res.aa_model is not None
        assert res.method == "normalized_slug"

    def test_unresolved_when_no_match(self, aa_catalog):
        res = resolve_model("totally-unknown-model", aa_catalog)
        assert res.aa_model is None
        assert res.method == "unresolved"


# Catalogs characterization (pure, offline)
class TestCatalogs:
    def test_aa_get_by_id(self, aa_catalog):
        assert aa_catalog.get_by_id("aa-llama-3.3-70b-versatile")["name"] == "Llama 3.3 70B"
        assert aa_catalog.get_by_id("nope") is None

    def test_aa_filter_and_search(self, aa_catalog):
        kept = aa_catalog.filter(min_score=24.0)
        assert {m["id"] for m in kept} == {"aa-llama-3.3-70b-versatile", "aa-llama-3.1-8b-instant"}
        search = aa_catalog.search("llama")
        assert len(search) == 2


# --------------------------------------------------------------------------- #
# discover_single composition (mocked judge + mocked /models — no secrets)
# --------------------------------------------------------------------------- #
class _FakeResponse:
    """Stand-in for httpx.Response returned by the judge endpoint."""

    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http error {self.status_code}")

    def json(self) -> dict:
        return self._payload


def _keep_message(aa_model_id: str = "aa-llama-3.3-70b-versatile"):
    import json as _json
    body = {
        "canonical_name": "Llama 3.3 70B",
        "coding": True,
        "aa_relevance": "strong",
        "confidence": 0.95,
        "decision": "keep",
        "evidence_level": "strong",
        "evidence": ["coding benchmark", "docs"],
        "coding_assessment": None,
    }
    return {"choices": [{"message": {"content": _json.dumps(body)}}]}


_TRACER_MODELS = [
    {"id": "allam-2-7b"},
    {"id": "llama-3.3-70b-versatile"},
    {"id": "llama-3.1-8b-instant"},
]


class TestDiscoverSingle:
    def _wire(self, monkeypatch, keep_payload):
        monkeypatch.setattr(
            "llm_discovery.llm.httpx.post",
            lambda *a, **k: _FakeResponse(keep_payload),
        )
        monkeypatch.setattr(
            "llm_discovery.pipeline.discover_models",
            lambda base_url, api_key: _TRACER_MODELS,
        )
        monkeypatch.setattr("llm_discovery.pipeline.load_all_secrets", lambda config=None: None)
        monkeypatch.setenv("AGNES_AI_API_KEY", "fake-judge-key")
        monkeypatch.setenv("GROQ_API_KEY", "fake-provider-key")

    def test_keeps_max_model_end_to_end(self, monkeypatch, aa_catalog, models_dev):
        self._wire(monkeypatch, _keep_message("aa-llama-3.3-70b-versatile"))
        config = load_config()
        record = discover_single("groq", config, aa_catalog, models_dev)

        assert record["provider_model_id"] == "llama-3.3-70b-versatile"
        assert record["decision"] == "keep"
        assert record["tier"] == "max"  # aa_score 55 >= 45
        assert record["aa_model_id"] == "aa-llama-3.3-70b-versatile"
        assert record["aa_score"] == 55.0

    def test_judge_failure_isolates_to_error_record(self, monkeypatch, aa_catalog, models_dev):
        """Judge failure → decision=error, NOT drop. Error is surfaced, not hidden."""
        def boom(*a, **k):
            raise RuntimeError("connection refused")

        monkeypatch.setattr("llm_discovery.llm.httpx.post", boom)
        monkeypatch.setattr(
            "llm_discovery.pipeline.discover_models",
            lambda base_url, api_key: _TRACER_MODELS,
        )
        monkeypatch.setattr("llm_discovery.pipeline.load_all_secrets", lambda config=None: None)
        monkeypatch.setenv("AGNES_AI_API_KEY", "fake-judge-key")
        monkeypatch.setenv("GROQ_API_KEY", "fake-provider-key")

        config = load_config()
        record = discover_single("groq", config, aa_catalog, models_dev)

        assert record["source"] == "llm_error"
        assert record["decision"] == "error"     # was "drop" — now "error"
        assert record["tier"] == "error"          # was "drop" — now "error"
        assert record["aa_score"] is None
        assert "connection refused" in record["evidence"][0]

    def test_specialized_model_never_reaches_judge(self, monkeypatch, aa_catalog, models_dev):
        """TTS/STT models are dropped deterministically before the judge is called."""
        def boom(*a, **k):
            raise RuntimeError("judge should not have been called")

        monkeypatch.setattr("llm_discovery.llm.httpx.post", boom)
        monkeypatch.setattr(
            "llm_discovery.pipeline.discover_models",
            lambda base_url, api_key: [{"id": "openai/whisper-tiny"}],
        )
        monkeypatch.setattr("llm_discovery.pipeline.load_all_secrets", lambda config=None: None)
        monkeypatch.setenv("AGNES_AI_API_KEY", "fake-judge-key")
        monkeypatch.setenv("GROQ_API_KEY", "fake-provider-key")

        config = load_config()
        record = discover_single("groq", config, aa_catalog, models_dev)

        assert record["decision"] == "drop"
        assert record["tier"] == "drop"
        assert record["source"] == "deterministic"


# --------------------------------------------------------------------------- #
# discover_all_providers — provider isolation
# --------------------------------------------------------------------------- #
class TestDiscoverAllProvidersIsolation:
    def test_broken_provider_does_not_abort_run(self, monkeypatch, aa_catalog, models_dev):
        """One provider raising HTTP 404 must not abort the rest."""
        call_count = {"n": 0}

        def fake_discover_models(base_url, api_key):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First provider (groq) succeeds
                return _TRACER_MODELS
            # Second provider raises 404 during discovery
            from httpx import HTTPStatusError
            resp = _FakeResponse({}, status_code=404)
            raise HTTPStatusError("404 Not Found", request=None, response=resp)

        monkeypatch.setattr(
            "llm_discovery.pipeline.discover_models",
            fake_discover_models,
        )
        monkeypatch.setattr("llm_discovery.pipeline.load_all_secrets", lambda config=None: None)
        # Set judge key + all provider keys so every provider passes
        # the key check and reaches discover_models.
        monkeypatch.setenv("AGNES_AI_API_KEY", "fake-judge-key")
        monkeypatch.setenv("GROQ_API_KEY", "fake-provider-key")
        monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
        monkeypatch.setenv("LLM7_API_KEY", "fake-llm7-key")
        monkeypatch.setenv("MISTRAL_API_KEY", "fake-mistral-key")
        monkeypatch.setenv("OPENCODE_ZEN_API_KEY", "fake-zen-key")
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake-openrouter-key")

        config = load_config()
        all_results = discover_all_providers(config, aa_catalog, models_dev, max_workers=1)

        # groq must have a result with keep/drop/error buckets.
        assert "groq" in all_results
        assert isinstance(all_results["groq"]["keep"], list)
        # google must be present with a discovery error (404).
        assert "google" in all_results
        provider_result = all_results["google"]
        assert len(provider_result["error"]) > 0
        assert provider_result["error"][0]["stage"] == "discovery"
        assert "404" in provider_result["error"][0]["evidence"][0]

    def test_error_record_has_stage_and_evidence(self, aa_catalog):
        """provider_error_result produces records with stage + evidence."""
        from httpx import HTTPStatusError

        resp = _FakeResponse({}, status_code=404)
        exc = HTTPStatusError("404 Not Found", request=None, response=resp)
        result = provider_error_result("llm7", exc)

        assert result["keep"] == []
        assert result["drop"] == []
        assert len(result["error"]) == 1
        rec = result["error"][0]
        assert rec["decision"] == "error"
        assert rec["tier"] == "error"
        assert rec["stage"] == "discovery"
        assert "404" in rec["evidence"][0]

    def test_classify_provider_error(self):
        """classify_provider_error returns (stage, detail) tuples."""
        from httpx import HTTPStatusError

        # 404
        resp = _FakeResponse({}, status_code=404)
        exc = HTTPStatusError("404 Not Found", request=None, response=resp)
        stage, detail = classify_provider_error(exc)
        assert stage == "discovery"
        assert "404" in detail

        # 401
        resp = _FakeResponse({}, status_code=401)
        exc = HTTPStatusError("401 Unauthorized", request=None, response=resp)
        stage, detail = classify_provider_error(exc)
        assert stage == "authentication"

        # connection refused
        exc = RuntimeError("Connection refused")
        stage, detail = classify_provider_error(exc)
        assert stage == "discovery"

        # LLM evaluation error
        exc = RuntimeError("LLM evaluation failed: timeout")
        stage, detail = classify_provider_error(exc)
        assert stage == "evaluation"