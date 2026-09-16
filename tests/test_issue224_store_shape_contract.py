"""Store shape split — CONTRACT step (issue #224).

Verifies migrate + contract: Tier derived on read via categorize from fresh evidence,
old persisted Tier fields removed, GC share-aware 14d, Derived Cache rebuild passes,
pricing re-average updates tier without LLM (evidence_hash unchanged).

Offline, deterministic (ISO timestamps, no network), same conventions as 223/221.
"""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from llm_discovery.model_info_store import (
    STORE_FILE_VERSION,
    DEFAULT_TTL_DAYS,
    BenchmarkSnapshot,
    FactsSnapshot,
    JudgeSnapshot,
    JudgementSnapshot,
    ModelInfoRecord,
    ModelInfoStore,
    PricingSnapshot,
    StoreMeta,
    aggregate_pricing,
    compute_evidence_hash,
    derive_tier,
)
from llm_discovery.evaluator import EvaluatorCoordinator

NOW_A = "2026-09-04T01:00:00+00:00"
NOW_B = "2026-09-04T02:00:00+00:00"
NOW_C = "2026-09-04T03:00:00+00:00"

EVIDENCE_URLS = ["https://example.com/a", "https://example.com/b"]
BENCH_SCORES = {"swe_bench_verified": {"score": 55, "source": "https://example.com"}}

def _iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()

def _strong_rec(**over):
    base = {
        "model_id": "openai/gpt-4o",
        "provider_model_id": "openai/gpt-4o",
        "evidence_level": "strong",
        "evidence": list(EVIDENCE_URLS),
        "confidence": 0.9,
        "coding": True,
        "decision": "keep",
        "judge_model": "judge-1",
        "aa_score": 60.0,
        "aa_model_id": "aa-test-id",
        "coding_score": 55.0,
        "benchmark_coverage": 0.5,
        "pricing": {"blended": 1.0, "input": 0.5, "output": 2.5},
        "benchmarks": {"scores": dict(BENCH_SCORES), "raw_benchmarks": [], "benchmark_coverage": 0.5, "coverage_with_supplements": 0.5},
    }
    base.update(over)
    return base

# ---- AC1: backfill and build_all use new shape; Tier derived on read not stored ----

def test_tier_never_persisted_anywhere():
    rec = ModelInfoRecord.from_provider_record(_strong_rec(tier="max", decision="keep"), provider="openrouter", evaluated_at=NOW_A)
    d = rec.to_dict()
    # No tier in any persisted key
    assert "tier" not in json.dumps(d)
    assert "tier" not in d.get("judge", {})
    assert "tier" not in d.get("judgement", {})
    assert "tier" not in d.get("facts", {})
    # Legacy Judge object still exists but tier is None / not persisted
    assert rec.judge is not None
    assert rec.judge.tier is None or "tier" not in rec.judge.to_dict()
    # Tier derived on read via categorize
    assert derive_tier(rec, model_id="gpt-4o", coding_score=50.0) == "flash"
    # Round trip still no tier
    rec2 = ModelInfoRecord.from_dict(d)
    d2 = rec2.to_dict()
    assert "tier" not in json.dumps(d2)

def test_backfill_uses_new_shape_no_tier(tmp_path: Path):
    from llm_discovery.backfill import backfill
    import yaml
    results = tmp_path / "results"
    results.mkdir()
    rec = _strong_rec()
    rec["tier"] = "max"  # yaml may contain tier, but backfill must not persist it
    (results / "a.yaml").write_text(yaml.safe_dump({"provider": "openrouter", "evaluated_at": NOW_A, "keep": [rec], "drop_llm": [], "error": []}))
    store_path = tmp_path / "store.json"
    stats = backfill(results_dir=results, store_path=store_path)
    assert stats["unique_models"] == 1
    raw = json.loads(store_path.read_text())
    assert raw["version"] == 2
    stored = raw["models"]["gpt-4o"]
    # New shape present
    assert "facts" in stored
    assert "evidence_snapshot_hash" in stored
    assert "judgement" in stored
    # No tier anywhere
    assert "tier" not in json.dumps(stored)
    # Tier derived on read
    store = ModelInfoStore(store_path)
    rec_loaded = store.get("gpt-4o")
    assert rec_loaded is not None
    tier = derive_tier(rec_loaded, model_id="gpt-4o", coding_score=50.0)
    assert tier in ("flash", "max", "contributor_free")

