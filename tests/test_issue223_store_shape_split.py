"""Store shape split - EXPAND step (issue #223).

New record shape written BESIDE the old slim-v2 keys:

    {benchmarks, pricing, _meta, judge?,
     facts: {aa_row_hash, pricing_observations[], bench_raw}?,
     evidence_snapshot_hash?,
     judgement: {evidence_level, decision, confidence, evidence[], judge_model}?}

Old keys (benchmarks, pricing, _meta, judge) keep being written and read exactly
as today; STORE_FILE_VERSION stays 2. Tier is not persisted in the new shape -
it is derived on read via categorize (derive_tier). Offline, deterministic
tests (ISO timestamps), no network - same conventions as
tests/test_issue221_per_evidence_ttl.py.
"""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from llm_discovery.model_info_store import (
    STORE_FILE_VERSION,
    BenchmarkSnapshot,
    JudgeSnapshot,
    JudgementSnapshot,
    ModelInfoRecord,
    ModelInfoStore,
    PricingSnapshot,
    StoreMeta,
    aggregate_pricing,
    compute_evidence_hash,
    derive_tier,
    merge_records,
)
from llm_discovery.evaluator import _pricing_is_stale, classify_hit

NOW_A = "2026-09-04T01:00:00+00:00"
NOW_B = "2026-09-04T02:00:00+00:00"
NOW_C = "2026-09-04T03:00:00+00:00"

EVIDENCE_URLS = [
    "https://example.com/aa",
    "https://example.com/bench",
    "https://example.com/price",
    "https://example.com/extra",
]
BENCH_SCORES = {"swe_bench_verified": {"score": 55, "source": "https://example.com"}}
PRICING_A = {"blended": 1.0, "input": 0.5, "output": 2.5}
PRICING_B = {"blended": 1.2, "input": 0.6, "output": 2.9}


def _iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _strong_rec(**over) -> dict:
    base = {
        "provider_model_id": "openai/gpt-4o",
        "evidence_level": "strong",
        "evidence": list(EVIDENCE_URLS),
        "confidence": 0.9,
        "coding": True,
        "decision": "keep",
        "judge_model": "judge-1",
        "aa_score": 60.0,
        "pricing": dict(PRICING_A),
        "benchmarks": {"scores": dict(BENCH_SCORES), "raw_benchmarks": []},
    }
    base.update(over)
    return base


def _v2_only_payload(last_updated: str) -> dict:
    """A v2 slim store record with NO new-shape keys (pre-#223 payload)."""
    return {
        "benchmarks": {"scores": {"swe_bench_verified": {"score": 55}}, "raw_benchmarks": []},
        "pricing": {"blended": 1.0, "input": 0.5, "output": 2.5, "per_provider_overrides": {}},
        "_meta": {"first_seen": NOW_A, "last_updated": last_updated, "version": 2},
        "judge": {"evidence_level": "strong", "evidence": ["x"], "confidence": 0.9, "coding": True, "decision": "keep"},
    }


# ---------------------------------------------------------------------------
# AC1: new shape written alongside old without breaking existing readers
# ---------------------------------------------------------------------------


def test_ac1_new_keys_written_beside_old():
    rec = ModelInfoRecord.from_provider_record(_strong_rec(), provider="openrouter", evaluated_at=NOW_A)
    d = rec.to_dict()
    # Old keys unchanged
    assert {"benchmarks", "pricing", "_meta", "judge"} <= set(d)
    assert d["benchmarks"]["scores"] == BENCH_SCORES
    assert d["pricing"]["blended"] == 1.0
    assert d["pricing"]["input"] == 0.5
    assert d["pricing"]["output"] == 2.5
    assert d["_meta"]["first_seen"] == NOW_A
    assert d["_meta"]["last_updated"] == NOW_A
    assert d["_meta"]["version"] == 2
    assert d["judge"]["evidence_level"] == "strong"
    assert d["judge"]["evidence"] == EVIDENCE_URLS[:3]
    # New keys present beside them
    assert {"facts", "evidence_snapshot_hash", "judgement"} <= set(d)
    assert d["facts"]["pricing_observations"] == [
        {"provider": "openrouter", "blended": 1.0, "input": 0.5, "output": 2.5, "ts": NOW_A}
    ]
    assert d["facts"]["bench_raw"] == BENCH_SCORES
    # aa_row_hash: deterministic AA-row identity hash (aa + pricing, no bench/urls)
    assert d["facts"]["aa_row_hash"] == compute_evidence_hash(60.0, None, 1.0, None)
    assert d["judgement"]["evidence_level"] == "strong"
    assert d["judgement"]["decision"] == "keep"
    assert d["judgement"]["confidence"] == 0.9
    assert d["judgement"]["evidence"] == EVIDENCE_URLS[:3]  # capped at 3
    assert d["judgement"]["judge_model"] == "judge-1"


