"""Tests for model_info_store schema & key normalization (issue #66)."""
import pytest
from llm_discovery.model_info_store import (
    normalize_store_key,
    normalized_key_with_matcher,
    should_cache,
    evidence_level_rank,
    FIELD_INCLUSION_MATRIX,
    CACHEABLE_LEVELS,
    is_pricing_outlier,
    aggregate_pricing,
    merge_records,
    ModelInfoRecord,
    BenchmarkSnapshot,
    PricingSnapshot,
    StoreMeta,
    RECOMMENDED_STORE_PATH,
)


class TestNormalizeStoreKey:
    def test_provider_prefix_stripped(self):
        assert normalize_store_key("openai/gpt-4o:free") == "gpt-4o"
        assert normalize_store_key("MiniMax/MiniMax-M3") == "minimax-m3"
        assert normalize_store_key("meta/muse-spark-1.2") == "muse-spark-1.2"

    def test_free_variants_stripped(self):
        assert normalize_store_key("minimax-m3-free") == "minimax-m3"
        assert normalize_store_key("minimax-m3:free") == "minimax-m3"
        assert normalize_store_key("minimax-m3_free") == "minimax-m3"
        assert normalize_store_key("minimax-m3/free") == "minimax-m3"

    def test_case_insensitive(self):
        assert normalize_store_key("GPT-4O") == "gpt-4o"
        assert normalize_store_key("MiniMax-M3") == "minimax-m3"

    def test_stepfun_normalization(self):
        assert normalize_store_key("stepfun/step-2.5-free") == "step-2.5"
        assert normalize_store_key("stepfun-2.5-free") == "step-2.5"
        assert normalize_store_key("step/step-2.5") == "step-2.5"
        assert normalize_store_key("STEPFUN-2.5_FREE") == "step-2.5"

    def test_vendor_suffix_kept(self):
        assert normalize_store_key("muse-spark-1.2-contributor-free") == "muse-spark-1.2-contributor"
        assert normalize_store_key("qwen3.8-flash-next") == "qwen3.8-flash-next"
        assert normalize_store_key("qwen3.8-flash-free") == "qwen3.8-flash"

    def test_dot_preserved(self):
        assert normalize_store_key("qwen3.8-flash") == "qwen3.8-flash"
        assert normalize_store_key("qwen-3.8-flash") == "qwen-3.8-flash"

    def test_whitespace_and_empty(self):
        assert normalize_store_key("  openai/gpt-4o  ") == "gpt-4o"
        assert normalize_store_key("") == ""
        assert normalize_store_key("   ") == ""

    def test_cross_provider_collapse(self):
        assert normalize_store_key("groq/llama-3.3-70b-versatile") == normalize_store_key("openrouter/llama-3.3-70b-versatile")
        assert normalize_store_key("a/gpt-4o:free") == normalize_store_key("b/gpt-4o")

    def test_with_matcher_folds_dots(self):
        k1 = normalized_key_with_matcher("qwen3.8-flash")
        k2 = normalized_key_with_matcher("qwen-3.8-flash")
        assert k1
        assert k2


class TestEvidenceGating:
    def test_cacheable_levels(self):
        assert should_cache("strong") is True
        assert should_cache("moderate") is True
        assert should_cache("Strong") is True
        assert should_cache("MODERATE") is True
        assert should_cache("weak") is False
        assert should_cache("none") is False
        assert should_cache(None) is False
        assert should_cache("") is False
        assert should_cache("weak", confidence=1.0) is False

    def test_field_inclusion_matrix(self):
        for lvl in ("strong", "moderate"):
            assert all(FIELD_INCLUSION_MATRIX[lvl].values()), lvl
        for lvl in ("weak", "none"):
            assert not any(FIELD_INCLUSION_MATRIX[lvl].values()), lvl

    def test_rank_ordering(self):
        assert evidence_level_rank("strong") > evidence_level_rank("moderate")
        assert evidence_level_rank("moderate") > evidence_level_rank("weak")
        assert evidence_level_rank("weak") > evidence_level_rank("none")
        assert evidence_level_rank(None) == 0


class TestPricingAggregation:
    def test_single_observation_stored_as_is(self):
        obs = [{"blended": 0.5, "input": 0.3, "output": 0.9, "provider": "a"}]
        agg = aggregate_pricing(obs)
        assert agg["blended"] == 0.5
        assert agg["per_provider_overrides"] == {}

    def test_avg_without_outlier(self):
        obs = [
            {"blended": 0.5, "input": 0.3, "output": 0.9, "provider": "a"},
            {"blended": 0.51, "input": 0.31, "output": 0.91, "provider": "b"},
        ]
        agg = aggregate_pricing(obs)
        assert agg["blended"] == pytest.approx(0.505)
        assert agg["per_provider_overrides"] == {}

    def test_outlier_excluded(self):
        obs = [
            {"blended": 0.5, "input": 0.3, "output": 0.9, "provider": "a"},
            {"blended": 0.52, "input": 0.31, "output": 0.91, "provider": "b"},
            {"blended": 1.5, "input": 1.0, "output": 2.0, "provider": "c"},
        ]
        agg = aggregate_pricing(obs)
        assert agg["blended"] == pytest.approx(0.51)
        assert "c" in agg["per_provider_overrides"]
        assert agg["per_provider_overrides"]["c"]["blended"] == 1.5

    def test_outlier_threshold(self):
        assert is_pricing_outlier(1.5, 0.5) is True
        assert is_pricing_outlier(0.51, 0.5) is False
        assert is_pricing_outlier(0.6, 0.5) is False
        assert is_pricing_outlier(0.8, 0.5) is True

    def test_raw_aa_shape(self):
        obs = [{"price_1m_blended_3_to_1": 0.237, "price_1m_input_tokens": 0.15, "price_1m_output_tokens": 0.5, "provider": "x"}]
        agg = aggregate_pricing(obs)
        assert agg["blended"] == 0.237

    def test_empty_returns_none(self):
        assert aggregate_pricing([]) is None
        assert aggregate_pricing([{}, {"blended": None}]) is None


