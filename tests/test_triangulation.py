"""Triangulation for no-AA / claim-only models (issue #39)."""
from types import SimpleNamespace

from llm_discovery.benchmarks import BenchmarkProfile
from llm_discovery.evaluation import ModelEvaluation
from llm_discovery.llm import SYSTEM_PROMPT
from llm_discovery.policy_gate import PolicyGate


def _profile_empty():
    return BenchmarkProfile(model_id="x", provider="test", scores={})


def _profile_with_bench():
    return BenchmarkProfile(
        model_id="x",
        provider="test",
        scores={"humaneval": {"score": 35.0, "metric": "%", "source": "test"}},
    )


def _resolution_none():
    return SimpleNamespace(aa_model=None)


def _llm_result(level, evidence):
    return ModelEvaluation(
        coding=True,
        aa_relevance="weak",
        confidence=0.8,
        decision="keep",
        evidence_level=level,
        evidence=evidence,
        coding_assessment=None,
    )


class TestTriangulationGuard:
    def test_unverified_moderate_demoted_to_weak(self):
        gate = PolicyGate(min_score=24.0, max_score=45.0, cache=None)
        profile = _profile_empty()
        llm = _llm_result("moderate", ["Supports Python, JS per model card"])
        rec = gate.apply(llm, _resolution_none(), "gpt-oss", "openai", profile=profile)
        assert rec["evidence_level"] == "weak"
        assert any("demoted" in e for e in rec["evidence"])

    def test_verified_moderate_kept(self):
        gate = PolicyGate(min_score=24.0, max_score=45.0, cache=None)
        profile = _profile_empty()
        llm = _llm_result(
            "moderate",
            ["Supports Python, JS per model card (source: https://openai.com/index/gpt-oss)"],
        )
        rec = gate.apply(llm, _resolution_none(), "gpt-oss", "openai", profile=profile)
        assert rec["evidence_level"] == "moderate"

    def test_verified_moderate_with_http_kept(self):
        gate = PolicyGate(min_score=24.0, max_score=45.0, cache=None)
        profile = _profile_empty()
        llm = _llm_result(
            "moderate",
            ["Supports Python, JS (source: https://mistral.ai/news/mistral-nemo)"],
        )
        rec = gate.apply(llm, _resolution_none(), "mistral-Nemo-Instruct-2407", "mistral", profile=profile)
        assert rec["evidence_level"] == "moderate"

    def test_weak_stays_weak_even_without_url(self):
        gate = PolicyGate(min_score=24.0, max_score=45.0, cache=None)
        profile = _profile_empty()
        llm = _llm_result("weak", ["No evidence"])
        rec = gate.apply(llm, _resolution_none(), "seed-2.0-mini", "bytedance", profile=profile)
        assert rec["evidence_level"] == "weak"

    def test_strong_never_demoted_even_without_url(self):
        gate = PolicyGate(min_score=24.0, max_score=45.0, cache=None)
        profile = _profile_empty()
        llm = _llm_result("strong", ["Strong claim without URL"])
        rec = gate.apply(llm, _resolution_none(), "gpt-oss", "openai", profile=profile)
        # LLM strong should not be demoted by triangulation guard
        assert rec["evidence_level"] == "strong"

    def test_moderate_with_benchmark_not_demoted_even_without_url(self):
        gate = PolicyGate(min_score=24.0, max_score=45.0, cache=None)
        profile = _profile_with_bench()
        llm = _llm_result("moderate", ["HumanEval 35"])
        rec = gate.apply(llm, _resolution_none(), "some-model", "test", profile=profile)
        # Has benchmark score >=30 => deterministic moderate, so guard does not apply
        assert rec["evidence_level"] == "moderate"

    def test_chroma_still_weak_when_unverified(self):
        gate = PolicyGate(min_score=24.0, max_score=45.0, cache=None)
        profile = _profile_empty()
        llm = _llm_result("moderate", ["Supports coding languages"])
        rec = gate.apply(llm, _resolution_none(), "chroma-v.46-flash", "chroma", profile=profile)
        assert rec["evidence_level"] == "weak"


class TestSystemPromptTriangulation:
    def test_prompt_contains_provider_native_checklist(self):
        assert "provider-native" in SYSTEM_PROMPT
        assert "first-party" in SYSTEM_PROMPT
        assert "(source: <url>)" in SYSTEM_PROMPT
        assert "No URL -> unverified" in SYSTEM_PROMPT

    def test_prompt_contains_search_queries(self):
        assert "model card coding programming languages" in SYSTEM_PROMPT
        assert "coding benchmark HumanEval SWE-bench" in SYSTEM_PROMPT
        assert "at most 2 web searches" in SYSTEM_PROMPT

    def test_prompt_contains_moderate_verified_rule(self):
        assert ">=2 programming languages" in SYSTEM_PROMPT
        assert "unverified claim stays weak" in SYSTEM_PROMPT

    def test_prompt_mini_never_moderate(self):
        assert "mini/small/lite/nano never moderate via claim alone" in SYSTEM_PROMPT

    def test_prompt_claim_only_never_strong(self):
        assert "Claim-only never reaches strong without a benchmark number" in SYSTEM_PROMPT
