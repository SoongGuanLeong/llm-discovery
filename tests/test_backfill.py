"""Backfill seeding from data/results/*.yaml (slim v2)."""
import json
from pathlib import Path

import yaml
from llm_discovery.backfill import backfill
from llm_discovery.model_info_store import ModelInfoStore


def _write_yaml(path: Path, provider: str, keep: list, evaluated_at="2026-09-04T05:00:00+00:00"):
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


class TestBackfillSeam:
    def test_backfill_creates_store_with_dedup_and_stats(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("openai/gpt-4o:free", pricing={"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9})])
        _write_yaml(results / "b.yaml", "b", [_keep("groq/gpt-4o", pricing={"price_1m_blended_3_to_1": 0.52, "price_1m_input_tokens": 0.31, "price_1m_output_tokens": 0.91})])
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["unique_models"] == 1
        assert stats["merged_conflicts"] == 1
        assert stats["files_processed"] == 2
        assert store_path.exists()
        raw = json.loads(store_path.read_text())
        assert raw["version"] == 2
        assert "gpt-4o" in raw["models"]
        assert raw["models"]["gpt-4o"]["pricing"]["blended"] == 0.51
        assert set(raw["models"]["gpt-4o"].keys()) == {"benchmarks", "pricing", "_meta"}
        assert raw["models"]["gpt-4o"]["_meta"]["version"] == 2

    def test_weak_skipped(self, tmp_path):
        # Gate: moderates/weaks and incomplete never enter store
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "x.yaml", "x", [_keep("weak-model", evidence_level="weak", coding_score=55), _keep("strong-model", evidence_level="strong", coding_score=55)])
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["unique_models"] == 1
        assert stats["gate_skipped"] == 1
        store = ModelInfoStore(store_path)
        assert store.get("strong-model") is not None
        assert store.get("weak-model") is None

    def test_gate_blocks_incomplete_pricing_and_benchmarks(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        # missing pricing -> gate fail (not free)
        _write_yaml(results / "a.yaml", "a", [_keep("no-pricing-model", pricing={}, benchmarks={"scores": {"aa_intelligence": {"score": 50, "source": "https://example.com/aa"}}, "raw_benchmarks": [], "benchmark_coverage": 0.25})])
        # empty benchmarks / coding_score null -> gate fail
        _write_yaml(results / "b.yaml", "b", [_keep("empty-bench-model", coding_score=None)])
        # UUID -> gate fail
        _write_yaml(results / "c.yaml", "c", [_keep("123e4567-e89b-12d3-a456-426614174000", evidence=["https://example.com/evidence"])])
        # hallucinated evidence -> gate fail (has http but denylist)
        _write_yaml(results / "d.yaml", "d", [_keep("hallu-model", evidence=["https://tokenmix.ai/bench 50 via tokenmix.ai"])])
        # valid keeper passes
        _write_yaml(results / "e.yaml", "e", [_keep("valid-model")])
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["unique_models"] == 1
        assert stats["gate_skipped"] == 4
        store = ModelInfoStore(store_path)
        assert store.get("valid-model") is not None
        assert store.get("no-pricing-model") is None
        assert store.get("empty-bench-model") is None

    def test_idempotent_merge_not_overwrite(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("openai/gpt-4o")])
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        _write_yaml(results / "b.yaml", "b", [_keep("groq/gpt-4o")])
        stats2 = backfill(results_dir=results, store_path=store_path)
        assert stats2["unique_models"] == 1
        store = ModelInfoStore(store_path)
        rec = store.get("gpt-4o")
        assert rec is not None
        assert rec._meta.version == 2
        assert "aa_intelligence" in rec.benchmarks.scores

    def test_outliers_separated(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("m1", pricing={"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9})])
        _write_yaml(results / "b.yaml", "b", [_keep("m1", pricing={"price_1m_blended_3_to_1": 0.52, "price_1m_input_tokens": 0.31, "price_1m_output_tokens": 0.91})])
        _write_yaml(results / "c.yaml", "c", [_keep("m1", pricing={"price_1m_blended_3_to_1": 1.5, "price_1m_input_tokens": 1.0, "price_1m_output_tokens": 2.0})])
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["pricing_outliers"] >= 1
        raw = json.loads(store_path.read_text())
        assert "per_provider_overrides" in raw["models"]["m1"]["pricing"]

    def test_real_results_dir_smoke(self):
        from pathlib import Path as P
        store_path = P("/tmp/test-backfill-smoke.json")
        if store_path.exists():
            store_path.unlink()
        stats = backfill(results_dir=P("data/results"), store_path=store_path)
        assert "files_processed" in stats
        assert "unique_models" in stats
        if store_path.exists():
            store_path.unlink()