def test_ac1_v2_only_payload_compatible_read():
    rec = ModelInfoRecord.from_dict(_v2_only_payload(_iso_days_ago(1)))
    # New fields absent -> None
    assert rec.facts is None
    assert rec.evidence_snapshot_hash is None
    assert rec.judgement is None
    # Old fields intact
    assert rec.judge is not None and rec.judge.evidence_level == "strong"
    assert rec.pricing.blended == 1.0
    # Existing readers (evaluator) still behave on such old records
    assert classify_hit(rec) == "strong_hit"
    assert _pricing_is_stale(rec) is False
    stale = ModelInfoRecord.from_dict(_v2_only_payload(_iso_days_ago(8)))
    assert _pricing_is_stale(stale) is True
    # Record without judge snapshot is still a miss (existence alone is not a hit)
    bare = {k: v for k, v in _v2_only_payload(NOW_A).items() if k != "judge"}
    assert classify_hit(ModelInfoRecord.from_dict(bare)) == "miss"


def test_old_shape_record_serializes_without_new_keys():
    # A record without new fields serializes with exactly the old keys (byte-identical shape)
    rec = ModelInfoRecord(
        benchmarks=BenchmarkSnapshot(scores={"a": {"score": 10}}),
        pricing=PricingSnapshot(blended=0.5),
        _meta=StoreMeta(first_seen=NOW_A, last_updated=NOW_A, version=2),
    )
    assert set(rec.to_dict()) == {"benchmarks", "pricing", "_meta"}
    rec_j = ModelInfoRecord(
        benchmarks=BenchmarkSnapshot(scores={"a": {"score": 10}}),
        pricing=PricingSnapshot(blended=0.5),
        _meta=StoreMeta(first_seen=NOW_A, last_updated=NOW_A, version=2),
        judge=JudgeSnapshot(evidence_level="strong", evidence=["x"], confidence=0.9, coding=True, decision="keep", tier="flash", evidence_hash="abc"),
    )
    assert set(rec_j.to_dict()) == {"benchmarks", "pricing", "_meta", "judge"}
    # from_dict -> to_dict of a v2 payload keeps the same key set (no new keys appear)
    v2 = _v2_only_payload(NOW_A)
    re = ModelInfoRecord.from_dict(v2).to_dict()
    assert set(re) == set(v2)
    assert re["benchmarks"] == v2["benchmarks"]
    assert re["_meta"] == v2["_meta"]
    assert re["judge"] == v2["judge"]
    assert re["pricing"]["blended"] == 1.0


# ---------------------------------------------------------------------------
# AC2: _meta.last_updated maintained; round trip preserves both shapes
# ---------------------------------------------------------------------------


def test_ac2_merge_keeps_max_last_updated():
    r1 = ModelInfoRecord.from_provider_record(_strong_rec(), provider="openrouter", evaluated_at=NOW_A)
    r2 = ModelInfoRecord.from_provider_record(_strong_rec(pricing=dict(PRICING_B)), provider="groq", evaluated_at=NOW_C)
    m = merge_records(r1, r2)
    assert m._meta.last_updated == NOW_C  # max
    assert m._meta.first_seen == NOW_A  # min
    # Same result when merge order is reversed
    m_rev = merge_records(r2, r1)
    assert m_rev._meta.last_updated == NOW_C
    assert m_rev._meta.first_seen == NOW_A
    # New fields follow the same freshest-wins policy as judge
    assert m.evidence_snapshot_hash == r2.evidence_snapshot_hash
    assert m_rev.evidence_snapshot_hash == r2.evidence_snapshot_hash
    assert m.judgement is not None and m.judgement.decision == "keep"


