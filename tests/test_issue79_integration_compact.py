"""Integration + compact verification for unified store (fixes #79).

Covers all 6 acceptance criteria:

- build-all on empty tmp dir creates data/model_info_store.json pretty with {version, models}
- dumps_compact() is shorter than pretty and round-trips to identical payload
- get_by_key selective lookup works without loading whole-file string
- Stale provider YAML (>14d) is ignored in final store count
- Deleted provider model vanishes from its YAML but stays in central store with last price
- No benchmarks.json or nararouter_raw_full.json is created; only store is committed
"""
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


def _keep(model_id, evidence_level="strong", confidence=0.9, aa_score=50, pricing=None, benchmarks=None, tier="flash", evidence=None):
    return {
        "model_id": model_id,
        "provider_model_id": model_id,
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


def _mock_discover_two_providers(name, config=None, aa=None, models_dev=None, max_workers=4):
    # Return keep records for 2 mocked providers, no network/LLM
    if name == "groq":
        return {"keep": [_keep("shared-model", aa_score=55, pricing={"price_1m_blended_3_to_1": 0.5})], "drop": [], "error": []}
    if name == "openrouter":
        return {"keep": [_keep("shared-model", aa_score=60, pricing={"price_1m_blended_3_to_1": 0.52})], "drop": [], "error": []}
    return {"keep": [], "drop": [], "error": []}


class TestBuildAllEmptyTmpDir:
    def test_build_all_creates_pretty_store_with_version(self, tmp_path):
        # tmp data_dir empty, config uses real providers.yaml but filtered to 2 via discover_fn mock
        config_path = Path("config/providers.yaml")
        # use subset providers that exist in config
        data_dir = tmp_path / "data"
        # build_all needs providers.yaml to have at least groq/openrouter; fallback to first 2 if not
        # Ensure data_dir is empty
        assert not (data_dir / "model_info_store.json").exists()
        # mock discover_fn that returns keep for any requested provider name
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [_keep(f"model-{name}", aa_score=50)], "drop": [], "error": []}

        # load real provider names to avoid ValueError
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:2]]
        res = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        store_path = data_dir / "model_info_store.json"
        assert store_path.exists(), "store not created on empty tmp dir"
        raw_text = store_path.read_text()
        # pretty: indent 2
        assert "\n  \"" in raw_text or '"  \n' in raw_text or "  \"version\"" in raw_text or raw_text.count("\n") > 2
        payload = json.loads(raw_text)
        assert "version" in payload, "missing version header"
        assert "models" in payload, "missing models dict"
        assert isinstance(payload["models"], dict)
        assert payload["version"] == 1
        # only store committed, no benchmarks
        assert not (data_dir / "benchmarks.json").exists()
        assert not (data_dir / "nararouter_raw_full.json").exists()
        assert res["store_size"] == 2

    def test_dumps_compact_shorter_and_round_trips(self, tmp_path):
        config_path = Path("config/providers.yaml")
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:2]]
        data_dir = tmp_path / "data2"
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [_keep("compact-model", aa_score=77, pricing={"price_1m_blended_3_to_1": 0.4})], "drop": [], "error": []}
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        store = ModelInfoStore(data_dir / "model_info_store.json")
        pretty = store.dumps_pretty()
        compact = store.dumps_compact()
        # compact shorter
        assert len(compact) < len(pretty), f"compact {len(compact)} not shorter than pretty {len(pretty)}"
        # round-trip identical payload
        assert json.loads(compact) == json.loads(pretty)
        # standalone dumps_compact helper also valid
        payload = json.loads(pretty)
        assert dumps_compact(payload) == compact

    def test_get_by_key_selective_lookup(self, tmp_path):
        config_path = Path("config/providers.yaml")
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:2]]
        data_dir = tmp_path / "data3"
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4):
            # two distinct models per provider, share one key
            if name == names[0]:
                return {"keep": [_keep("alpha-model", aa_score=50)], "drop": [], "error": []}
            else:
                return {"keep": [_keep("beta-model", aa_score=60)], "drop": [], "error": []}
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        store = ModelInfoStore(data_dir / "model_info_store.json")
        # selective lookup without loading whole-file string again: ensure no re-load
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
        assert rec_alpha is not None and rec_alpha.aa_score == 50
        assert rec_beta is not None and rec_beta.aa_score == 60
        # get_by_key should not trigger file load after already loaded
        assert calls["n"] == 0, "get_by_key re-read whole file"
        # also get() normalizes key
        assert store.get("ALPHA-MODEL") is not None
        assert calls["n"] == 0
        # missing key returns None without error
        assert store.get_by_key("nonexistent-key-xyz") is None
        assert calls["n"] == 0
        store.load = orig_load  # restore


