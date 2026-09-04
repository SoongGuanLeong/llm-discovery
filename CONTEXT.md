# LLM Discovery

Single context for discovering, evaluating, and caching LLM models suitable for coding tasks.

## Language

### Discovery & Evaluation

**Keeper**:
A model record that passed the Accurate-Enough Gate and is eligible for caching in the store and for 14-day TTL reuse.
_Avoid_: keep, approved model

**Candidate**:
A model record that has not passed the Accurate-Enough Gate (moderate, weak, or failing floors) and must be re-evaluated on every build.
_Avoid_: non-keeper, suspect keep

**Evidence Level**:
LLM-judge assessment of supporting evidence strength: strong, moderate, or weak. Promoted deterministically by AA score, coding_score, and benchmark coverage but never demoted from strong.
_Avoid_: confidence, trust level

**Benchmark Coverage**:
Share of KEY_SIGNALS (aa_intelligence, swe_bench_verified, livecodebench, humaneval) present in a record. Used as a floor for the Accurate-Enough Gate.
_Avoid_: coverage, bench score

### Source of Truth

**Source of Truth**:
The committed `data/model_info_store.json` store. It is the only durable artifact consulted for TTL reuse; per-model lookups normalize via `normalize_store_key`.
_Avoid_: cache, model cache, database

**Ephemeral Report**:
A per-provider `data/results/<provider>.yaml` file produced by discovery. Overwritten on each build and gitignored; its keep records are backfilled into the Source of Truth but the file itself is not retained for audit.
_Avoid_: results file, YAML store, provider report

**Accurate-Enough Gate**:
Predicate that decides whether a keep record may become a Keeper. Requires: evidence_level == strong, coding_score != null, pricing present or free-marker exception, aa_model_id present or qualifying supplement (SWE/Terminal >=50) with URL, benchmark_coverage >=0.25, at least one http URL in evidence, and no hallucinated or UUID model_id.
_Avoid_: eligibility gate, cache gate

**Record TTL**:
Per-record freshness window tracked by `StoreMeta.last_updated`. A Keeper with age <= 14 days may be reused without re-evaluation; older records are stale and must be rebuilt.
_Avoid_: file TTL, cache expiry, 14-day skip

**Hallucinated Evidence**:
Evidence strings that cite non-existent benchmarks or unverified domains (e.g. tokenmix.ai, callsphere.ai, benchlm) without a first-party URL. Such records fail the Accurate-Enough Gate.
_Avoid_: fake evidence, weak claim


**Invalidation Signal**:
Ranked condition that forces a Keeper to be rebuilt even before Record TTL expiry. Order: Identity Integrity → Model-List Churn (new) → Evidence / Benchmark Delta (including Pricing Delta) → Time TTL. Checked in build_all before reuse.
_Avoid_: stale reason, expiry trigger

**Model-List Churn**:
Difference between the current discovered normalized keys and the keys stored for a provider. New keys are always built; removed keys are retained 14 days then GC if not rediscovered and not shared by another provider.
_Avoid_: provider diff, list drift

**Evidence Delta**:
Change between fresh catalog/benchmark lookup and stored ModelInfoRecord that exceeds a threshold (AA ≥2.0, new KEY_SIGNAL, score ≥10%, or benchmark_coverage crossing 0.25). Forces rebuild regardless of TTL.
_Avoid_: score change, benchmark drift

**Pricing Delta**:
Subset of Evidence Delta for blended pricing (3:1) where absolute ≥0.05 $/1M or relative ≥10% forces rebuild. Free-marker exception still satisfies pricing presence floor.
_Avoid_: price change, cost drift

**UUID Model Id**:
A provider model_id that is a UUID (8-4-4-4-12 hex) rather than a human name. Never cacheable; blocked by the gate until a human-name mapping exists.
_Avoid_: infra id, opaque id