def test_ac2_store_roundtrip_preserves_both_shapes(tmp_path: Path):
    store_path = tmp_path / "store.json"
    r1 = ModelInfoRecord.from_provider_record(_strong_rec(), provider="openrouter", evaluated_at=NOW_A)
    r2 = ModelInfoRecord.from_provider_record(_strong_rec(pricing=dict(PRICING_B)), provider="groq", evaluated_at=NOW_C)
    store = ModelInfoStore(store_path)
    store.put("gpt-4o", r1)
    store.put("gpt-4o", r2)
    raw = json.loads(store_path.read_text())
    # STORE_FILE_VERSION still 2 (old readers must not break)
    assert STORE_FILE_VERSION == 2
    assert raw["version"] == 2
    on_disk = raw["models"]["gpt-4o"]
    # Old shape on disk, unchanged
    assert on_disk["_meta"]["last_updated"] == NOW_C
    assert on_disk["_meta"]["first_seen"] == NOW_A
    assert on_disk["judge"]["evidence_level"] == "strong"
    assert on_disk["pricing"]["blended"] == pytest.approx(1.1)
    # New shape on disk
    assert on_disk["evidence_snapshot_hash"]
    assert on_disk["judgement"]["evidence_level"] == "strong"
    assert len(on_disk["facts"]["pricing_observations"]) == 2
    # Fresh load: both shapes preserved
    loaded = ModelInfoStore(store_path).get("gpt-4o")
    assert loaded._meta.last_updated == NOW_C
    assert loaded.judge is not None and loaded.judge.evidence_level == "strong"
    assert loaded.judgement is not None and loaded.judgement.decision == "keep"
    assert loaded.evidence_snapshot_hash == on_disk["evidence_snapshot_hash"]
    assert loaded.facts is not None
    assert {o["provider"] for o in loaded.facts.pricing_observations} == {"openrouter", "groq"}
    assert loaded.facts.bench_raw == BENCH_SCORES
    assert loaded.facts.aa_row_hash == on_disk["facts"]["aa_row_hash"]
    # Re-serialization is stable (load -> to_dict equals what is on disk)
    assert loaded.to_dict() == on_disk


# ---------------------------------------------------------------------------
# AC3: pricing observations stored per-provider for later re-average
# ---------------------------------------------------------------------------


def test_ac3_pricing_observations_merge_per_provider():
    r1 = ModelInfoRecord.from_provider_record(_strong_rec(pricing=dict(PRICING_A)), provider="openrouter", evaluated_at=NOW_A)
    r2 = ModelInfoRecord.from_provider_record(_strong_rec(pricing=dict(PRICING_B)), provider="groq", evaluated_at=NOW_C)
    m = merge_records(r1, r2)
    assert m.facts is not None
    obs = m.facts.pricing_observations
    assert len(obs) == 2
    by_prov = {o["provider"]: o for o in obs}
    assert by_prov["openrouter"] == {"provider": "openrouter", "blended": 1.0, "input": 0.5, "output": 2.5, "ts": NOW_A}
    assert by_prov["groq"] == {"provider": "groq", "blended": 1.2, "input": 0.6, "output": 2.9, "ts": NOW_C}
    # Merge order does not matter
    m_rev = merge_records(r2, r1)
    assert {o["provider"]: o["blended"] for o in m_rev.facts.pricing_observations} == {
        "openrouter": 1.0,
        "groq": 1.2,
    }


def test_ac3_same_provider_latest_ts_wins():
    r_old = ModelInfoRecord.from_provider_record(_strong_rec(pricing=dict(PRICING_A)), provider="groq", evaluated_at=NOW_A)
    r_new = ModelInfoRecord.from_provider_record(_strong_rec(pricing=dict(PRICING_B)), provider="groq", evaluated_at=NOW_C)
    m = merge_records(r_old, r_new)
    assert len(m.facts.pricing_observations) == 1
    assert m.facts.pricing_observations[0]["blended"] == 1.2
    assert m.facts.pricing_observations[0]["ts"] == NOW_C
    # Same when the older record is the "existing" side
    m2 = merge_records(r_new, r_old)
    assert len(m2.facts.pricing_observations) == 1
    assert m2.facts.pricing_observations[0]["blended"] == 1.2
    assert m2.facts.pricing_observations[0]["ts"] == NOW_C