def test_old_tier_file_compat_read_still_works(tmp_path: Path):
    # Old file with judge.tier must still load (compat) but next save drops tier
    store_path = tmp_path / "store.json"
    legacy_payload = {
        "version": 2,
        "models": {
            "legacy": {
                "benchmarks": {"scores": {"swe_bench_verified": {"score": 55}}, "raw_benchmarks": []},
                "pricing": {"blended": 1.0, "input": 0.5, "output": 2.5, "per_provider_overrides": {}},
                "_meta": {"first_seen": NOW_A, "last_updated": NOW_A, "version": 2},
                "judge": {"evidence_level": "strong", "evidence": ["https://example.com/a"], "confidence": 0.9, "coding": True, "decision": "keep", "tier": "flash", "evidence_hash": "abc123"},
            }
        }
    }
    store_path.write_text(json.dumps(legacy_payload))
    store = ModelInfoStore(store_path)
    store.load()
    rec = store.get_by_key("legacy")
    assert rec is not None
    # Compat: tier read but not considered canonical
    assert rec.judge is not None
    # After a put (which merges), tier must not be persisted
    store.put("legacy", rec)
    raw2 = json.loads(store_path.read_text())
    assert "tier" not in json.dumps(raw2["models"]["legacy"])

# ---- AC2: Old persisted Tier fields removed after no caller remains (evaluator derives) ----

def test_evaluator_derives_tier_not_reuses_persisted():
    # Evaluator._build_cached_strong_record must not reuse stored tier; it categorizes fresh
    from llm_discovery.model_info_store import ModelInfoStore
    # Store with cheap pricing -> should derive flash even though AA 60 would be max
    rec = ModelInfoRecord.from_provider_record(_strong_rec(pricing={"blended": 0.15, "input": 0.1, "output": 0.2}), provider="p", evaluated_at=NOW_A)
    # Manually ensure no tier in stored judge
    assert "tier" not in rec.to_dict().get("judge", {})
    # derive_tier with aa 55 should be flash due to cheap pricing, not max
    tier = derive_tier(rec, model_id="gpt-4o", aa_score=55.0)
    assert tier == "flash"
    # Control: without cheap pricing same AA would be max
    from llm_discovery.categorize import categorize_model
    assert categorize_model(True, 55.0, model_id="gpt-4o", pricing_blended=None) == "max"

# ---- AC3: GC via live-set scan (14d) still share-aware; Derived Cache rebuild passes ----

def test_gc_14d_share_aware(tmp_path: Path):
    assert DEFAULT_TTL_DAYS == 14
    store_path = tmp_path / "store.json"
    store = ModelInfoStore(store_path)
    # key a: fresh live, key b: stale live (should stay because live), key c: stale not live (should be GC'd)
    store.put("live-fresh", ModelInfoRecord(
        benchmarks=BenchmarkSnapshot(scores={"s": {"score": 55}}),
        pricing=PricingSnapshot(blended=1.0),
        _meta=StoreMeta(first_seen=_iso_days_ago(20), last_updated=_iso_days_ago(1), version=2),
        judgement=JudgementSnapshot(evidence_level="strong", decision="keep", confidence=0.9, evidence=["https://example.com/a"]),
    ))
    store.put("live-stale", ModelInfoRecord(
        benchmarks=BenchmarkSnapshot(scores={"s": {"score": 55}}),
        pricing=PricingSnapshot(blended=1.0),
        _meta=StoreMeta(first_seen=_iso_days_ago(30), last_updated=_iso_days_ago(20), version=2),
        judgement=JudgementSnapshot(evidence_level="strong", decision="keep", confidence=0.9, evidence=["https://example.com/a"]),
    ))
    store.put("orphan-stale", ModelInfoRecord(
        benchmarks=BenchmarkSnapshot(scores={"s": {"score": 55}}),
        pricing=PricingSnapshot(blended=1.0),
        _meta=StoreMeta(first_seen=_iso_days_ago(30), last_updated=_iso_days_ago(20), version=2),
        judgement=JudgementSnapshot(evidence_level="strong", decision="keep", confidence=0.9, evidence=["https://example.com/a"]),
    ))
    # orphan-fresh should not be GC'd even though orphan, because not stale
    store.put("orphan-fresh", ModelInfoRecord(
        benchmarks=BenchmarkSnapshot(scores={"s": {"score": 55}}),
        pricing=PricingSnapshot(blended=1.0),
        _meta=StoreMeta(first_seen=_iso_days_ago(2), last_updated=_iso_days_ago(2), version=2),
        judgement=JudgementSnapshot(evidence_level="strong", decision="keep", confidence=0.9, evidence=["https://example.com/a"]),
    ))
    live = {"live-fresh", "live-stale", "orphan-fresh"}
    removed = store.gc(live, ttl_days=DEFAULT_TTL_DAYS)
    assert removed == 1
    assert "orphan-stale" not in store.keys()
    assert "live-stale" in store.keys()  # share-aware: live keys never GC'd even if stale
    assert "live-fresh" in store.keys()
    assert "orphan-fresh" in store.keys()

