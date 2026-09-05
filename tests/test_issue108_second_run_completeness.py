"""Issue #108: 2nd-run completeness + telemetry — cold vs cached equality, TTL, gap-fill, churn, gate, no masked defaults."""
from __future__ import annotations
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from llm_discovery.backfill import backfill
from llm_discovery.benchmarks import BenchmarkDataCache, BenchmarkProfile, compute_coding_score
from llm_discovery.build_all import build_all
from llm_discovery.config import load_config
from llm_discovery.gate import is_accurate_enough
from llm_discovery.model_info_store import (
    BenchmarkSnapshot,
    ModelInfoRecord,
    ModelInfoStore,
    PricingSnapshot,
    StoreMeta,
    is_stale,
)
from llm_discovery.pipeline import (
    _gap_fill_benchmarks,
    _refresh_pricing_if_stale,
    build_cached_keep_record,
    classify_hit,
)
from llm_discovery.policy_gate import PolicyGate
from llm_discovery.results import ProviderBatchWriter


def _fresh_ts() -> str:
    return datetime.now(UTC).isoformat()

def _stale_ts(days: int = 20) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()

def _write_yaml(path: Path, provider: str, keep: list, evaluated_at: str | None = None):
    data = {"provider": provider, "evaluated_at": evaluated_at or _fresh_ts(), "keep": keep, "drop_llm": [], "error": []}
    path.write_text(yaml.safe_dump(data))

def _keep(
    model_id: str,
    pricing=None,
    benchmarks=None,
    evidence_level="strong",
    coding_score=55,
    aa_model_id="aa-test-id",
    aa_score=50,
    confidence=0.9,
    evidence=None,
    tier="max",
):
    if pricing is None:
        pricing = {"blended": 0.5, "input": 0.3, "output": 0.9, "per_provider_overrides": {}}
    if benchmarks is None:
        benchmarks = {"scores": {"aa_intelligence": {"score": 50, "source": "https://artificialanalysis.ai/models/test"}, "swe_bench_verified": {"score": 60, "source": "https://swebench.com/result"}}, "raw_benchmarks": [], "benchmark_coverage": 0.5, "coverage_with_supplements": 0.5}
    if evidence is None:
        evidence = ["https://artificialanalysis.ai/models/test score 60", "https://swebench.com/result swe_bench_verified 60"]
    return {
        "model_id": model_id,
        "provider_model_id": model_id,
        "decision": "keep",
        "tier": tier,
        "aa_model_id": aa_model_id,
        "aa_score": aa_score,
        "aa_name": "Test Model",
        "aa_slug": "test-model",
        "coding_score": coding_score,
        "confidence": confidence,
        "evidence_level": evidence_level,
        "evidence": evidence,
        "coding_assessment": {"is_coding": True, "confidence": confidence, "reason": "test", "coding_score": coding_score, "aa_score": aa_score},
        "pricing": pricing,
        "benchmarks": benchmarks,
        "benchmark_coverage": benchmarks.get("benchmark_coverage", 0.5),
    }

def _fake_resolution(model_id="test-model", aa_score=60, blended=0.5):
    return Mock(aa_model={
        "id": "test-model", "name": "Test Model", "slug": "test-model",
        "evaluations": {"artificial_analysis_intelligence_index": aa_score},
        "pricing": {"price_1m_blended_3_to_1": blended, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9}
    })

def _fake_llm_result(coding=True, decision="keep", confidence=0.9, evidence_level="strong"):
    m = Mock()
    m.coding = coding
    m.decision = decision
    m.confidence = confidence
    m.evidence_level = evidence_level
    m.evidence = ["AA 60 via https://artificialanalysis.ai/models/test-model", "swe_bench_verified 60 via https://swebench.com/result"]
    m.canonical_name = "Test Model"
    m.coding_assessment = Mock(model_dump=lambda: {"is_coding": True, "confidence": confidence, "reason": "llm judge", "coding_score": 55, "aa_score": 60})
    return m


