"""Demo: mocked discovery + catalog deltas showing per-model reuse vs rebuild"""
from pathlib import Path
import tempfile, json, sys
from datetime import UTC, datetime, timedelta
sys.path.insert(0, "src")
sys.path.insert(0, ".")
from llm_discovery.model_info_store import ModelInfoStore, ModelInfoRecord, BenchmarkSnapshot, PricingSnapshot, normalize_store_key, DEFAULT_TTL_DAYS
from prototypes.issue86.intelligent_build import intelligent_build  # noqa

def make_keeper(model_id, aa_score=55, coding_score=55, blended=0.5, bench_cov=0.5, age_days=2, evidence_level="strong"):
    now = datetime.now(UTC)
    last = (now - timedelta(days=age_days)).isoformat()
    first = (now - timedelta(days=age_days+5)).isoformat()
    rec = ModelInfoRecord(
        aa_model_id=model_id, aa_score=aa_score, coding_score=coding_score,
        benchmarks=BenchmarkSnapshot(scores={"aa_intelligence": {"score": aa_score, "source": "aa"}}, benchmark_coverage=bench_cov),
        evidence=[f"AA {aa_score}", f"https://example.com/{model_id}"], evidence_level=evidence_level, confidence=0.9, tier="flash",
        pricing=PricingSnapshot(blended=blended, input=0.3, output=0.9),
    )
    rec._meta.first_seen = first
    rec._meta.last_updated = last
    rec._meta.source_providers = ["groq"]
    rec._meta.source_evidence_levels = [evidence_level]
    return rec

def build_fn_mock(key, meta, fresh):
    # simulate LLM judge + packet: fresh catalog drives record, counts as LLM call
    build_fn_mock.calls += 1
    aa_score = fresh.get("aa_score", 55) if fresh else 55
    coding = fresh.get("coding_score", 55) if fresh else 55
    blended = 0.5
    if fresh and fresh.get("pricing"):
        p = fresh["pricing"]
        if isinstance(p, dict):
            blended = p.get("blended", p.get("price_1m_blended_3_to_1", 0.5))
        else:
            blended = float(p)
    bm = fresh.get("benchmarks", {}) if fresh else {}
    rec = ModelInfoRecord(
        aa_model_id=fresh.get("aa_model_id", key) if fresh else key,
        aa_score=aa_score, coding_score=coding,
        benchmarks=BenchmarkSnapshot(scores=dict(bm.get("scores", {"aa_intelligence": {"score": aa_score}})), benchmark_coverage=bm.get("benchmark_coverage", 0.5)),
        evidence=fresh.get("evidence", [f"AA {aa_score}"]) if fresh else [f"AA {aa_score}"],
        evidence_level="strong", confidence=0.92, tier="flash",
        pricing=PricingSnapshot(blended=blended, input=0.3, output=0.9),
    )
    now = datetime.now(UTC).isoformat()
    rec._meta.first_seen = now
    rec._meta.last_updated = now
    rec._meta.source_providers = [meta.get("provider", "mock")]
    rec._meta.source_evidence_levels = ["strong"]
    return rec
build_fn_mock.calls = 0

def run_scenario(name, store_path, discovered, fresh_map):
    build_fn_mock.calls = 0
    print(f"\n=== {name} ===")
    store_before = ModelInfoStore(store_path)
    store_before.load()
    print(f"store before: size={store_before.size()} keys={sorted(store_before._data.keys())}")
    res = intelligent_build(discovered, fresh_map, store_path, build_fn_mock)
    print(f"result: discovered={res['discovered']} reused={res['reused']} rebuilt={res['rebuilt']} gc={res['gc_candidates']} store_size={res['store_size']} reuse%={res['reuse_pct']}")
    print(f"reasons: {res['reasons']}")
    print(f"LLM judge calls (build_fn): {build_fn_mock.calls}  (should == rebuilt)")
    print(f"Evaluator packet skipped for reused={res['reused']} models (packet is deterministic, fresh catalog cheap)")
    store_after = ModelInfoStore(store_path)
    store_after.load()
    for k, rec in sorted(store_after._data.items()):
        cov = rec.benchmarks.benchmark_coverage if rec.benchmarks else None
        bl = rec.pricing.blended if rec.pricing else None
        print(f"  {k}: aa={rec.aa_score} cov={cov} blended={bl} updated={rec._meta.last_updated[:10]}")
    return res

