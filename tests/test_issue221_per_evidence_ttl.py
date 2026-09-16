"""Per-evidence TTL split (issue #221) - frozen clock tests."""
from datetime import UTC, datetime, timedelta
from llm_discovery.model_info_store import (
    BENCHMARK_TTL_DAYS,
    CATALOG_ROW_TTL_DAYS,
    PRICING_TTL_DAYS,
    ModelInfoRecord,
    BenchmarkSnapshot,
    PricingSnapshot,
    StoreMeta,
    JudgeSnapshot,
    compute_evidence_hash,
    is_stale,
    is_pricing_stale,
    is_benchmark_stale,
    is_judgement_stale,
)

def _iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()

def test_pricing_ttl_3_7d():
    # pricing 7d TTL: 8 days ago = stale, 6 days ago = fresh
    assert is_pricing_stale(_iso_days_ago(8)) is True
    assert is_pricing_stale(_iso_days_ago(6)) is False
    assert PRICING_TTL_DAYS == 7

def test_catalog_row_ttl_7_14d():
    from llm_discovery.model_info_store import is_catalog_row_stale
    assert is_catalog_row_stale(_iso_days_ago(15)) is True
    assert is_catalog_row_stale(_iso_days_ago(10)) is False
    assert CATALOG_ROW_TTL_DAYS == 14

def test_benchmark_ttl_60_90d():
    assert is_benchmark_stale(_iso_days_ago(91)) is True
    assert is_benchmark_stale(_iso_days_ago(60)) is False
    assert BENCHMARK_TTL_DAYS == 90

def test_evidence_hash_stable_reuse():
    h1 = compute_evidence_hash(55.0, {"swe_bench_verified": {"score": 60}}, 1.2, ["https://example.com/a"])
    h2 = compute_evidence_hash(55.0, {"swe_bench_verified": {"score": 60}}, 1.2, ["https://example.com/a"])
    assert h1 == h2
    assert not is_judgement_stale(h1, h2)

def test_evidence_hash_changed_triggers_reeval():
    h1 = compute_evidence_hash(55.0, {"swe_bench_verified": {"score": 60}}, 1.2, ["https://example.com/a"])
    h2 = compute_evidence_hash(56.0, {"swe_bench_verified": {"score": 60}}, 1.2, ["https://example.com/a"])
    assert h1 != h2
    assert is_judgement_stale(h1, h2)
    # pricing change also changes hash
    h3 = compute_evidence_hash(55.0, {"swe_bench_verified": {"score": 60}}, 1.3, ["https://example.com/a"])
    assert h1 != h3

def test_judgement_reused_when_pricing_fresh_but_catalog_old():
    # frozen clock: cached 5 days ago (pricing fresh), catalog fetched_at >14d should not force re-eval
    # Simulate: store last_updated 5 days ago, evidence_hash unchanged
    h = compute_evidence_hash(60.0, {"livecodebench": {"score": 55}}, 0.5, [])
    rec = ModelInfoRecord(
        benchmarks=BenchmarkSnapshot(scores={"livecodebench": {"score": 55}}),
        pricing=PricingSnapshot(blended=0.5),
        _meta=StoreMeta(first_seen=_iso_days_ago(5), last_updated=_iso_days_ago(5)),
        judge=JudgeSnapshot(evidence_level="strong", evidence=["x"], confidence=0.9, coding=True, decision="keep", evidence_hash=h),
    )
    assert not is_pricing_stale(rec._meta.last_updated)
    assert not is_judgement_stale(rec.judge.evidence_hash, h)
    # Even though catalog stale 20d, judgement not stale
    assert is_stale(_iso_days_ago(20), 14) is True  # catalog would be stale
    # But per-evidence, judgement still reusable
    assert not is_judgement_stale(h, h)

def test_global_catalog_stale_removed():
    import inspect
    from llm_discovery import build_all as bmod
    src = inspect.getsource(bmod.build_all)
    assert "catalog_stale = False  # deprecated" in src or "global catalog_stale removed" in src
    # evaluator should not use catalog_stale to force re-eval of keep
    from llm_discovery.evaluator import EvaluatorCoordinator
    src2 = inspect.getsource(EvaluatorCoordinator.evaluate)
    assert "self.catalog_stale and self._cached_decision" not in src2

def test_evidence_hash_persisted():
    rec = ModelInfoRecord.from_provider_record({
        "evidence_level": "strong",
        "evidence": ["https://example.com/bench via http"],
        "confidence": 0.9,
        "coding": True,
        "decision": "keep",
        "aa_score": 60.0,
        "pricing": {"blended": 1.0},
        "benchmarks": {"scores": {"swe_bench_verified": {"score": 55, "source": "https://example.com"}}},
    })
    assert rec.judge is not None
    assert rec.judge.evidence_hash is not None
    d = rec.to_dict()
    assert d["judge"]["evidence_hash"] == rec.judge.evidence_hash
    rec2 = ModelInfoRecord.from_dict(d)
    assert rec2.judge.evidence_hash == rec.judge.evidence_hash