class TestColdVsCachedEquality:
    """Red test: cached hit == cold shape for same benchmarks/pricing (issue #108 core)."""
    def test_cold_vs_cached_keep_equality_for_keeper(self):
        # Cold path via PolicyGate, cached path via build_cached_keep_record — same inputs must yield identical completeness.
        from llm_discovery.benchmarks import BenchmarkProfile
        model_id = "keeper-model"
        provider = "test-provider"
        # Build cache with scores that give deterministic coding_score
        cache = BenchmarkDataCache()
        # Inject synthetic benchmark data via _data dict
        cache._data = {
            "keeper-model": {
                "benchmarks": {
                    "aa_intelligence": {"score": 60, "source": "https://artificialanalysis.ai/models/test-model"},
                    "swe_bench_verified": {"score": 65, "source": "https://swebench.com/result"},
                },
                "raw_benchmarks": []
            }
        }
        cache._loaded = True
        resolution = _fake_resolution(model_id, aa_score=60, blended=0.5)

        # Cold: PolicyGate.apply with fake LLM result
        gate = PolicyGate(min_score=24, max_score=45, cache=cache)
        profile = BenchmarkProfile(model_id=model_id, provider=provider)
        profile.scores = {
            "aa_intelligence": {"score": 60, "source": "https://artificialanalysis.ai/models/test-model"},
            "swe_bench_verified": {"score": 65, "source": "https://swebench.com/result"},
        }
        llm_result = _fake_llm_result()
        cold = gate.apply(llm_result, resolution, model_id, provider, profile=profile)

        # Slim store record from cold (benchmarks+pricing only)
        slim = ModelInfoRecord.from_provider_record(cold, provider=provider, evaluated_at=_fresh_ts())

        # Cached hit derives live
        cached = build_cached_keep_record(model_id, provider, slim, resolution=resolution, cache=cache, min_score=24, max_score=45)

        # Required completeness fields identical (confidence deterministic may differ from LLM; check presence not exact)
        for field in ("tier", "aa_model_id", "aa_score", "coding_score", "evidence_level"):
            assert cached[field] == cold[field], f"{field} mismatch: cached={cached[field]} cold={cold[field]}"
        # Confidence both present and >0 (LLM 0.9 vs deterministic 0.17 both valid)
        assert cached["confidence"] is not None and cached["confidence"] > 0
        assert cold["confidence"] is not None and cold["confidence"] > 0
        # Pricing blended identical
        assert cached["pricing"].get("blended") == cold["pricing"].get("price_1m_blended_3_to_1") or cached["pricing"].get("blended") == 0.5
        # Benchmarks scores identical keys
        assert set(cached["benchmarks"]["scores"].keys()) == set(cold["benchmarks"]["scores"].keys())
        # Evidence contains URL in both
        assert any("http" in str(e) for e in cached["evidence"]), "cached evidence missing URL"
        assert any("http" in str(e) for e in cold["evidence"]), "cold evidence missing URL"
        # No null tier/evidence for Keeper
        assert cached["tier"] is not None, "cached tier null for Keeper"
        assert cached["evidence_level"] == "strong"
        assert cached["coding_score"] is not None

    def test_cached_keeper_has_complete_evidence_with_urls(self):
        cache = BenchmarkDataCache()
        cache._data = {
            "m1": {"benchmarks": {"aa_intelligence": {"score": 60, "source": "https://example.com/a"}}, "raw_benchmarks": []}
        }
        cache._loaded = True
        resolution = _fake_resolution("m1", aa_score=60)
        slim = ModelInfoRecord(
            benchmarks=BenchmarkSnapshot(scores={"aa_intelligence": {"score": 60, "source": "https://example.com/a"}}, raw_benchmarks=[], benchmark_coverage=0.25),
            pricing=PricingSnapshot(blended=0.5, input=0.3, output=0.9),
            _meta=StoreMeta(first_seen=_fresh_ts(), last_updated=_fresh_ts(), version=2),
        )
        rec = build_cached_keep_record("m1", "prov", slim, resolution=resolution, cache=cache)
        assert rec["tier"] is not None
        assert rec["aa_model_id"] is not None
        assert rec["coding_score"] is not None
        assert rec["pricing"]["blended"] is not None
        assert rec["benchmarks"]["scores"]
        assert any("http" in str(e) for e in rec["evidence"])
        assert rec["evidence_level"] == "strong"
        assert rec["confidence"] is not None and rec["confidence"] > 0