def test_cache_db_rebuild_verify_with_new_shape(tmp_path: Path):
    from llm_discovery.cache_db import rebuild_cache_db, verify_cache_db
    store_path = tmp_path / "store.json"
    db_path = tmp_path / "cache.db"
    store = ModelInfoStore(store_path)
    store.put("gpt-4o", ModelInfoRecord.from_provider_record(_strong_rec(), provider="openrouter", evaluated_at=NOW_A))
    store.put("claude", ModelInfoRecord.from_provider_record(_strong_rec(provider_model_id="claude", benchmarks={"scores": {"livecode": {"score": 60}}}), provider="anthropic", evaluated_at=NOW_B))
    rebuilt = rebuild_cache_db(store_path=store_path, db_path=db_path)
    assert rebuilt["row_count"] == 2
    assert rebuilt["store_size"] == 2
    v = verify_cache_db(store_path=store_path, db_path=db_path)
    assert v["ok"] is True
    assert v["mismatches"] == []

# ---- AC4: Pricing re-average updates tier without LLM (evidence_hash unchanged) ----

def test_pricing_reaverage_updates_tier_without_llm(tmp_path: Path):
    # Initial store with cheap pricing -> flash tier
    store_path = tmp_path / "store.json"
    store = ModelInfoStore(store_path)
    rec_a = ModelInfoRecord.from_provider_record(_strong_rec(pricing={"blended": 0.15, "input": 0.1, "output": 0.2}), provider="provider-a", evaluated_at=NOW_A)
    store.put("test-model", rec_a)
    loaded_a = store.get_by_key("test-model")
    assert loaded_a is not None
    hash_a = loaded_a.evidence_snapshot_hash
    tier_a = derive_tier(loaded_a, model_id="test-model", aa_score=55.0)
    # Cheap pricing demotes max -> flash
    assert tier_a == "flash"
    # Second provider with expensive pricing, same AA/bench/URLs -> hash unchanged (no pricing in hash)
    rec_b = ModelInfoRecord.from_provider_record(_strong_rec(pricing={"blended": 10.0, "input": 8.0, "output": 12.0}), provider="provider-b", evaluated_at=NOW_B)
    store.put("test-model", rec_b)
    loaded_b = store.get_by_key("test-model")
    assert loaded_b is not None
    hash_b = loaded_b.evidence_snapshot_hash
    # Hash unchanged because evidence_snapshot_hash excludes pricing
    assert hash_a == hash_b
    # Pricing re-averaged via facts observations
    assert loaded_b.facts is not None
    assert len(loaded_b.facts.pricing_observations) == 2
    agg = aggregate_pricing(loaded_b.facts.pricing_observations)
    assert agg is not None
    # Blended should be ~ (0.15+10)/2 = 5.075, not cheap, so tier becomes max
    expected_blended = (0.15 + 10.0) / 2
    assert abs(loaded_b.pricing.blended - expected_blended) < 1e-6
    tier_b = derive_tier(loaded_b, model_id="test-model", aa_score=55.0)
    assert tier_b == "max"
    # Tier changed without LLM, hash unchanged
    assert tier_a != tier_b
    assert hash_a == hash_b

