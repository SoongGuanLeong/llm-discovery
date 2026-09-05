# ADR 0007: Incremental build invalidation policy (per-record TTL and signals) — slim v2

## Status
Accepted — Issue #85 grilling (part of #80 Wayfinder), 2026-09-04. Extends ADR 0006 per-record TTL with ranked invalidation signals. Updated by #91/#95/#98 to slim v2 (Evidence Delta disabled, Pricing TTL 14d only).

## Context
ADR 0006 replaced file-level `is_stale(evaluated_at, 14)` with per-record `is_stale(StoreMeta.last_updated, 14)` and made `data/model_info_store.json` the Source of Truth. File-level TTL hid per-model staleness and model-list churn: a 17-file directory with mixed-age records would skip an entire file while 65 UUID Cloudflare ids stayed unfixable and new true-free ids (stepfun, qwen dot variants) never triggered rebuild. Research #81 showed weak/moderate keeps, hallucinated evidence, and UUID file-shapes; #82 confirmed dot-hyphen benchmark split and 40.5% AA match. Wayfinder #80 needs a safe reuse gate that reuses fresh strong records without LLM calls yet rebuilds on identity fixes, list churn, and evidence drift.

Open questions in #85: rank the four signal families (time TTL, model-list diff, evidence/benchmark delta, identity fix), set default TTL and per-signal overrides, and state where the gate lives (backfill `is_stale` vs `build_all` vs `store.is_stale`).

## Decision

### 1. Signal ranking (highest → lowest) — slim v2 (Wayfinder 91, #95)

1. **Identity Integrity** — UUID-shaped `model_id` or hallucinated denylist or missing human name. Never cacheable; always stale. Overrides all.
2. **Model-List Churn (new ids)** — Discovered `normalize_store_key(id)` not in store → mandatory build. No TTL; new key is unconditionally stale.
3. **Model-List Churn (removed ids)** — Stored key absent from current live normalized set (scanned from all keep lists) → retained 14d then GC if still absent and not shared. Share-aware via live-set scan (no `source_providers` in v2).
4. **Pricing TTL 14d** — If `_meta.last_updated` age >14d, pricing re-averaged via `aggregate_pricing` (no LLM) rather than full rebuild; benchmarks immutable gap-fill only. (Evidence/Benchmark Delta disabled per #91 Q3.)
5. **Time TTL** — Baseline freshness: age ≤14d may be reused if none of 1–3 fire; age >14d stale (same as Pricing TTL in slim v2, but kept as baseline).
6. **Catalog staleness** — `fetched_at` >14d suggests catalog refresh before pricing re-average, but does not alone force rebuild.

### 2. TTL — default and overrides (slim v2)

- **Default TTL:** `DEFAULT_TTL_DAYS = 14`, per-record via `StoreMeta.last_updated`, checked by `get_if_fresh(key, 14)`.
- **Overrides (TTL = 0, always rebuild):** identity failure (Rank 1), new id (Rank 2).
- **Pricing TTL re-average:** when age >14d, re-average pricing from catalog observations (`aggregate_pricing`) instead of full LLM rebuild; benchmarks gap-fill only.
- **Retention TTL for removed ids (Rank 3):** keep 14d after last sighting in live set; purge if still absent and not shared (live-set scan, no `source_providers`).
- **Evidence Delta disabled:** per Wayfinder 91 Q3, benchmark scores immutable gap-fill only; no Evidence/Benchmark Delta rebuild.

### 3. Gate location — where each check lives (slim v2)

- **Store (`model_info_store.py`):** `is_stale(last_updated, 14)`, `get_if_fresh(key, 14)`, `merge_records`, `aggregate_pricing`. No `is_accurate_enough`/`should_cache` at store layer (gate at pipeline before put).
- **build_all selective reuse loop (`build_all.py`):** collects `live_keys` from all keep lists, does `get_if_fresh` + Rank 1–3 checks, re-averages pricing when stale, otherwise copies verbatim via in-pipeline early return. GC scans live set, no `source_providers`.
- **backfill (`backfill.py`):** dedupes normalized keys, merges via `merge_records` + `aggregate_pricing`, no legacy gate at store layer (keep[] already filtered at pipeline).
- **discovery (`discovery.py`):** normalizes ids before store lookup; does not decide reuse.

### 4. Thresholds (slim v2 — Pricing TTL only)

- Pricing blended (3:1): re-average when age >14d via `aggregate_pricing` (outlier to per_provider_overrides); no AA-score delta or benchmark delta thresholds in slim v2 (benchmarks immutable gap-fill).
- Evidence Delta disabled per Wayfinder 91.

### 5. Telemetry

`build_all` logs per-provider counts: `discovered N | reused R | rebuilt B (new X, delta Y, ttl Z, identity W) | gc G`, plus allowlist/catalog source and store size.

## Considered Options

- File-level TTL kept alongside per-record (rejected — masks per-model staleness, ADR 0006 already removed).
- Per-provider variable TTL (e.g. 7d for fast-moving routers, 30d for stable) — deferred; one global 14d plus forced-stale overrides is simpler and matches current store homogeneity.
- Keeping removed ids forever (rejected — store bloat; 14d GC with provider-share check bounds growth).
- Pricing delta as separate top-rank signal (rejected — logically part of evidence delta; unification avoids double-counting).

## Consequences (slim v2)

- `model_info_store.py`: slim schema, 14d TTL via `last_updated`, pricing re-average, GC via live-set scan.
- `build_all.py`: selective reuse + backfill + GC + telemetry as implemented in #97.
- `backfill.py`: union-max + aggregate_pricing for slim store.
- Migration `scripts/migrate_store_v2.py` already executed; store size ~60% smaller.ery and reports reuse/rebuild stats.
- No migration: existing store records keep their `last_updated`; identity-bad keys (UUID) are already non-cacheable via gate and will age out / be purged on next build.

## References

- #80 Wayfinder map, #81 audit, #82 coverage gaps, #83 Accurate-Enough Gate (ADR 0006), #84 Cloudflare identity fix
- `src/llm_discovery/model_info_store.py`, `backfill.py`, `build_all.py`, `discovery.py`