class TestPricingTTLReaverage:
    def test_pricing_reaverages_when_stale_14d(self):
        slim = ModelInfoRecord(
            benchmarks=BenchmarkSnapshot(scores={"aa_intelligence": {"score": 50}}, raw_benchmarks=[]),
            pricing=PricingSnapshot(blended=0.5),
            _meta=StoreMeta(first_seen=_stale_ts(20), last_updated=_stale_ts(20), version=2),
        )
        assert is_stale(_stale_ts(20), 14) is True
        fresh_obs = [{"blended": 0.8, "input": 0.5, "output": 1.1, "provider": "prov"}]
        refreshed = _refresh_pricing_if_stale(slim, fresh_obs)
        # stale -> re-averaged to fresh obs value
        blended = refreshed.blended if hasattr(refreshed, "blended") else refreshed.get("blended")
        assert blended == 0.8

    def test_pricing_not_reaveraged_when_fresh(self):
        slim = ModelInfoRecord(
            benchmarks=BenchmarkSnapshot(scores={"aa_intelligence": {"score": 50}}, raw_benchmarks=[]),
            pricing=PricingSnapshot(blended=0.5),
            _meta=StoreMeta(first_seen=_fresh_ts(), last_updated=_fresh_ts(), version=2),
        )
        fresh_obs = [{"blended": 0.8, "provider": "prov"}]
        out = _refresh_pricing_if_stale(slim, fresh_obs)
        blended = out.blended if hasattr(out, "blended") else out.get("blended")
        assert blended == 0.5  # not stale, kept

    def test_empty_pricing_rederives_even_when_fresh(self):
        # #104 fix: {per_provider_overrides:{}} with no blended counts as missing
        slim = ModelInfoRecord(
            benchmarks=BenchmarkSnapshot(scores={"aa_intelligence": {"score": 50}}, raw_benchmarks=[]),
            pricing=PricingSnapshot(blended=None, per_provider_overrides={}),
            _meta=StoreMeta(first_seen=_fresh_ts(), last_updated=_fresh_ts(), version=2),
        )
        fresh_obs = [{"blended": 0.15, "provider": "prov"}]
        out = _refresh_pricing_if_stale(slim, fresh_obs)
        blended = out.blended if hasattr(out, "blended") else out.get("blended")
        assert blended == 0.15


class TestBenchmarksImmutableGapFill:
    def test_gap_fill_null_to_fill_only(self):
        cached = {"scores": {"aa_intelligence": {"score": 50}}, "raw_benchmarks": []}
        fresh = {"scores": {"aa_intelligence": {"score": 99}, "swe_bench_verified": {"score": 70}}, "raw_benchmarks": [{"bench": "swe"}]}
        out = _gap_fill_benchmarks(cached, fresh)
        # immutable: aa_intelligence stays 50, not overwritten to 99
        assert out["scores"]["aa_intelligence"]["score"] == 50
        # gap-fill: new key added
        assert out["scores"]["swe_bench_verified"]["score"] == 70

    def test_gap_fill_no_delta_rebuild(self):
        cached = {"scores": {"aa_intelligence": {"score": 50}, "swe_bench_verified": {"score": 60}}, "raw_benchmarks": []}
        fresh = {"scores": {"aa_intelligence": {"score": 55}, "swe_bench_verified": {"score": 65}}, "raw_benchmarks": []}
        out = _gap_fill_benchmarks(cached, fresh)
        # No Evidence Delta: cached verbatim even if fresh differs
        assert out["scores"]["aa_intelligence"]["score"] == 50
        assert out["scores"]["swe_bench_verified"]["score"] == 60


class TestModelListChurn:
    def test_new_keys_always_built_even_when_store_populated(self, tmp_path):
        config_path = Path("config/providers.yaml")
        data_dir = tmp_path / "data_churn"
        data_dir.mkdir(parents=True)
        # First build with one model
        def discover_one(name, config=None, aa=None, models_dev=None, max_workers=4, store=None):
            return {"keep": [_keep("existing-model")], "drop": [], "error": []}
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:1]]
        res1 = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_one)
        assert res1["store_size"] == 1
        # Second build introduces new key + existing
        def discover_with_new(name, config=None, aa=None, models_dev=None, max_workers=4, store=None):
            return {"keep": [_keep("existing-model"), _keep("brand-new-model")], "drop": [], "error": []}
        res2 = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_with_new)
        assert res2["store_size"] == 2
        store = ModelInfoStore(data_dir / "model_info_store.json")
        assert store.get("existing-model") is not None
        assert store.get("brand-new-model") is not None


