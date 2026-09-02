"""T1 (issue #22) seam tests for EvidenceCollector.

Pins the evidence-collection behaviour that used to live in the free function
build_evidence_packet(), now exposed as EvidenceCollector.collect(model, cache,
models_dev, resolution). Independent of the model resolver, so these stay green
while the resolver (T6/#29) WIP is reconciled separately.
"""
from types import SimpleNamespace

from llm_discovery.evidence import (
    BenchmarkEvidence,
    EvidenceCategory,
    EvidenceCollector,
    EvidencePacket,
    EvidencePolarity,
    EvidenceSource,
    ProviderClaim,
)


class _FakeModelsDev:
    """Minimal ModelsDevCatalog stand-in for description-based tests."""

    def __init__(self, models=None):
        self._models = models or {}

    def get_model(self, model_id):
        return self._models.get(model_id)


def _resolution(aa_model):
    return SimpleNamespace(aa_model=aa_model)


# Acceptance: standalone factory deleted; collector exists with collect().
def test_build_evidence_packet_deleted_collector_exists():
    import llm_discovery.evidence as ev
    assert not hasattr(ev, "build_evidence_packet")
    assert hasattr(EvidenceCollector, "collect")
    assert callable(getattr(EvidenceCollector, "collect"))


def test_collect_signature_contracts():
    import inspect
    params = list(inspect.signature(EvidenceCollector.collect).parameters)
    assert params == ["self", "model", "cache", "models_dev", "resolution"]


def test_provider_name_captured_at_construction():
    packet = EvidenceCollector("groq").collect(
        {"id": "openai/whisper-base"}, None, _FakeModelsDev(), _resolution(None)
    )
    assert packet.provider == "groq"
    assert packet.model_id == "openai/whisper-base"


def test_specialized_model_flag_from_id():
    packet = EvidenceCollector("groq").collect(
        {"id": "openai/whisper-base"}, None, _FakeModelsDev(), _resolution(None)
    )
    assert packet.deterministic_flags == ["specialized_model:whisper"]
    assert packet.is_specialized() is True
    assert packet.aa_match == {"matched": False, "model_id": None, "score": None}
    assert packet.benchmarks == []
    assert packet.provider_claims == []


def test_empty_packet_when_no_evidence():
    packet = EvidenceCollector("acme").collect(
        {"id": "unknown-model-x"}, None, _FakeModelsDev(), _resolution(None)
    )
    expected = EvidencePacket(
        model_id="unknown-model-x",
        provider="acme",
        benchmarks=[],
        provider_claims=[],
        deterministic_flags=[],
        aa_match={"matched": False, "model_id": None, "score": None},
    )
    assert packet == expected


def test_collect_benchmarks_classified_and_aa_matched(aa_catalog, models_dev):
    model = {"id": "llama-3.3-70b-versatile"}
    cache = {
        "llama-3.3-70b-versatile": {
            "swe_bench": {"score": 60.0},
            "humaneval": {"score": 50.0},
        }
    }
    aa_model = aa_catalog.get_by_id("aa-llama-3.3-70b-versatile")
    packet = EvidenceCollector("groq").collect(model, cache, models_dev, _resolution(aa_model))
    expected = EvidencePacket(
        model_id="llama-3.3-70b-versatile",
        provider="groq",
        benchmarks=[
            BenchmarkEvidence(
                source=EvidenceSource.SWE_BENCH,
                name="Swe Bench",
                value=60.0,
                polarity=EvidencePolarity.POSITIVE,
                category=EvidenceCategory.AGENTIC,
            ),
            BenchmarkEvidence(
                source=EvidenceSource.HUMANEVAL,
                name="Humaneval",
                value=50.0,
                polarity=EvidencePolarity.NEUTRAL,
                category=EvidenceCategory.CODING,
            ),
        ],
        provider_claims=[
            ProviderClaim(
                claim="Multilingual chat, reasoning, and coding model",
                source="models_dev",
                strength=EvidencePolarity.POSITIVE,
            ),
        ],
        deterministic_flags=[],
        aa_match={
            "matched": True,
            "model_id": "aa-llama-3.3-70b-versatile",
            "name": "Llama 3.3 70B",
            "score": 55.0,
        },
    )
    assert packet == expected


def test_models_dev_description_drives_claim_and_specialized_flag():
    models_dev = _FakeModelsDev({
        "codestar-pro": {
            "id": "codestar-pro",
            "name": "CodeStar Pro",
            "description": "A coding and reasoning model with text-to-speech support",
        }
    })
    packet = EvidenceCollector("acme").collect(
        {"id": "codestar-pro"}, None, models_dev, _resolution(None)
    )
    expected = EvidencePacket(
        model_id="codestar-pro",
        provider="acme",
        benchmarks=[],
        provider_claims=[
            ProviderClaim(
                claim="A coding and reasoning model with text-to-speech support",
                source="models_dev",
                strength=EvidencePolarity.POSITIVE,
            ),
        ],
        deterministic_flags=["specialized_model:text-to-speech"],
        aa_match={"matched": False, "model_id": None, "score": None},
    )
    assert packet == expected


# Pipeline wiring: evaluate_model builds its packet via EvidenceCollector.
# resolve_model is mocked because the resolver is currently T6/#29 WIP and
# out of #22's scope.
def test_pipeline_uses_evidence_collector(monkeypatch, aa_catalog, models_dev):
    from llm_discovery.evaluation import ModelEvaluation
    from llm_discovery.pipeline import evaluate_model

    fake_aa_model = aa_catalog.get_by_id("aa-llama-3.3-70b-versatile")
    monkeypatch.setattr(
        "llm_discovery.pipeline.resolve_model",
        lambda *a, **k: SimpleNamespace(aa_model=fake_aa_model),
    )

    captured = {}

    class _FakeEvaluator:
        def evaluate(self, request, evidence_packet=None):
            captured["request"] = request
            captured["packet"] = evidence_packet
            return ModelEvaluation(
                canonical_name="Llama 3.3 70B",
                coding=True,
                aa_relevance="strong",
                confidence=0.95,
                decision="keep",
                evidence_level="strong",
                evidence=["coding benchmark", "docs"],
                coding_assessment=None,
            )

    rec = evaluate_model(
        model={"id": "llama-3.3-70b-versatile"},
        provider_name="groq",
        aa=aa_catalog,
        models_dev=models_dev,
        evaluator=_FakeEvaluator(),
        min_score=24.0,
        max_score=45.0,
        cache=None,
    )
    assert rec["decision"] == "keep"
    assert rec["tier"] == "max"
    assert rec["aa_model_id"] == "aa-llama-3.3-70b-versatile"
    assert rec["aa_score"] == 55.0
    # Collector populated provider + aa_match on the packet handed to the judge.
    assert captured["packet"].provider == "groq"
    assert captured["packet"].aa_match["matched"] is True
    assert captured["packet"].aa_match["score"] == 55.0
    assert captured["request"].provider == "groq"
