# ADR 0006: Accurate-enough gate and store as source of truth (slim v2 update)

## Status
Accepted — Issue #83 grilling (part of #80 Wayfinder), 2026-09-04. Supersedes file-level TTL and dual-truth assumptions from ADR 0005. Updated by #95/#98 to slim v2 (store holds only benchmarks+pricing+_meta, version 2).

## Context
Research #81 audited 122 keeps across 17 providers: 111 strong (0.23 benchmark_coverage, 96% coding_score), 7 moderate (0.04 coverage, 14% coding_score, 4/7 suspect AA-only), 3 weak router keeps, plus 4 hallucinated evidence strings, 10.7% cs_null, 8.2% no_pricing, 65 UUID cloudflare records. Research #82 measured 506 records: 40.5% AA match, 59% null benchmark_coverage, keeps far better than drops but still shallow coverage. Wayfinder #80 needs a safe 14-day reuse gate without hiding stale or mis-identified records. Open: which evidence_level/confidence combos may be cached, whether backfill file-level should_cache/is_stale stays or store becomes primary with YAML ephemeral, and concrete accuracy floors.

## Decision

### 1. Keeper eligibility — strong only
`should_cache` and the Accurate-Enough Gate allow only `evidence_level == strong`. Moderate and weak are Candidates and are never inserted into the store; they are re-evaluated every build. `confidence` is stored but not used for gating. Deterministic promotion (PolicyGate `_deterministic_evidence_level`) that upgrades moderate → strong makes the record cacheable as strong; claim-only moderate that stays moderate remains non-cacheable. Router keeps (`kilo-auto/free`, `openrouter/free`, generic `router`/`auto+free`) are always keep via `_is_router_model` but tagged `router=true` and excluded from coding Keeper counts.

### 2. Source of truth — store primary (slim v2), YAML ephemeral
`data/model_info_store.json` v2 is the Source of Truth (slim: {benchmarks, pricing, _meta} per normalized key). `data/results/*.yaml` are Ephemeral Reports: overwritten each `discover_provider`/`build_all` run, gitignored, used only as transient backfill input. Audit trail for benchmarks/pricing lives in store; full evidence audit lives in YAML + git history. Store no longer holds evidence/tier/confidence/source_providers (dropped in #95, contracted in #98).

### 3. Accurate-Enough Gate floors (all must pass for Keeper) — enforced at pipeline before store write
- `evidence_level == strong`
- `coding_score != null`
- pricing present OR free-marker exception (`:free`/`-free`/`_free`/`/free` in model_id OR AA `pricing.blended == 0`)
- `aa_model_id != null` OR supplement bench `>=50` (swe_bench_verified, terminal_bench, terminal_bench_2_1) with a first-party http URL in evidence
- `benchmark_coverage >= 0.25` (one KEY_SIGNAL; `aa_intelligence` alone counts)
- evidence contains at least one `http` URL
- model_id is not UUID-shaped and not in hallucinated denylist (`tokenmix.ai`, `callsphere.ai`, `benchlm`)

Failing any floor means Candidate, not Keeper, even if `decision == keep`. Slim v2 store holds only Keepers; gate still decides keep before put, but slim record does not persist the gate fields.

### 4. TTL — per-record, 14 days
Replace file-level `is_stale(evaluated_at, 14)` with per-record `is_stale(StoreMeta.last_updated, 14)`. `DEFAULT_TTL_DAYS = 14`, `StoreMeta.last_updated` set on every `put`/`merge_records`. `get_if_fresh(key, ttl_days=14)` is the reuse check for `build_all` selective rebuild (#86). File `evaluated_at` no longer gates reuse.

### 5. Migration — hard purge
Next backfill/build applies the new gate and removes keys that fail it. Store shrinks to Keeper-only. Git history preserves the prior file. No quarantine layer.

## Considered Options
- Strong+moderate caching with guards (rejected: moderate keeps are 0.04 coverage and AA-only, would pollute TTL).
- Dual truth keeping YAML for audit (rejected: duplicates Source of Truth, store already carries provenance).
- File-level TTL kept alongside per-record (rejected: masks per-model staleness in mixed-age files).
- Pricing strictly required (rejected: free routers would never be Keepers).
- Higher coverage floor 0.5 (rejected: would over-block current strong keeps at 0.23 avg; 0.25 plus coding_score floor is tighter while allowing AA-only strong with bench supplement).

## Consequences (slim v2)
- `model_info_store.py`: slim `StoreMeta` {first_seen,last_updated,version:2} and `ModelInfoRecord` {benchmarks,pricing,_meta} only; dropped fields not persisted; `from_dict` compat reads v1; `should_cache`/`is_accurate_enough` removed from store (gate at pipeline/backfill only where YAML still has those fields, but store layer no longer checks them).
- `backfill.py`: merges deduped keep records via `merge_records` + `aggregate_pricing`; slim store receives only benchmarks/pricing (gate at source YAML before merge, but store no longer enforces legacy fields).
- `build_all.py`: selective rebuild consults `get_if_fresh` per key (Pricing TTL 14d) + model-list churn; YAML writes remain ephemeral.
- Cloudflare UUID→human mapping done in #84; slim migration purged remaining non-Keepers.

## References
- #80 Wayfinder map, #81 audit (122 keeps, 5 modes), #82 coverage gaps (506 records), #83 grilling
- src/llm_discovery/policy_gate.py, model_info_store.py, backfill.py, build_all.py