def test_ac3_stored_observations_usable_for_reaverage():
    r1 = ModelInfoRecord.from_provider_record(_strong_rec(pricing=dict(PRICING_A)), provider="openrouter", evaluated_at=NOW_A)
    r2 = ModelInfoRecord.from_provider_record(_strong_rec(pricing=dict(PRICING_B)), provider="groq", evaluated_at=NOW_C)
    m = merge_records(r1, r2)
    # aggregate_pricing over the stored per-provider obs re-averages cleanly...
    reavg = aggregate_pricing(list(m.facts.pricing_observations))
    assert reavg is not None
    assert reavg["blended"] == pytest.approx(1.1)
    assert reavg["input"] == pytest.approx(0.55)
    assert reavg["output"] == pytest.approx(2.7)
    # ...and is consistent with the merged old-shape pricing
    assert m.pricing.blended == pytest.approx(1.1)
    assert m.pricing.input == pytest.approx(0.55)
    assert m.pricing.output == pytest.approx(2.7)


# ---------------------------------------------------------------------------
# AC4: evidence_snapshot_hash populated from AA + bench + pricing + URLs
# ---------------------------------------------------------------------------


def test_ac4_evidence_snapshot_hash_matches_and_changes_with_aa():
    rec = ModelInfoRecord.from_provider_record(_strong_rec(), provider="openrouter", evaluated_at=NOW_A)
    # Issue #224 contract: evidence_snapshot_hash excludes pricing_blended (pricing re-average keeps hash unchanged)
    expected = compute_evidence_hash(60.0, dict(BENCH_SCORES), None, list(EVIDENCE_URLS))
    assert rec.evidence_snapshot_hash == expected
    # judge.evidence_hash also excludes pricing after contract (tier derived, no pricing staleness on judgement)
    assert rec.judge is not None
    assert rec.judge.evidence_hash == expected
    # Changes when aa_score changes
    rec2 = ModelInfoRecord.from_provider_record(_strong_rec(aa_score=61.0), provider="openrouter", evaluated_at=NOW_A)
    assert rec2.evidence_snapshot_hash != rec.evidence_snapshot_hash
    assert rec2.evidence_snapshot_hash == compute_evidence_hash(61.0, dict(BENCH_SCORES), None, list(EVIDENCE_URLS))


# ---------------------------------------------------------------------------
# No tier in new shapes; derive_tier on read
# ---------------------------------------------------------------------------


def test_aa_row_hash_deterministic_identity():
    r1 = ModelInfoRecord.from_provider_record(_strong_rec(), provider="a", evaluated_at=NOW_A)
    r2 = ModelInfoRecord.from_provider_record(_strong_rec(), provider="b", evaluated_at=NOW_C)
    assert r1.facts is not None and r2.facts is not None
    # Deterministic: same inputs -> same hash, regardless of provider/ts
    assert r1.facts.aa_row_hash == r2.facts.aa_row_hash
    assert r1.facts.aa_row_hash == compute_evidence_hash(60.0, None, 1.0, None)
    r3 = ModelInfoRecord.from_provider_record(_strong_rec(aa_score=61.0), provider="a", evaluated_at=NOW_A)
    assert r3.facts.aa_row_hash != r1.facts.aa_row_hash


def test_no_tier_key_in_new_shapes():
    rec = ModelInfoRecord.from_provider_record(_strong_rec(tier="max"), provider="openrouter", evaluated_at=NOW_A)
    d = rec.to_dict()
    assert "tier" not in d["judgement"]
    assert "tier" not in d["facts"]
    assert not hasattr(rec.judgement, "tier")
    assert not hasattr(rec.facts, "tier")
    # Contract #224: tier never persisted, not even in legacy judge (derived on read)
    assert "tier" not in d["judge"]
    # Round trip: tier does not leak into any shape
    d2 = ModelInfoRecord.from_dict(d).to_dict()
    assert "tier" not in d2["judgement"]
    assert "tier" not in d2["facts"]
    assert "tier" not in d2.get("judge", {})


