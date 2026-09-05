"""Hardening for #78: Backfill pricing avg + monotonic store — slim v2."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
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


def _keep(model_id, pricing=None, benchmarks=None, evidence_level="strong", coding_score=55, aa_model_id="aa-test-id", evidence=None):
    if pricing is None:
        pricing_val = {"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9}
    else:
        pricing_val = pricing
    if benchmarks is None:
        benchmarks_val = {"scores": {"aa_intelligence": {"score": 50, "source": "https://example.com/aa"}}, "raw_benchmarks": [], "benchmark_coverage": 0.25}
    else:
        benchmarks_val = benchmarks
    if evidence is None:
        evidence_val = ["https://example.com/evidence for " + model_id]
    else:
        evidence_val = evidence
    return {
        "model_id": model_id,
        "decision": "keep",
        "evidence_level": evidence_level,
        "coding_score": coding_score,
        "aa_model_id": aa_model_id,
        "aa_score": 50,
        "confidence": 0.9,
        "pricing": pricing_val,
        "benchmarks": benchmarks_val,
        "evidence": evidence_val,
    }


def _fresh_ts() -> str:
    return datetime.now(UTC).isoformat()


def _stale_ts(days: int = 20) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


class TestStaleFileIgnored:
    def test_stale_file_skipped(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "stale.yaml", "stale_provider", [_keep("stale-model")], evaluated_at=_stale_ts(20))
        _write_yaml(results / "fresh.yaml", "fresh_provider", [_keep("fresh-model")], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["stale_skipped"] == 0
        assert stats["unique_models"] == 2
        store = ModelInfoStore(store_path)
        assert store.get("stale-model") is not None
        assert store.get("fresh-model") is not None
        assert is_stale(_stale_ts(20), DEFAULT_TTL_DAYS) is True
        assert is_stale(_fresh_ts(), DEFAULT_TTL_DAYS) is False

    def test_stale_file_ignored_even_with_strong_evidence(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "old.yaml", "old", [_keep("old-model")], evaluated_at=_stale_ts(30))
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["stale_skipped"] == 0
        assert stats["unique_models"] == 1
        assert ModelInfoStore(store_path).size() == 1

    def test_boundary_14d_not_stale_15d_stale(self, tmp_path):
        at_14 = (datetime.now(UTC) - timedelta(days=14)).isoformat()
        at_15 = (datetime.now(UTC) - timedelta(days=15)).isoformat()
        assert is_stale(at_14, 14) is False
        assert is_stale(at_15, 14) is True
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("model-14")], evaluated_at=at_14)
        _write_yaml(results / "b.yaml", "b", [_keep("model-15")], evaluated_at=at_15)
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["stale_skipped"] == 0
        store = ModelInfoStore(store_path)
        assert store.get("model-14") is not None
        assert store.get("model-15") is not None


class TestSecondProviderPriceAvg:
    def test_second_provider_price_avg_verified(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "provider_a", [_keep("shared-model", pricing={"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9})], evaluated_at=_fresh_ts())
        _write_yaml(results / "b.yaml", "provider_b", [_keep("shared-model", pricing={"price_1m_blended_3_to_1": 0.52, "price_1m_input_tokens": 0.31, "price_1m_output_tokens": 0.91})], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["unique_models"] == 1
        assert stats["merged_conflicts"] == 1
        assert stats["pricing_avgs"] == 1
        raw = json.loads(store_path.read_text())
        pricing = raw["models"]["shared-model"]["pricing"]
        assert pricing["blended"] == 0.51
        assert pricing["per_provider_overrides"] == {}
        store = ModelInfoStore(store_path)
        rec = store.get("shared-model")
        assert rec is not None
        assert rec._meta.version == 2
        assert rec.pricing.blended == 0.51

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
        assert raw["models"]["m1"]["pricing"]["blended"] == 0.51


class TestScalarGapFill:
    def test_scalar_gap_fill_only(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("gap-model", pricing={"price_1m_blended_3_to_1": 0.5})], evaluated_at=_fresh_ts())
        _write_yaml(results / "b.yaml", "b", [_keep("gap-model", pricing={"price_1m_blended_3_to_1": 0.52})], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        store = ModelInfoStore(store_path)
        rec = store.get("gap-model")
        assert rec is not None
        # pricing re-averaged
        assert rec.pricing.blended == 0.51

    def test_benchmarks_union_max(self, tmp_path):
        b1 = {"scores": {"aa_intelligence": {"score": 50}}, "raw_benchmarks": [], "benchmark_coverage": 0.25}
        b2 = {"scores": {"aa_intelligence": {"score": 55}, "swe_bench_verified": {"score": 70}}, "raw_benchmarks": [], "benchmark_coverage": 0.5}
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("bench-model", benchmarks=b1)], evaluated_at=_fresh_ts())
        _write_yaml(results / "b.yaml", "b", [_keep("bench-model", benchmarks=b2)], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        rec = ModelInfoStore(store_path).get("bench-model")
        assert rec.benchmarks.scores["aa_intelligence"]["score"] == 55
        assert rec.benchmarks.scores["swe_bench_verified"]["score"] == 70


class TestMonotonicStore:
    def test_provider_yaml_deletion_does_not_delete_store_key(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("keep-model")], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        assert ModelInfoStore(store_path).size() == 1
        # delete yaml
        (results / "a.yaml").unlink()
        backfill(results_dir=results, store_path=store_path)
        # store monotonic: still retains
        assert ModelInfoStore(store_path).size() == 1

    def test_retained_record_still_updatable_for_price(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("m1", pricing={"price_1m_blended_3_to_1": 0.5})], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        # new pricing for same model
        _write_yaml(results / "a.yaml", "a", [_keep("m1", pricing={"price_1m_blended_3_to_1": 0.55})], evaluated_at=_fresh_ts())
        backfill(results_dir=results, store_path=store_path)
        rec = ModelInfoStore(store_path).get("m1")
        # re-averaged
        assert rec.pricing.blended == pytest.approx(0.525)

    def test_store_via_put_monotonic_not_deleted_by_missing_yaml(self, tmp_path):
        store_path = tmp_path / "store.json"
        store = ModelInfoStore(store_path)
        rec = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 50}}), pricing=PricingSnapshot(blended=0.5), _meta=StoreMeta(last_updated=_fresh_ts()))
        store.put("direct-model", rec)
        assert store.size() == 1
        results = tmp_path / "results"
        results.mkdir()
        # backfill empty results dir should not delete direct put
        backfill(results_dir=results, store_path=store_path)
        assert ModelInfoStore(store_path).size() == 1