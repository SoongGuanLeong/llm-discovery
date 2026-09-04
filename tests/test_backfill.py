"""Backfill seeding from data/results/*.yaml (issue #69)."""
import json
from pathlib import Path

import yaml
from llm_discovery.backfill import backfill
from llm_discovery.model_info_store import ModelInfoStore


def _write_yaml(path: Path, provider: str, keep: list, evaluated_at="2026-09-04T05:00:00+00:00"):
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


class TestBackfillSeam:
    def test_backfill_creates_store_with_dedup_and_stats(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        # two providers, same logical model but different provider prefix -> should dedup
        _write_yaml(results / "a.yaml", "a", [_keep("openai/gpt-4o:free", evidence_level="strong", aa_score=55, pricing={"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9})])
        _write_yaml(results / "b.yaml", "b", [_keep("groq/gpt-4o", evidence_level="moderate", aa_score=50, pricing={"price_1m_blended_3_to_1": 0.52, "price_1m_input_tokens": 0.31, "price_1m_output_tokens": 0.91})])
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        # assertions on stats shape (spec sources #69)
        assert stats["unique_models"] == 1
        assert stats["merged_conflicts"] == 1
        assert stats["files_processed"] == 2
        assert store_path.exists()
        raw = json.loads(store_path.read_text())
        assert raw["version"] == 1
        assert "gpt-4o" in raw["models"]
        # pricing avg: 0.51
        assert raw["models"]["gpt-4o"]["pricing"]["blended"] == 0.51

    def test_weak_skipped(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "x.yaml", "x", [_keep("weak-model", evidence_level="weak", aa_score=30), _keep("strong-model", evidence_level="strong", aa_score=60)])
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["weak_skipped"] == 1
        assert stats["unique_models"] == 1
        store = ModelInfoStore(store_path)
        assert store.get("strong-model") is not None
        assert store.get("weak-model") is None

    def test_idempotent_merge_not_overwrite(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("openai/gpt-4o", evidence_level="moderate", aa_score=50)])
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        # second run with stronger evidence: #72 Q10 b gap-fill keeps existing scalar (50), not overwrite
        _write_yaml(results / "b.yaml", "b", [_keep("groq/gpt-4o", evidence_level="strong", aa_score=60)])
        stats2 = backfill(results_dir=results, store_path=store_path)
        assert stats2["unique_models"] == 1
        store = ModelInfoStore(store_path)
        assert store.get("gpt-4o").aa_score == 50  # gap-fill only, keep existing

    def test_outliers_separated(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "a.yaml", "a", [_keep("m1", aa_score=50, pricing={"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9})])
        _write_yaml(results / "b.yaml", "b", [_keep("m1", aa_score=51, pricing={"price_1m_blended_3_to_1": 0.52, "price_1m_input_tokens": 0.31, "price_1m_output_tokens": 0.91})])
        _write_yaml(results / "c.yaml", "c", [_keep("m1", aa_score=52, pricing={"price_1m_blended_3_to_1": 1.5, "price_1m_input_tokens": 1.0, "price_1m_output_tokens": 2.0})])
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["pricing_outliers"] >= 1
        raw = json.loads(store_path.read_text())
        assert "per_provider_overrides" in raw["models"]["m1"]["pricing"]

    def test_real_results_dir_smoke(self):
        # smoke against actual repo data/results — at least enumerates without crash, tolerant of empty keep
        from pathlib import Path as P
        store_path = P("/tmp/test-backfill-smoke.json")
        if store_path.exists():
            store_path.unlink()
        stats = backfill(results_dir=P("data/results"), store_path=store_path)
        assert "files_processed" in stats
        assert "unique_models" in stats
        # current repo has ~17 files, at most 1 keep (bazaarlink auto:free) => unique >=0
        assert stats["files_processed"] == 17
        if store_path.exists():
            store_path.unlink()