class TestMergeRecords:
    def test_strong_wins_over_moderate(self):
        # #72 Q10 b: pricing re-avg, other scalars gap-fill only — existing value kept even if incoming stronger
        r1 = ModelInfoRecord(aa_score=50, evidence_level="moderate", confidence=0.8, evidence=["x"], _meta=StoreMeta(last_updated="2026-09-04T01:00:00+00:00", source_providers=["a"]))
        r2 = ModelInfoRecord(aa_score=55, evidence_level="strong", confidence=0.9, evidence=["y"], _meta=StoreMeta(last_updated="2026-09-04T02:00:00+00:00", source_providers=["b"]))
        m = merge_records(r1, r2)
        assert m.aa_score == 50  # gap-fill only, keep existing
        assert m.evidence == ["x"]
        assert set(m._meta.source_providers) == {"a", "b"}

    def test_gap_fill(self):
        r1 = ModelInfoRecord(aa_model_id=None, aa_score=None, evidence_level="strong", _meta=StoreMeta(last_updated="2026-09-04T01:00:00+00:00"))
        r2 = ModelInfoRecord(aa_model_id="m1", aa_score=60, evidence_level="strong", _meta=StoreMeta(last_updated="2026-09-04T02:00:00+00:00"))
        m = merge_records(r1, r2)
        assert m.aa_model_id == "m1"
        assert m.aa_score == 60

    def test_benchmark_union_max(self):
        b1 = BenchmarkSnapshot(scores={"aa_intelligence": {"score": 50, "source": "aa"}, "swe_bench_verified": {"score": 70, "source": "dev"}})
        b2 = BenchmarkSnapshot(scores={"aa_intelligence": {"score": 55, "source": "aa"}, "livecodebench": {"score": 60, "source": "lc"}})
        r1 = ModelInfoRecord(benchmarks=b1, evidence_level="moderate", _meta=StoreMeta(last_updated="2026-09-04T01:00:00+00:00"))
        r2 = ModelInfoRecord(benchmarks=b2, evidence_level="moderate", _meta=StoreMeta(last_updated="2026-09-04T02:00:00+00:00"))
        m = merge_records(r1, r2)
        assert m.benchmarks.scores["aa_intelligence"]["score"] == 55
        assert m.benchmarks.scores["swe_bench_verified"]["score"] == 70
        assert m.benchmarks.scores["livecodebench"]["score"] == 60

    def test_none_existing(self):
        r2 = ModelInfoRecord(aa_score=10, evidence_level="strong", _meta=StoreMeta(last_updated="2026-09-04T01:00:00+00:00"))
        m = merge_records(None, r2)
        assert m.aa_score == 10

    def test_provenance_union(self):
        r1 = ModelInfoRecord(evidence_level="strong", _meta=StoreMeta(source_providers=["groq"], source_evidence_levels=["strong"], last_updated="2026-09-04T01:00:00+00:00"))
        r2 = ModelInfoRecord(evidence_level="moderate", _meta=StoreMeta(source_providers=["openrouter"], source_evidence_levels=["moderate"], last_updated="2026-09-04T02:00:00+00:00"))
        m = merge_records(r1, r2)
        assert set(m._meta.source_providers) == {"groq", "openrouter"}


class TestSchema:
    def test_recommended_path(self):
        assert RECOMMENDED_STORE_PATH == "data/model_info_store.json"

    def test_record_roundtrip(self):
        rec = ModelInfoRecord(
            aa_model_id="muse-spark-1-2",
            aa_score=56.8,
            coding_score=58.3,
            benchmarks=BenchmarkSnapshot(scores={"aa_intelligence": {"score": 56.8}}),
            evidence=["AA 56.8"],
            evidence_level="strong",
            confidence=0.92,
            tier="flash",
            pricing=PricingSnapshot(blended=0.45, input=0.3, output=0.9),
        )
        d = rec.to_dict()
        rec2 = ModelInfoRecord.from_dict(d)
        assert rec2.aa_model_id == "muse-spark-1-2"
        assert rec2.tier == "flash"
        assert rec2.pricing.blended == 0.45

    def test_from_provider_record(self):
        rec = {
            "provider_model_id": "muse-spark-1.2-contributor:free",
            "aa_model_id": "muse-spark-1-2",
            "aa_score": 56.8,
            "coding_score": 58.3,
            "tier": "contributor_special",
            "confidence": 0.9,
            "evidence_level": "strong",
            "evidence": ["x"],
            "benchmarks": {"scores": {"aa_intelligence": {"score": 56.8}}, "raw_benchmarks": []},
            "pricing": {"price_1m_blended_3_to_1": 0.45, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9},
        }
        mir = ModelInfoRecord.from_provider_record(rec, provider="groq", evaluated_at="2026-09-04T05:00:00+00:00")
        assert mir.tier == "contributor_free"
        assert mir.pricing.blended == 0.45
        assert mir._meta.source_providers == ["groq"]