def test_judgement_recorded_for_all_evidence_levels():
    for lvl in ("strong", "moderate", "weak", "none", "uncertain", "error"):
        rec = ModelInfoRecord.from_provider_record(_strong_rec(evidence_level=lvl), provider="p", evaluated_at=NOW_A)
        assert rec.judgement is not None, lvl
        assert rec.judgement.evidence_level == lvl
    # No evidence_level -> no judgement
    plain = {k: v for k, v in _strong_rec().items() if k != "evidence_level"}
    assert ModelInfoRecord.from_provider_record(plain, provider="p", evaluated_at=NOW_A).judgement is None
    # Old judge snapshot still only strong+moderate (unchanged)
    assert ModelInfoRecord.from_provider_record(_strong_rec(evidence_level="weak"), provider="p", evaluated_at=NOW_A).judge is None
    assert ModelInfoRecord.from_provider_record(_strong_rec(evidence_level="moderate"), provider="p", evaluated_at=NOW_A).judge is not None


def test_derive_tier_flash_with_pricing_and_coding_score():
    rec = ModelInfoRecord.from_provider_record(_strong_rec(), provider="openrouter", evaluated_at=NOW_A)
    # coding_score 50 (>=35 min, <65 max) + pricing from record -> flash
    assert derive_tier(rec, model_id="gpt-4o", coding_score=50.0) == "flash"


def test_derive_tier_uncertain_without_signals():
    bare = ModelInfoRecord(benchmarks=BenchmarkSnapshot(), pricing=PricingSnapshot(), _meta=StoreMeta())
    assert derive_tier(bare, model_id="some-model") == "uncertain"


def test_derive_tier_uses_record_pricing_to_demote():
    from llm_discovery.categorize import categorize_model

    rec = ModelInfoRecord.from_provider_record(_strong_rec(pricing={"blended": 0.15, "input": 0.1, "output": 0.2}), provider="p", evaluated_at=NOW_A)
    # aa 50 is max-range, but the record's cheap pricing demotes to flash
    assert derive_tier(rec, model_id="gpt-4o", aa_score=50.0) == "flash"
    # Control: without the record's pricing the same score would be max
    assert categorize_model(True, 50.0, model_id="gpt-4o", pricing_blended=None) == "max"


def test_derive_tier_uses_judge_coding_and_error_decision():
    not_coding = ModelInfoRecord(
        judge=JudgeSnapshot(evidence_level="strong", evidence=[], confidence=0.5, coding=False, decision="keep"),
    )
    assert derive_tier(not_coding, model_id="m", coding_score=50.0) == "drop"
    errored = ModelInfoRecord(judgement=JudgementSnapshot(evidence_level="error", decision="error", confidence=0.0, evidence=[]))
    assert derive_tier(errored, model_id="m") == "error"


# ---------------------------------------------------------------------------
# AC1: cache_db (existing reader) works unchanged on new-shape records
# ---------------------------------------------------------------------------


def test_cache_db_rebuild_verify_with_new_shape(tmp_path: Path):
    from llm_discovery.cache_db import rebuild_cache_db, verify_cache_db

    store_path = tmp_path / "model_info_store.json"
    db_path = tmp_path / "cache.db"
    store = ModelInfoStore(store_path)
    # New-shape record + legacy v2-only record side by side
    store.put("gpt-4o", ModelInfoRecord.from_provider_record(_strong_rec(), provider="openrouter", evaluated_at=NOW_A))
    store.merge_from_dict({"legacy-model": _v2_only_payload(NOW_A)})
    on_disk = json.loads(store_path.read_text())
    assert on_disk["version"] == 2
    assert {"benchmarks", "pricing", "_meta", "judge", "facts", "evidence_snapshot_hash", "judgement"} <= set(on_disk["models"]["gpt-4o"])
    # Legacy v2-only record: old keys (incl. optional judge) only, no new keys
    assert set(on_disk["models"]["legacy-model"]) == {"benchmarks", "pricing", "_meta", "judge"}
    assert not ({"facts", "evidence_snapshot_hash", "judgement"} & set(on_disk["models"]["legacy-model"]))
    # cache_db reads only old keys - rebuild + verify must succeed unchanged
    rebuilt = rebuild_cache_db(store_path=store_path, db_path=db_path)
    assert rebuilt["row_count"] == 2
    assert rebuilt["store_size"] == 2
    v = verify_cache_db(store_path=store_path, db_path=db_path)
    assert v["ok"] is True
    assert v["mismatches"] == []