def main():
    with tempfile.TemporaryDirectory() as tmp:
        store_path = Path(tmp) / "model_info_store.json"
        # Seed store: 3 Keepers — one fresh, one stale, one fresh with gap
        seed = ModelInfoStore(store_path)
        seed.load()
        # fresh reuse candidate (age 2d, no delta)
        seed.put("fresh-model", make_keeper("fresh-model", aa_score=55, blended=0.5, bench_cov=0.5, age_days=2))
        # stale candidate (age 20d >14)
        seed.put("stale-model", make_keeper("stale-model", aa_score=50, blended=0.4, bench_cov=0.25, age_days=20))
        # fresh but will have pricing delta
        seed.put("pricing-delta-model", make_keeper("pricing-delta-model", aa_score=56, blended=0.5, bench_cov=0.5, age_days=1))
        # gap model: fresh but benchmarks missing one signal
        seed.put("gap-model", make_keeper("gap-model", aa_score=60, blended=0.6, bench_cov=0.25, age_days=1))
        # set gap-model to have only aa_intelligence, fresh will add swe_bench
        rec_gap = seed.get_by_key("gap-model")
        rec_gap.benchmarks.scores = {"aa_intelligence": {"score": 60, "source": "aa"}}
        seed.save()
        print(f"Seeded store_size={seed.size()}")

        # Scenario 1: mixed discovery (fresh reuse + stale rebuild + pricing delta rebuild + new id + gap-fill reuse)
        discovered = {
            "fresh-model": {"id": "fresh-model", "provider": "groq"},
            "stale-model": {"id": "stale-model", "provider": "groq"},
            "pricing-delta-model": {"id": "pricing-delta-model", "provider": "groq"},
            "gap-model": {"id": "gap-model", "provider": "groq"},
            "new-model": {"id": "new-model", "provider": "groq"},
        }
        fresh_map = {
            "fresh-model": {"aa_model_id": "fresh-model", "aa_score": 55, "coding_score": 55, "evidence": ["AA 55", "https://example.com/fresh"], "pricing": {"blended": 0.5}, "benchmarks": {"scores": {"aa_intelligence": {"score": 55}}, "benchmark_coverage": 0.5}},
            "stale-model": {"aa_model_id": "stale-model", "aa_score": 50, "coding_score": 50, "evidence": ["AA 50"], "pricing": {"blended": 0.4}, "benchmarks": {"scores": {"aa_intelligence": {"score": 50}}, "benchmark_coverage": 0.25}},
            "pricing-delta-model": {"aa_model_id": "pricing-delta-model", "aa_score": 56, "coding_score": 56, "evidence": ["AA 56"], "pricing": {"blended": 0.575}, "benchmarks": {"scores": {"aa_intelligence": {"score": 56}}, "benchmark_coverage": 0.5}},  # +0.15 delta triggers rebuild
            "gap-model": {"aa_model_id": "gap-model", "aa_score": 60, "coding_score": 60, "evidence": ["AA 60"], "pricing": {"blended": 0.6}, "benchmarks": {"scores": {"aa_intelligence": {"score": 60}, "swe_bench_verified": {"score": 70, "source": "models_dev"}}, "benchmark_coverage": 0.5}},  # new signal -> should rebuild per Rank4? Actually gap-model stored 0.25, fresh 0.5 with new signal => delta triggers rebuild, demonstrate gap-fill vs rebuild distinction
            "new-model": {"aa_model_id": "new-model", "aa_score": 70, "coding_score": 70, "evidence": ["AA 70"], "pricing": {"blended": 0.7}, "benchmarks": {"scores": {"aa_intelligence": {"score": 70}}, "benchmark_coverage": 0.5}},
        }
        # But to show pure gap-fill reuse, make gap-model NOT cross threshold: keep same coverage and just add non-KEY signal? Instead keep gap-model fresh but add livecodebench which IS key -> will rebuild. So for gap-fill demo, use fresh-model2 that gains non-key but we treat as gap-fill on reuse path separate scenario
        res1 = run_scenario("Scenario 1: mixed (fresh reuse, stale, pricing delta, new id)", store_path, discovered, fresh_map)

        # Scenario 2: pure gap-fill reuse — modify fresh-model to have extra benchmark that is gap-filled without rebuild (if we disable evidence delta for demo we show reuse + union). Instead run second build with no deltas: all fresh => 100% reuse
        discovered2 = {
            "fresh-model": {"id": "fresh-model", "provider": "groq"},
            "pricing-delta-model": {"id": "pricing-delta-model", "provider": "groq"},  # now fresh after rebuild, should reuse
            "gap-model": {"id": "gap-model", "provider": "groq"},
        }
        fresh_map2 = {
            "fresh-model": {"aa_model_id": "fresh-model", "aa_score": 55, "coding_score": 55, "evidence": ["AA 55"], "pricing": {"blended": 0.5}, "benchmarks": {"scores": {"aa_intelligence": {"score": 55}}, "benchmark_coverage": 0.5}},
            "pricing-delta-model": {"aa_model_id": "pricing-delta-model", "aa_score": 56, "coding_score": 56, "evidence": ["AA 56"], "pricing": {"blended": 0.575}, "benchmarks": {"scores": {"aa_intelligence": {"score": 56}}, "benchmark_coverage": 0.5}},
            "gap-model": {"aa_model_id": "gap-model", "aa_score": 60, "coding_score": 60, "evidence": ["AA 60"], "pricing": {"blended": 0.6}, "benchmarks": {"scores": {"aa_intelligence": {"score": 60}, "swe_bench_verified": {"score": 70}}, "benchmark_coverage": 0.5}},
        }
        res2 = run_scenario("Scenario 2: second build same ids no delta (100% reuse, packet+LLM skipped)", store_path, discovered2, fresh_map2)

        # Scenario 3: identity bad (UUID) + removed id GC
        discovered3 = {
            "fresh-model": {"id": "fresh-model", "provider": "groq"},
            "bad-uuid-model": {"id": "01564c52-8717-47dc-8efd-907a2ca18301", "provider": "cloudflare"},
        }
        fresh_map3 = {
            "fresh-model": {"aa_model_id": "fresh-model", "aa_score": 55, "coding_score": 55, "evidence": ["AA 55"], "pricing": {"blended": 0.5}, "benchmarks": {"scores": {"aa_intelligence": {"score": 55}}, "benchmark_coverage": 0.5}},
            "bad-uuid-model": {"aa_model_id": None, "aa_score": None, "evidence": [], "pricing": None, "benchmarks": {}},
        }
        res3 = run_scenario("Scenario 3: UUID identity bad forces rebuild (TTL0) + GC stale-model not discovered", store_path, discovered3, fresh_map3)

        print("\n=== Summary across scenarios ===")
        for i, r in enumerate([res1, res2, res3], 1):
            print(f"S{i}: discovered={r['discovered']} reused={r['reused']} rebuilt={r['rebuilt']} store={r['store_size']} reuse%={r['reuse_pct']} reasons={r['reasons']}")
        # write before_after stats for ticket
        stats = {"s1": res1, "s2": res2, "s3": res3}
        Path("prototypes/issue86/before_after.json").write_text(json.dumps(stats, indent=2))
        print("\nWrote prototypes/issue86/before_after.json")

if __name__ == "__main__":
    main()