class TestGateBlocksIncomplete:
    def test_agnes_pro_flash_style_incomplete_blocked(self):
        # agnes-2.5-pro: pricing empty {per_provider_overrides:{}}, benchmarks empty
        rec_pro = _keep("agnes-2.5-pro", pricing={"per_provider_overrides": {}}, benchmarks={"scores": {}, "raw_benchmarks": [], "benchmark_coverage": 0.0}, coding_score=None, evidence_level="weak", aa_model_id=None)
        ok, reason = is_accurate_enough(rec_pro)
        assert ok is False
        # agnes-2.5-flash: benchmarks empty, evidence no URL
        rec_flash = _keep("agnes-2.5-flash", pricing={"blended": 0.5}, benchmarks={"scores": {}, "raw_benchmarks": [], "benchmark_coverage": 0.0}, coding_score=None, evidence=["no url evidence"], evidence_level="weak")
        ok2, _ = is_accurate_enough(rec_flash)
        assert ok2 is False

    def test_backfill_gate_blocks_incomplete_not_written(self, tmp_path):
        results = tmp_path / "results"
        results.mkdir()
        incomplete = _keep("agnes-2.5-pro", pricing={"per_provider_overrides": {}}, benchmarks={"scores": {}, "raw_benchmarks": []}, coding_score=None, evidence_level="weak", aa_model_id=None, evidence=["no url"])
        complete = _keep("keeper-complete")
        _write_yaml(results / "a.yaml", "agnes", [incomplete, complete])
        store_path = tmp_path / "store.json"
        stats = backfill(results_dir=results, store_path=store_path)
        assert stats["gate_skipped"] >= 1
        store = ModelInfoStore(store_path)
        assert store.get("agnes-2.5-pro") is None
        assert store.get("keeper-complete") is not None


class TestNoMaskedDefaults:
    def test_provider_batch_writer_no_masked_defaults(self, tmp_path):
        writer = ProviderBatchWriter()
        incomplete = {"provider_model_id": "incomplete-model", "decision": "keep"}  # missing tier, aa, coding, confidence, evidence_level
        out = writer._to_record(incomplete)
        assert out["tier"] is None
        assert out["aa_model_id"] is None
        assert out["aa_score"] is None
        assert out["coding_score"] is None
        assert out["confidence"] is None
        assert out["evidence_level"] is None
        # pricing missing -> None, not fake dict
        assert out["pricing"] is None
        # evidence empty clean -> []
        assert out["evidence"] == []

    def test_incomplete_yaml_shows_null_not_fake_09(self, tmp_path):
        writer = ProviderBatchWriter()
        rec = {"provider_model_id": "m", "decision": "keep", "evidence_level": None, "confidence": None}
        projected = writer._to_record(rec)
        assert projected["confidence"] is None  # not 0.9 masked
        assert projected["evidence_level"] is None  # not strong masked


class TestBuildAllSecondRunCompleteness:
    def test_second_run_reuses_keepers_produces_complete_yaml(self, tmp_path):
        config_path = Path("config/providers.yaml")
        data_dir = tmp_path / "data2"
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:1]]
        keeper = _keep("keeper-model", tier="max", coding_score=55, aa_model_id="aa-test-id", evidence_level="strong")
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4, store=None):
            return {"keep": [keeper], "drop": [], "error": []}
        # First run cold
        res1 = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        assert res1["store_size"] == 1
        yaml1 = yaml.safe_load((data_dir / "results" / f"{names[0]}.yaml").read_text())
        assert yaml1["keep"][0]["tier"] == "max"
        assert yaml1["keep"][0]["aa_model_id"] is not None
        # Second run with same keeper — should reuse, not re-evaluate, YAML identical completeness
        res2 = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        assert res2["store_size"] == 1
        yaml2 = yaml.safe_load((data_dir / "results" / f"{names[0]}.yaml").read_text())
        k2 = yaml2["keep"][0]
        # Completeness: tier/aa/coding/evidence with URLs not null/[]
        assert k2["tier"] is not None and k2["tier"] != "null"
        assert k2["aa_model_id"] is not None
        assert k2["coding_score"] is not None
        assert k2["evidence_level"] == "strong"
        assert k2["evidence"] and any("http" in str(e) for e in k2["evidence"])
        assert k2["pricing"] is not None
        assert k2["benchmarks"] and k2["benchmarks"].get("scores")
        # Equality of completeness keys between runs
        for field in ("tier", "aa_model_id", "coding_score", "evidence_level"):
            assert k2[field] == yaml1["keep"][0][field]

    def test_gc_after_14d_removes_stale_not_live_key(self, tmp_path):
        config_path = Path("config/providers.yaml")
        data_dir = tmp_path / "data_gc"
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:1]]
        # Seed store with stale key directly
        store_path = data_dir / "model_info_store.json"
        data_dir.mkdir(parents=True)
        (data_dir / "results").mkdir(parents=True, exist_ok=True)
        # Create providers.yaml-compatible empty results then manually put stale record
        store = ModelInfoStore(store_path)
        stale_rec = ModelInfoRecord(
            benchmarks=BenchmarkSnapshot(scores={"aa_intelligence": {"score": 50}}, raw_benchmarks=[]),
            pricing=PricingSnapshot(blended=0.5),
            _meta=StoreMeta(first_seen=_stale_ts(20), last_updated=_stale_ts(20), version=2),
        )
        store.put("stale-gc-model", stale_rec)
        assert store.size() == 1
        # Build with only new live key, stale not in live set -> GC should delete it
        def discover_new(name, config=None, aa=None, models_dev=None, max_workers=4, store=None):
            return {"keep": [_keep("fresh-live-model")], "drop": [], "error": []}
        res = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_new)
        # GC after 14d: stale-gc-model absent from live_keys and stale -> removed
        final = ModelInfoStore(store_path)
        assert final.get("stale-gc-model") is None, "GC should remove stale not-live key after 14d"
        assert final.get("fresh-live-model") is not None
        assert res["gc"] >= 1

    def test_telemetry_after_second_run(self, tmp_path):
        config_path = Path("config/providers.yaml")
        data_dir = tmp_path / "data_tel"
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:1]]
        def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4, store=None):
            return {"keep": [_keep("tel-model")], "drop": [], "error": []}
        res1 = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        res2 = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        assert "telemetry" in res2
        tel = res2["telemetry"]
        assert tel["store_size"] == 1
        assert tel["unique_discovered"] == 1
        # second run reused unique
        assert tel["reused"] >= 1


