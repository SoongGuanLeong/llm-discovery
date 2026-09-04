# ADR 0006: Accurate-enough gate and store as source of truth

## Status
Accepted — Issue #83 grilling (part of #80 Wayfinder), 2026-09-04. Supersedes file-level TTL and dual-truth assumptions from ADR 0005.

## Context
Research #81 audited 122 keeps across 17 providers: 111 strong (0.23 benchmark_coverage, 96% coding_score), 7 moderate (0.04 coverage, 14% coding_score, 4/7 suspect AA-only), 3 weak router keeps, plus 4 hallucinated evidence strings, 10.7% cs_null, 8.2% no_pricing, 65 UUID cloudflare records. Research #82 measured 506 records: 40.5% AA match, 59% null benchmark_coverage, keeps far better than drops but still shallow coverage. Wayfinder #80 needs a safe 14-day reuse gate without hiding stale or mis-identified records. Open: which evidence_level/confidence combos may be cached, whether backfill file-level should_cache/is_stale stays or store becomes primary with YAML ephemeral, and concrete accuracy floors.

## Decision

### 1. Keeper eligibility — strong only
`should_cache` and the Accurate-Enough Gate allow only `evidence_level == strong`. Moderate and weak are Candidates and are never inserted into the store; they are re-evaluated every build. `confidence` is stored but not used for gating. Deterministic promotion (PolicyGate `_deterministic_evidence_level`) that upgrades moderate → strong makes the record cacheable as strong; claim-only moderate that stays moderate remains non-cacheable. Router keeps (`kilo-auto/free`, `openrouter/free`, generic `router`/`auto+free`) are always keep via `_is_router_model` but tagged `router=true` and excluded from coding Keeper counts.

### 2. Source of truth — store primary, YAML ephemeral
`data/model_info_store.json` is the Source of Truth. `data/results/*.yaml` are Ephemeral Reports: overwritten each `discover_provider`/`build_all` run, gitignored, used only as transient backfill input. Audit trail lives in `ModelInfoRecord` (evidence[], benchmarks, pricing) plus `StoreMeta` (first_seen, last_updated, source_providers, source_evidence_levels). No file-level retention beyond the current run.

### 3. Accurate-Enough Gate floors (all must pass for Keeper)
- `evidence_level == strong`
- `coding_score != null`
- pricing present OR free-marker exception (`:free`/`-free`/`_free`/`/free` in model_id OR AA `pricing.blended == 0`)
- `aa_model_id != null` OR supplement bench `>=50` (swe_bench_verified, terminal_bench, terminal_bench_2_1) with a first-party http URL in evidence
- `benchmark_coverage >= 0.25` (one KEY_SIGNAL; `aa_intelligence` alone counts)
- evidence contains at least one `http` URL
- model_id is not UUID-shaped (`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$` case-insensitive, dashed or undashed 32-hex) and not in hallucinated denylist (`tokenmix.ai`, `callsphere.ai`, `benchlm`)

Failing any floor means Candidate, not Keeper, even if `decision == keep`.

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

## Consequences
- `model_info_store.py`: `CACHEABLE_LEVELS = {"strong"}`, `should_cache` enforces strong-only, new `is_accurate_enough()` predicate implements floors, `is_stale` now per-record, UUID/denylist checks added.
- `backfill.py`: drop file-level `is_stale` skip, iterate keeps and apply gate per record, purge failing keys.
- `build_all.py`: selective rebuild consults `get_if_fresh` per key; YAML writes remain but are not read for reuse.
- Cloudflare stays 0 Keepers until #84 maps UUID → human name.
- Follow-ups: #85 refines TTL signals, #86 prototypes selective build_all, #87 hardens YAML gate to mirror store gate.

## References
- #80 Wayfinder map, #81 audit (122 keeps, 5 modes), #82 coverage gaps (506 records), #83 grilling
- src/llm_discovery/policy_gate.py, model_info_store.py, backfill.py, build_all.py
