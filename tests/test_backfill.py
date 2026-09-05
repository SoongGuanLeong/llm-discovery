"""Backfill seeding from data/results/*.yaml (slim v2)."""
import json
from pathlib import Path

import yaml
from llm_discovery.backfill import backfill
from llm_discovery.model_info_store import ModelInfoStore


def _write_yaml(path: Path, provider: str, keep: list, evaluated_at="2026-09-04T05:00:00+00:00"):
    data = {"provider": provider, "evaluated_at": evaluated_at, "keep": keep, "drop_llm": [], "error": []}
    path.write_text(yaml.safe_dump(data))


def _keep(model_id, pricing=None, benchmarks=None):
    return {
        "model_id": model_id,
        "decision": "keep",
        "pricing": pricing or {"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9},
        "benchmarks": benchmarks or {"scores": {"aa_intelligence": {"score": 50, "source": "aa"}}, "raw_benchmarks": [], "benchmark_coverage": 0.25},
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
        # slim v2: no gate on legacy evidence_level; all keep entries merged
        # keep two distinct models, both stored
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "x.yaml", "x", [_keep("weak-model"), _keep("strong-model")])
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["unique_models"] == 2
        store = ModelInfoStore(store_path)
        assert store.get("strong-model") is not None
        assert store.get("weak-model") is not None

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
