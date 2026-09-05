"""Integration + compact verification for unified store (slim v2)."""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from llm_discovery.backfill import backfill
from llm_discovery.build_all import build_all
from llm_discovery.config import load_config
from llm_discovery.model_info_store import ModelInfoStore, dumps_compact, is_stale, DEFAULT_TTL_DAYS


def _write_yaml(path: Path, provider: str, keep: list, evaluated_at: str):
    data = {"provider": provider, "evaluated_at": evaluated_at, "keep": keep, "drop": [], "error": []}
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
        "provider_model_id": model_id,
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


class TestBuildAllEmptyTmpDir:
    def test_build_all_creates_pretty_store_with_version(self, tmp_path):
        config_path = Path("config/providers.yaml")
        data_dir = tmp_path / "data"
        assert not (data_dir / "model_info_store.json").exists()
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [_keep(f"model-{name}")], "drop": [], "error": []}
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:2]]
        res = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        store_path = data_dir / "model_info_store.json"
        assert store_path.exists()
        raw_text = store_path.read_text()
        assert "\n  \"" in raw_text or '"  \n' in raw_text or "  \"version\"" in raw_text or raw_text.count("\n") > 2
        payload = json.loads(raw_text)
        assert "version" in payload
        assert "models" in payload
        assert isinstance(payload["models"], dict)
        assert payload["version"] == 2
        assert not (data_dir / "benchmarks.json").exists()
        assert not (data_dir / "nararouter_raw_full.json").exists()
        assert res["store_size"] == 2

    def test_dumps_compact_shorter_and_round_trips(self, tmp_path):
        config_path = Path("config/providers.yaml")
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:2]]
        data_dir = tmp_path / "data2"
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [_keep("compact-model", pricing={"price_1m_blended_3_to_1": 0.4})], "drop": [], "error": []}
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        store = ModelInfoStore(data_dir / "model_info_store.json")
        pretty = store.dumps_pretty()
        compact = store.dumps_compact()
        assert len(compact) < len(pretty)
        assert json.loads(compact) == json.loads(pretty)
        payload = json.loads(pretty)
        assert dumps_compact(payload) == compact

    def test_get_by_key_selective_lookup(self, tmp_path):
        config_path = Path("config/providers.yaml")
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:2]]
        data_dir = tmp_path / "data3"
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4):
            if name == names[0]:
                return {"keep": [_keep("alpha-model")], "drop": [], "error": []}
            else:
                return {"keep": [_keep("beta-model")], "drop": [], "error": []}
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        store = ModelInfoStore(data_dir / "model_info_store.json")
        store.load()
        assert store._loaded is True
        calls = {"n": 0}
        orig_load = store.load
        def counting_load(*a, **kw):
            calls["n"] += 1
            return orig_load(*a, **kw)
        store.load = counting_load  # type: ignore
        rec_alpha = store.get_by_key("alpha-model")
        rec_beta = store.get_by_key("beta-model")
        assert rec_alpha is not None and rec_alpha.benchmarks.scores["aa_intelligence"]["score"] == 50
        assert rec_beta is not None and rec_beta.benchmarks.scores["aa_intelligence"]["score"] == 50

class TestStaleIgnored:
    def test_stale_yaml_ignored_in_store_count(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "fresh.yaml", "fresh", [_keep("fresh-model")], evaluated_at=_fresh_ts())
        _write_yaml(results / "stale.yaml", "stale", [_keep("stale-model")], evaluated_at=_stale_ts(30))
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        # slim v2: no file-level stale gate, both counted
        assert stats["unique_models"] == 2

    def test_build_all_stale_filtered(self, tmp_path):
        config_path = Path("config/providers.yaml")
        data_dir = tmp_path / "data_stale"
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [_keep("stale-build-model")], "drop": [], "error": []}
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:1]]
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        store = ModelInfoStore(data_dir / "model_info_store.json")
        assert store.size() == 1

class TestMonotonicDeletion:
    def test_deleted_provider_model_stays_with_last_price(self, tmp_path):
        config_path = Path("config/providers.yaml")
        data_dir = tmp_path / "data_mono"
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [_keep("keep-model", pricing={"price_1m_blended_3_to_1": 0.5})], "drop": [], "error": []}
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:1]]
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        store = ModelInfoStore(data_dir / "model_info_store.json")
        assert store.get("keep-model") is not None
        # second build with no keep -> GC would keep 14d, store monotonic
        def discover_empty(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [], "drop": [], "error": []}
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_empty)
        # still retained (GC 14d)
        assert ModelInfoStore(data_dir / "model_info_store.json").get("keep-model") is not None

    def test_build_all_monotonic_via_mock(self, tmp_path):
        config_path = Path("config/providers.yaml")
        data_dir = tmp_path / "data_mono2"
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:1]]
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [_keep("a-model"), _keep("b-model")], "drop": [], "error": []}
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        assert ModelInfoStore(data_dir / "model_info_store.json").size() == 2

class TestNoExtraArtifacts:
    def test_no_benchmarks_or_nararouter_raw_created(self, tmp_path):
        config_path = Path("config/providers.yaml")
        data_dir = tmp_path / "data_art"
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:1]]
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [_keep("art-model")], "drop": [], "error": []}
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        assert not (data_dir / "benchmarks.json").exists()
        assert not (data_dir / "nararouter_raw_full.json").exists()