class TestStaleIgnored:
    def test_stale_yaml_ignored_in_store_count(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        _write_yaml(results / "fresh.yaml", "fresh", [_keep("fresh-model", aa_score=60)], evaluated_at=_fresh_ts())
        _write_yaml(results / "stale.yaml", "stale", [_keep("stale-model", aa_score=99)], evaluated_at=_stale_ts(20))
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["stale_skipped"] == 1
        store = ModelInfoStore(store_path)
        assert store.get("fresh-model") is not None
        assert store.get("stale-model") is None
        assert store.size() == 1

    def test_build_all_stale_filtered(self, tmp_path):
        # Simulate build_all where one provider writes stale YAML: verify stale ignored after backfill
        # Directly use backfill inside build_all flow: create stale + fresh YAML then backfill
        data_dir = tmp_path / "data"
        results = data_dir / "results"
        results.mkdir(parents=True)
        _write_yaml(results / "a.yaml", "a", [_keep("keep-me", aa_score=50)], evaluated_at=_fresh_ts())
        _write_yaml(results / "b.yaml", "b", [_keep("drop-me", aa_score=50)], evaluated_at=_stale_ts(30))
        store_path = data_dir / "model_info_store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert ModelInfoStore(store_path).size() == 1
        assert ModelInfoStore(store_path).get("drop-me") is None


class TestMonotonicDeletion:
    def test_deleted_provider_model_stays_with_last_price(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        # initial: two models
        _write_yaml(results / "a.yaml", "a", [_keep("keep-model", aa_score=60, pricing={"price_1m_blended_3_to_1": 0.5}), _keep("vanish-model", aa_score=70, pricing={"price_1m_blended_3_to_1": 0.8})], evaluated_at=_fresh_ts())
        store_path = tmp_path / "store.json"
        backfill(results_dir=results, store_path=store_path)
        assert ModelInfoStore(store_path).get("vanish-model") is not None
        assert ModelInfoStore(store_path).get("vanish-model").pricing.blended == 0.8
        # rewrite a.yaml without vanish-model (simulates provider deletion)
        _write_yaml(results / "a.yaml", "a", [_keep("keep-model", aa_score=60, pricing={"price_1m_blended_3_to_1": 0.5})], evaluated_at=_fresh_ts())
        # second backfill: monotonic store retains vanish-model
        backfill(results_dir=results, store_path=store_path)
        store2 = ModelInfoStore(store_path)
        assert store2.get("vanish-model") is not None, "deleted provider model should stay in central store"
        assert store2.get("vanish-model").pricing.blended == 0.8, "last price retained"
        # vanished from its YAML: verify YAML no longer contains it
        yaml_data = yaml.safe_load((results / "a.yaml").read_text())
        keep_ids = [r["model_id"] for r in yaml_data.get("keep", [])]
        assert "vanish-model" not in keep_ids

    def test_build_all_monotonic_via_mock(self, tmp_path):
        # Use build_all then simulate deletion via second build_all with fewer models
        config_path = Path("config/providers.yaml")
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:2]]
        data_dir = tmp_path / "data"
        # first build: both providers produce model X
        def disc1(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [_keep("lonely-model", pricing={"price_1m_blended_3_to_1": 0.9})], "drop": [], "error": []}
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=disc1)
        assert ModelInfoStore(data_dir / "model_info_store.json").get("lonely-model") is not None
        # second build: only one provider reports model, other empty -> store should retain
        def disc2(name, config=None, aa=None, models_dev=None, max_workers=4):
            if name == names[0]:
                return {"keep": [_keep("lonely-model", pricing={"price_1m_blended_3_to_1": 0.9})], "drop": [], "error": []}
            return {"keep": [], "drop": [], "error": []}
        # Note: build_all overwrites YAMLs via ProviderBatchWriter.write, so second run drops lonely-model from one YAML but not store
        # backfill monotonic keeps it
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=disc2)
        assert ModelInfoStore(data_dir / "model_info_store.json").get("lonely-model") is not None


class TestNoExtraArtifacts:
    def test_no_benchmarks_or_nararouter_raw_created(self, tmp_path):
        config_path = Path("config/providers.yaml")
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:2]]
        data_dir = tmp_path / "data"
        def disc(name, config=None, aa=None, models_dev=None, max_workers=4):
            return {"keep": [_keep("m", aa_score=50)], "drop": [], "error": []}
        build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=disc)
        assert not (data_dir / "benchmarks.json").exists(), "benchmarks.json should not be created"
        assert not (data_dir / "nararouter_raw_full.json").exists()
        assert not (data_dir / "nararouter_raw.json").exists()
        assert (data_dir / "model_info_store.json").exists()
        # also test refresh cache-miss does not create benchmarks.json
        from llm_discovery.refresh import refresh_benchmarks
        out = tmp_path / "data" / "benchmarks.json"
        # ensure caches missing
        for p in [tmp_path / "data" / "artificial_analysis_models.json", tmp_path / "data" / "models_dev_catalog.json"]:
            if p.exists():
                p.unlink()
        # should not create when caches missing
        res = refresh_benchmarks(aa_path=tmp_path / "data" / "artificial_analysis_models.json", models_dev_path=tmp_path / "data" / "models_dev_catalog.json", output=out)
        assert res is None
        assert not out.exists()