class TestClassifyHitStrongOnly:
    def test_strong_hit_moderate_miss(self):
        assert classify_hit({"evidence_level": "strong"}) == "strong_hit"
        assert classify_hit({"evidence_level": "moderate"}) == "miss"
        assert classify_hit({"evidence_level": "weak"}) == "miss"
        assert classify_hit(None) == "miss"
        # slim Keeper without level implies strong_hit (existence == strong)
        rec = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 50}}), pricing=PricingSnapshot(blended=0.5), _meta=StoreMeta(first_seen=_fresh_ts(), last_updated=_fresh_ts(), version=2))
        assert classify_hit(rec) == "strong_hit"

class TestPipelineCacheHitAvoidsLLM:
    def test_evaluate_model_hit_does_not_call_llm(self, tmp_path):
        from llm_discovery.benchmarks import BenchmarkDataCache
        from llm_discovery.model_info_store import ModelInfoStore
        from llm_discovery.pipeline import evaluate_model
        cache = BenchmarkDataCache()
        cache._data = {
            "keeper-llm": {"benchmarks": {"aa_intelligence": {"score": 60, "source": "https://example.com/a"}, "swe_bench_verified": {"score": 65, "source": "https://swebench.com/b"}}, "raw_benchmarks": []}
        }
        cache._loaded = True
        store_path = tmp_path / "store.json"
        store = ModelInfoStore(store_path)
        rec = ModelInfoRecord(
            benchmarks=BenchmarkSnapshot(scores={"aa_intelligence": {"score": 60, "source": "https://example.com/a"}, "swe_bench_verified": {"score": 65, "source": "https://swebench.com/b"}}, raw_benchmarks=[], benchmark_coverage=0.5),
            pricing=PricingSnapshot(blended=0.5, input=0.3, output=0.9),
            _meta=StoreMeta(first_seen=_fresh_ts(), last_updated=_fresh_ts(), version=2),
        )
        store.put("keeper-llm", rec)
        class FakeAA:
            models = [{"id": "keeper-llm", "name": "Keeper LLM", "slug": "keeper-llm", "evaluations": {"artificial_analysis_intelligence_index": 60}, "pricing": {"price_1m_blended_3_to_1": 0.5}}]
        class FakeMD:
            models = {}
            providers = {}
        class ExplodingEvaluator:
            def evaluate(self, *a, **kw):
                raise AssertionError("LLM should not be called on cache hit")
        from unittest.mock import patch
        fake_res = Mock(aa_model={"id": "keeper-llm", "name": "Keeper LLM", "slug": "keeper-llm", "evaluations": {"artificial_analysis_intelligence_index": 60}, "pricing": {"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9}})
        with patch("llm_discovery.pipeline.resolve_model", return_value=fake_res):
            result = evaluate_model(model={"id": "keeper-llm"}, provider_name="test-provider", aa=FakeAA(), models_dev=FakeMD(), evaluator=ExplodingEvaluator(), min_score=24, max_score=45, cache=cache, store=store)
        assert result["cached"] is True
        assert result["tier"] is not None
        assert result["aa_model_id"] == "keeper-llm"
        assert result["evidence_level"] == "strong"
        assert any("http" in str(e) for e in result["evidence"])
        assert result["decision"] == "keep"
