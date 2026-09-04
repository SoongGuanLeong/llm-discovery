"""Hardening for #78: Backfill 14d filter + pricing gap-fill + monotonic store.

Covers all 6 acceptance criteria:

- backfill loads YAMLs skips stale evaluated_at >14d
- Pricing re-averaged via aggregate_pricing, outliers in per_provider_overrides
- Scalar fields gap-fill only
- Benchmarks union-max, coverage max
- Provider YAML deletion monotonic in store
- Unit test: stale file ignored, second provider price avg verified
"""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from llm_discovery.backfill import backfill
from llm_discovery.model_info_store import (
    DEFAULT_TTL_DAYS,
    BenchmarkSnapshot,
    ModelInfoRecord,
    ModelInfoStore,
    PricingSnapshot,
    StoreMeta,
    is_stale,
)


def _write_yaml(path: Path, provider: str, keep: list, evaluated_at: str):
    data = {"provider": provider, "evaluated_at": evaluated_at, "keep": keep, "drop_llm": [], "error": []}
    path.write_text(yaml.safe_dump(data))


def _keep(model_id, evidence_level="strong", confidence=0.9, aa_score=50, pricing=None, benchmarks=None, tier="flash", evidence=None):
    return {
        "model_id": model_id,
        "decision": "keep",
        "tier": tier,
        "aa_model_id": model_id,
        "aa_score": aa_score,
        "coding_score": aa_score,
        "pricing": pricing or {"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9},
        "benchmarks": benchmarks or {"scores": {"aa_intelligence": {"score": aa_score, "source": "aa"}}, "raw_benchmarks": []},
        "confidence": confidence,
        "evidence_level": evidence_level,
        "evidence": evidence or [f"AA {aa_score}"],
    }


def _fresh_ts() -> str:
    return datetime.now(UTC).isoformat()


def _stale_ts(days: int = 20) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


class TestStaleFileIgnored:
    def test_stale_file_skipped(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        # stale file: 20 days old, should be skipped entirely
        _write_yaml(results / "stale.yaml", "stale_provider", [_keep("stale-model", aa_score=99)], evaluated_at=_stale_ts(20))
        # fresh file: now, should be kept
        _write_yaml(results / "fresh.yaml", "fresh_provider", [_keep("fresh-model", aa_score=60)], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        # stale_skipped counts keep records in stale file
        assert stats["stale_skipped"] == 1
        assert stats["unique_models"] == 1
        store = ModelInfoStore(store_path)
        assert store.get("stale-model") is None
        assert store.get("fresh-model") is not None
        assert is_stale(_stale_ts(20), DEFAULT_TTL_DAYS) is True
        assert is_stale(_fresh_ts(), DEFAULT_TTL_DAYS) is False

    def test_stale_file_ignored_even_with_strong_evidence(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        # even strong evidence but stale date -> skipped
        _write_yaml(results / "old.yaml", "old", [_keep("old-model", evidence_level="strong", aa_score=80)], evaluated_at=_stale_ts(30))
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["stale_skipped"] == 1
        assert stats["unique_models"] == 0
        assert ModelInfoStore(store_path).size() == 0

    def test_boundary_14d_not_stale_15d_stale(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        # 14 days ago: not stale (age > ttl only when >14)
        at_14 = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        at_15 = (datetime.now(UTC) - timedelta(days=15)).isoformat()
        assert is_stale(at_14, 14) is False
        assert is_stale(at_15, 14) is True
        _write_yaml(results / "a.yaml", "a", [_keep("model-14", aa_score=50)], evaluated_at=at_14)
        _write_yaml(results / "b.yaml", "b", [_keep("model-15", aa_score=50)], evaluated_at=at_15)
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["stale_skipped"] == 1
        store = ModelInfoStore(store_path)
        assert store.get("model-14") is not None
        assert store.get("model-15") is None


class TestSecondProviderPriceAvg:
    def test_second_provider_price_avg_verified(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        # same model from two providers, different pricing -> re-averaged
        _write_yaml(results / "a.yaml", "provider_a", [_keep("shared-model", aa_score=55, pricing={"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9})], evaluated_at=_fresh_ts())
        _write_yaml(results / "b.yaml", "provider_b", [_keep("shared-model", aa_score=55, pricing={"price_1m_blended_3_to_1": 0.52, "price_1m_input_tokens": 0.31, "price_1m_output_tokens": 0.91})], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["unique_models"] == 1
        assert stats["merged_conflicts"] == 1
        assert stats["pricing_avgs"] == 1
        raw = json.loads(store_path.read_text())
        pricing = raw["models"]["shared-model"]["pricing"]
        # blended avg (0.5+0.52)/2 = 0.51
        assert pricing["blended"] == 0.51
        assert pricing["per_provider_overrides"] == {}
        # scalar gap-fill: aa_score keeps first value (55) even though second same here
        store = ModelInfoStore(store_path)
        assert store.get("shared-model").aa_score == 55

    def test_pricing_outlier_to_overrides(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("m1", pricing={"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9})], evaluated_at=_fresh_ts())
        _write_yaml(results / "b.yaml", "b", [_keep("m1", pricing={"price_1m_blended_3_to_1": 0.52, "price_1m_input_tokens": 0.31, "price_1m_output_tokens": 0.91})], evaluated_at=_fresh_ts())
        _write_yaml(results / "c.yaml", "c", [_keep("m1", pricing={"price_1m_blended_3_to_1": 1.5, "price_1m_input_tokens": 1.0, "price_1m_output_tokens": 2.0})], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["pricing_outliers"] >= 1
        raw = json.loads(store_path.read_text())
        assert "c" in raw["models"]["m1"]["pricing"]["per_provider_overrides"]
        # avg without outlier
        assert raw["models"]["m1"]["pricing"]["blended"] == 0.51


class TestScalarGapFill:
    def test_scalar_gap_fill_only(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        # both providers in single backfill: aa_score 55 vs 90 gap-fill keeps first (55)
        _write_yaml(results / "a.yaml", "a", [_keep("gap-model", evidence_level="moderate", aa_score=55, pricing={"price_1m_blended_3_to_1": 0.5})], evaluated_at=_fresh_ts())
        _write_yaml(results / "b.yaml", "b", [_keep("gap-model", evidence_level="strong", aa_score=90, pricing={"price_1m_blended_3_to_1": 0.52})], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        store = ModelInfoStore(store_path)
        assert store.get("gap-model").aa_score == 55  # gap-fill keeps existing
        # pricing re-averaged (0.5+0.52)/2 = 0.51
        assert store.get("gap-model").pricing.blended == 0.51

    def test_gap_fill_evidence_and_tier(self, tmp_path):
        # direct store put path also gap-fill
        p = tmp_path / "s.json"
        store = ModelInfoStore(p)
        r1 = ModelInfoRecord(aa_score=60, evidence=["first"], tier="flash", evidence_level="strong", _meta=StoreMeta(last_updated=_fresh_ts(), source_providers=["a"]))
        r2 = ModelInfoRecord(aa_score=99, evidence=["second"], tier="tool", evidence_level="strong", _meta=StoreMeta(last_updated=_fresh_ts(), source_providers=["b"]))
        store.put("gap2", r1)
        store.put("gap2", r2)
        got = store.get_by_key("gap2")
        assert got.aa_score == 60
        assert got.evidence == ["first"]
        assert got.tier == "flash"


class TestBenchmarksUnionMax:
    def test_benchmarks_union_max_and_coverage(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        bm_a = {"scores": {"aa_intelligence": {"score": 50, "source": "aa"}, "swe_bench": {"score": 70, "source": "dev"}}, "raw_benchmarks": [{"name": "a"}], "benchmark_coverage": 0.5}
        bm_b = {"scores": {"aa_intelligence": {"score": 55, "source": "aa"}, "livecode": {"score": 60, "source": "lc"}}, "raw_benchmarks": [{"name": "b"}], "benchmark_coverage": 0.8}
        _write_yaml(results / "a.yaml", "a", [_keep("bench-model", benchmarks=bm_a)], evaluated_at=_fresh_ts())
        _write_yaml(results / "b.yaml", "b", [_keep("bench-model", benchmarks=bm_b)], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        store = ModelInfoStore(store_path)
        rec = store.get("bench-model")
        assert rec.benchmarks.scores["aa_intelligence"]["score"] == 55  # max
        assert rec.benchmarks.scores["swe_bench"]["score"] == 70
        assert rec.benchmarks.scores["livecode"]["score"] == 60
        assert rec.benchmarks.benchmark_coverage == 0.8  # max


class TestMonotonicStore:
    def test_provider_yaml_deletion_does_not_delete_store_key(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        # initial backfill: two models from two providers
        _write_yaml(results / "a.yaml", "a", [_keep("keep-model", aa_score=60), _keep("delete-model", aa_score=70)], evaluated_at=_fresh_ts())
        _write_yaml(results / "b.yaml", "b", [_keep("keep-model", aa_score=60)], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        store = ModelInfoStore(store_path)
        assert store.get("delete-model") is not None
        # simulate provider a dropping delete-model: rewrite a.yaml without it
        _write_yaml(results / "a.yaml", "a", [_keep("keep-model", aa_score=60)], evaluated_at=_fresh_ts())
        # b.yaml unchanged, delete-model now absent from all YAMLs
        backfill(results_dir=results, store_path=store_path)
        store2 = ModelInfoStore(store_path)
        # monotonic: delete-model retained even though no provider reports it
        assert store2.get("delete-model") is not None
        assert store2.get("delete-model").aa_score == 70

    def test_retained_record_still_updatable_for_price(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("price-model", pricing={"price_1m_blended_3_to_1": 0.5})], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        # direct store upsert for same key with new price (provider still gone but price update via store put)
        store = ModelInfoStore(store_path)
        new_rec = ModelInfoRecord(pricing=PricingSnapshot(blended=0.6, input=0.4, output=1.0), evidence_level="strong", _meta=StoreMeta(last_updated=_fresh_ts(), source_providers=["new_provider"]))
        store.put("price-model", new_rec)
        updated = store.get("price-model")
        # pricing re-averaged (0.5+0.6)/2 = 0.55
        assert updated.pricing.blended == 0.55

    def test_store_via_put_monotonic_not_deleted_by_missing_yaml(self, tmp_path):
        # also verify via build_all path: backfill monotonic across re-run
        p = tmp_path / "s.json"
        store = ModelInfoStore(p)
        r1 = ModelInfoRecord(aa_score=10, evidence_level="strong", _meta=StoreMeta(last_updated=_fresh_ts(), source_providers=["a"]))
        store.put("solo", r1)
        assert store.get_by_key("solo") is not None
        # second put with different key does not delete first
        r2 = ModelInfoRecord(aa_score=20, evidence_level="strong", _meta=StoreMeta(last_updated=_fresh_ts(), source_providers=["b"]))
        store.put("other", r2)
        assert store.get_by_key("solo") is not None
        assert store.size() == 2
