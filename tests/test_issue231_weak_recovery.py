"""Issue #231: Cheap deterministic recovery before weak/none.

EvaluatorCoordinator.evaluate must, on deterministic weak/none, try cheap recovery in
order (canonical benchmark alias, AA alias/canonical, models.dev metadata, cached
evidence, provider first-party claim), recompute Evidence Level via PolicyGate, and
only then invoke the LLM when appropriate. Thresholds frozen per ADR 0008.

Acceptance criteria:
- weak alias-miss models (glm/mimo/Claude/Gemini) now strong/moderate deterministically
- strong finishes without LLM; moderate goes to LLM
- genuine weak with no claim stays weak without LLM; Candidate TTL gates repeats
- no LLM when cheap recovery already yielded strong
- tests cover weak->strong via alias and weak-stays-weak without claim
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from llm_discovery.benchmarks import BenchmarkDataCache
from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from llm_discovery.candidate_store import CandidateRecord, CandidateStore
from llm_discovery.evaluation import ModelEvaluation
from llm_discovery.evaluator import EvaluatorCoordinator
from llm_discovery.model_info_store import compute_evidence_hash

DATA_DIR = Path("data")


def _make_aa(tmp, models):
    data = {"source": "test", "models": models}
    p = Path(tmp) / "aa.json"
    p.write_text(json.dumps(data))
    return ArtificialAnalysisCatalog(p)


def _make_md(tmp, models_dict=None):
    data = {"models": models_dict or {}, "providers": {}}
    p = Path(tmp) / "md.json"
    p.write_text(json.dumps(data))
    return ModelsDevCatalog(p)


class _CountingFake:
    def __init__(self, result):
        self.calls = 0
        self.result = result

    def evaluate(self, request, packet=None):
        self.calls += 1
        return self.result


def _moderate_llm_result():
    return ModelEvaluation(
        coding=True,
        decision="keep",
        confidence=0.9,
        evidence_level="moderate",
        evidence=["llm http://example.com"],
        coding_assessment=None,
    )


def _fixture_cache(benchmarks_by_key):
    """Benchmark cache with direct _data entries and NO norm index (alias miss by design).

    Direct-key lookup works; dot/hyphen/variant lookups intentionally miss the index,
    so initial screening sees an empty profile and the weak-branch recovery path is
    what must find the evidence.
    """
    cache = BenchmarkDataCache()
    cache._loaded = True
    cache._data = {k: {"benchmarks": v} for k, v in benchmarks_by_key.items()}
    return cache


def _coord(aa, md, cache, fake, candidate_store=None):
    return EvaluatorCoordinator(
        provider_name="test", aa=aa, models_dev=md, evaluator=fake,
        min_score=24, max_score=45, cache=cache, candidate_store=candidate_store,
    )


# ---------------------------------------------------------------------------
# AC5: weak -> strong via canonical benchmark alias, no LLM
# ---------------------------------------------------------------------------
def test_weak_benchmark_alias_miss_recovers_strong_without_llm(tmp_path):
    aa = _make_aa(tmp_path, [])
    md = _make_md(tmp_path)
    cache = _fixture_cache({
        "glm-5-3": {"swe_bench_verified": {"score": 55, "source": "https://example.com/swe"}},
    })
    fake = _CountingFake(_moderate_llm_result())
    coord = _coord(aa, md, cache, fake)
    rec = coord.evaluate({"id": "glm-5.3"})
    assert rec["evidence_level"] == "strong"
    assert rec["decision"] == "keep"
    assert fake.calls == 0
    assert rec["evidence_status"] == "recovered"
    assert any(a.startswith("canonical_benchmark_lookup:glm-5-3") for a in rec["recovery_attempts"])


def test_weak_benchmark_alias_miss_negative_no_merge(tmp_path):
    # gpt-4 must NOT recover from gpt-4o bench data (distinct models)
    aa = _make_aa(tmp_path, [])
    md = _make_md(tmp_path)
    cache = _fixture_cache({
        "gpt-4o": {"swe_bench_verified": {"score": 80, "source": "https://example.com/swe"}},
    })
    fake = _CountingFake(_moderate_llm_result())
    coord = _coord(aa, md, cache, fake)
    rec = coord.evaluate({"id": "gpt-4"})
    assert rec["evidence_level"] == "weak"
    assert rec["decision"] == "uncertain"
    assert fake.calls == 0


# ---------------------------------------------------------------------------
# AC2/AC4: strong finishes without LLM (already deterministic strong)
# ---------------------------------------------------------------------------
def test_strong_aa55_finishes_without_llm(tmp_path):
    aa = _make_aa(tmp_path, [{"id": "aa-s", "name": "S", "slug": "strong-slug",
                              "evaluations": {"artificial_analysis_intelligence_index": 55}}])
    md = _make_md(tmp_path)
    cache = BenchmarkDataCache()
    cache._loaded = True
    cache._data["strong-slug"] = {"benchmarks": {}}
    fake = _CountingFake(_moderate_llm_result())
    coord = _coord(aa, md, cache, fake)
    rec = coord.evaluate({"id": "strong-slug"})
    assert rec["evidence_level"] == "strong"
    assert fake.calls == 0


# ---------------------------------------------------------------------------
# AC3: genuine weak with no claim stays weak without LLM; Candidate TTL gates repeats
# ---------------------------------------------------------------------------
def test_genuine_weak_no_claim_stays_weak_without_llm(tmp_path):
    aa = _make_aa(tmp_path, [])
    md = _make_md(tmp_path)
    cache = _fixture_cache({})
    fake = _CountingFake(_moderate_llm_result())
    coord = _coord(aa, md, cache, fake)
    rec = coord.evaluate({"id": "ghost-model-9"})
    assert rec["evidence_level"] == "weak"
    assert rec["decision"] == "uncertain"
    assert fake.calls == 0
    assert rec["evidence_status"] == "uncertain"
    assert rec.get("evidence_reason")
    assert rec["recovery_attempts"]


def test_genuine_weak_candidate_store_gates_repeat(tmp_path):
    aa = _make_aa(tmp_path, [])
    md = _make_md(tmp_path)
    cache = _fixture_cache({})
    fake = _CountingFake(_moderate_llm_result())
    store = CandidateStore(tmp_path / "candidates.json")
    coord = _coord(aa, md, cache, fake, candidate_store=store)
    first = coord.evaluate({"id": "ghost-model-9"})
    assert first["evidence_level"] == "weak"
    assert fake.calls == 0
    # repeat with identical evidence hash: candidate cache hit, no LLM
    second = coord.evaluate({"id": "ghost-model-9"})
    assert second["evidence_level"] == "weak"
    assert second.get("source") == "candidate_cache"
    assert second.get("cached") is True
    assert fake.calls == 0
    assert store.stats.hits >= 1


def test_weak_with_unverified_claim_but_candidate_hit_skips_llm(tmp_path):
    # Claim present (unverified: non-allowlisted URL) + stale candidate entry -> LLM skipped
    aa = _make_aa(tmp_path, [])
    md = _make_md(tmp_path, {"ghost-coder": {
        "id": "ghost-coder",
        "name": "Ghost Coder",
        "description": "Ghost coding agent for repository edits in python and rust",
        "weights": [{"label": "X", "url": "https://example.com/ghost-labs/ghost-coder"}],
    }})
    cache = _fixture_cache({})
    fake = _CountingFake(_moderate_llm_result())
    store = CandidateStore(tmp_path / "candidates.json")
    rec_entry = CandidateRecord(
        model_id="ghost-coder",
        evidence_hash=compute_evidence_hash(None, {"old": {"score": 1}}, None, []),  # stale hash
        evidence_level="weak",
        decision="uncertain",
        tier="uncertain",
        last_updated=datetime.now(UTC).isoformat(),
    )
    store.put("ghost-coder", rec_entry)
    coord = _coord(aa, md, cache, fake, candidate_store=store)
    out = coord.evaluate({"id": "ghost-coder"})
    assert out["evidence_level"] == "weak"
    assert fake.calls == 0  # claim exists but candidate cache gates the LLM


# ---------------------------------------------------------------------------
# weak with claim (no candidate cache) -> bounded LLM/web recovery
# ---------------------------------------------------------------------------
def test_weak_with_claim_triggers_bounded_llm_recovery(tmp_path):
    aa = _make_aa(tmp_path, [])
    md = _make_md(tmp_path, {"ghost-coder": {
        "id": "ghost-coder",
        "name": "Ghost Coder",
        "description": "Ghost coding agent for repository edits in python and rust",
        "weights": [{"label": "X", "url": "https://example.com/ghost-labs/ghost-coder"}],
    }})
    cache = _fixture_cache({})
    # LLM evidence carries an allowlisted URL so the triangulation guard keeps the
    # claim-informed moderate (a non-allowlisted URL would demote claim-only moderate).
    allowlisted_moderate = ModelEvaluation(
        coding=True,
        decision="keep",
        confidence=0.9,
        evidence_level="moderate",
        evidence=["https://huggingface.co/ghost-labs/ghost-coder"],
        coding_assessment=None,
    )
    fake = _CountingFake(allowlisted_moderate)
    coord = _coord(aa, md, cache, fake)
    rec = coord.evaluate({"id": "ghost-coder"})
    assert fake.calls == 1  # exactly one bounded LLM pass
    assert "llm_web_recovery" in rec["recovery_attempts"]
    assert rec["evidence_level"] == "moderate"


# ---------------------------------------------------------------------------
# models.dev metadata recovery (step 3 of the fixed cheap-recovery order)
# ---------------------------------------------------------------------------
def test_models_dev_metadata_recovers_strong_without_llm(tmp_path):
    aa = _make_aa(tmp_path, [])
    md = _make_md(tmp_path, {"hy3": {
        "id": "hy3", "name": "Hy3",
        "description": "reasoning model",
        "benchmarks": [{"name": "SWE-Bench Verified", "score": 55, "metric": "resolved",
                        "source": "https://huggingface.co/tencent/Hy3"}],
    }})
    cache = _fixture_cache({})
    fake = _CountingFake(_moderate_llm_result())
    coord = _coord(aa, md, cache, fake)
    rec = coord.evaluate({"id": "hy3"})
    assert rec["evidence_level"] == "strong"
    assert fake.calls == 0
    assert rec["evidence_status"] == "recovered"


def test_models_dev_metadata_variant_recovers_moderate_then_llm(tmp_path):
    # provider id hy3.1 -> safe hyphen variant hy3-1 in models.dev; AA index 30 -> moderate
    aa = _make_aa(tmp_path, [])
    md = _make_md(tmp_path, {"hy3-1": {
        "id": "hy3-1", "name": "Hy3.1",
        "description": "reasoning model",
        "benchmarks": [{"name": "Artificial Analysis Intelligence Index", "score": 30,
                        "metric": "index", "source": "https://huggingface.co/tencent/Hy3-1"}],
    }})
    cache = _fixture_cache({})
    fake = _CountingFake(_moderate_llm_result())
    coord = _coord(aa, md, cache, fake)
    rec = coord.evaluate({"id": "hy3.1"})
    assert rec["evidence_level"] == "moderate"
    assert fake.calls == 1  # moderate falls through to the LLM judge as before


# ---------------------------------------------------------------------------
# Real-data alias-miss cases (AC1/AC4): glm / mimo / Claude / Gemini
# ---------------------------------------------------------------------------
def _real_catalogs():
    aa_path = DATA_DIR / "artificial_analysis_models.json"
    md_path = DATA_DIR / "models_dev_catalog.json"
    if not (aa_path.exists() and md_path.exists()):
        pytest.skip("real catalog data missing")
    aa = ArtificialAnalysisCatalog(aa_path)
    md = ModelsDevCatalog(md_path)
    cache = BenchmarkDataCache()
    cache.collect_from_local(aa, md)
    return aa, md, cache


def test_real_glm_dot_hyphen_strong_without_llm():
    aa, md, cache = _real_catalogs()
    fake = _CountingFake(_moderate_llm_result())
    coord = _coord(aa, md, cache, fake)
    rec = coord.evaluate({"id": "glm-5.3"})
    assert rec["evidence_level"] == "strong"
    assert fake.calls == 0


def test_real_gemini_preview_alias_strong_without_llm():
    aa, md, cache = _real_catalogs()
    fake = _CountingFake(_moderate_llm_result())
    coord = _coord(aa, md, cache, fake)
    rec = coord.evaluate({"id": "gemini-2.5-pro-preview-06-05"})
    assert rec["evidence_level"] in ("strong", "moderate")
    if rec["evidence_level"] == "strong":
        assert fake.calls == 0


def test_real_mimo_moderate_goes_to_llm():
    aa, md, cache = _real_catalogs()
    fake = _CountingFake(_moderate_llm_result())
    coord = _coord(aa, md, cache, fake)
    rec = coord.evaluate({"id": "mimo-v2.5"})
    assert rec["evidence_level"] == "moderate"
    assert fake.calls == 1  # AC2: moderate still goes to the LLM


def test_real_claude_alias_weak_stays_weak_without_llm():
    aa, md, cache = _real_catalogs()
    for qid in ("claude-3-5-sonnet", "claude-sonnet-3-5"):
        fake = _CountingFake(_moderate_llm_result())
        coord = _coord(aa, md, cache, fake)
        rec = coord.evaluate({"id": qid})
        assert rec["evidence_level"] == "weak", qid
        assert rec["decision"] == "uncertain", qid
        assert fake.calls == 0, qid
        assert rec["evidence_status"] == "uncertain", qid
        assert rec.get("evidence_reason"), qid
        assert rec["recovery_attempts"], qid
