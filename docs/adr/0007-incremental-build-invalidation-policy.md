# ADR 0007: Incremental build invalidation policy (per-record TTL and signals)

## Status
Accepted — Issue #85 grilling (part of #80 Wayfinder), 2026-09-04. Extends ADR 0006 per-record TTL with ranked invalidation signals.

## Context
ADR 0006 replaced file-level `is_stale(evaluated_at, 14)` with per-record `is_stale(StoreMeta.last_updated, 14)` and made `data/model_info_store.json` the Source of Truth. File-level TTL hid per-model staleness and model-list churn: a 17-file directory with mixed-age records would skip an entire file while 65 UUID Cloudflare ids stayed unfixable and new true-free ids (stepfun, qwen dot variants) never triggered rebuild. Research #81 showed weak/moderate keeps, hallucinated evidence, and UUID file-shapes; #82 confirmed dot-hyphen benchmark split and 40.5% AA match. Wayfinder #80 needs a safe reuse gate that reuses fresh strong records without LLM calls yet rebuilds on identity fixes, list churn, and evidence drift.

Open questions in #85: rank the four signal families (time TTL, model-list diff, evidence/benchmark delta, identity fix), set default TTL and per-signal overrides, and state where the gate lives (backfill `is_stale` vs `build_all` vs `store.is_stale`).

## Decision

### 1. Signal ranking (highest → lowest)

1. **Identity Integrity** — UUID-shaped `model_id` or hallucinated denylist (`tokenmix.ai`, `callsphere.ai`, `benchlm`) or missing human name. Never cacheable; always stale. Overrides all other signals. Rank 1 because a bad key poisons dedup and all downstream deltas.
2. **Model-List Churn (new ids)** — Discovered `normalize_store_key(id)` not in store → mandatory build. No TTL; new key is unconditionally stale.
3. **Model-List Churn (removed ids)** — Stored key with `source_providers` containing this provider but absent from current discovery set. Not rebuilt; retained for 14d then GC if still absent across next build and not shared by another provider. Ranked below new-id because it is a retention/GC decision, not a build trigger.
4. **Evidence / Benchmark Delta** — Forces rebuild even when TTL fresh:
   - `aa_score` delta ≥ 2.0 points, or `coding_score` null→non-null, or `evidence_level` would change under gate
   - `benchmark_coverage` gains a new KEY_SIGNAL (aa_intelligence, swe_bench_verified, livecodebench, humaneval) or existing signal score changes ≥ 10%
   - Pricing delta (see #5) — treated as subset of evidence delta for gate purposes
   Ranked below list churn because list churn is structural (presence), while delta is value drift.
5. **Time TTL** — Baseline freshness. Keeper with `StoreMeta.last_updated` age ≤ 14 days may be reused if none of 1–4 fire; age > 14 days is stale.
6. **Catalog staleness** — `data/artificial_analysis_models.json` / `data/models_dev_catalog.json` `fetched_at` > 14 days suggests evidence may be stale but does not alone force per-record rebuild; triggers catalog refresh before delta checks.

### 2. TTL — default and overrides

- **Default TTL:** `DEFAULT_TTL_DAYS = 14`, per-record via `StoreMeta.last_updated`, checked by `ModelInfoStore.get_if_fresh(key, ttl_days=14)`.
- **Overrides (TTL = 0, always rebuild):** identity failure (Rank 1), new id (Rank 2).
- **Overrides (ignore TTL, compare fresh catalog/packet vs stored):** Rank 4 deltas. If delta threshold crossed, record is stale regardless of age.
- **Retention TTL for removed ids (Rank 3):** keep stored record 14 days after last sighting; purge on next build if still absent and `source_providers` would become empty. If another provider still sources the key, keep.
- **No per-signal variable TTL:** one default (14d) plus the above forced-stale overrides. Keeps the gate predictable; per-provider tuning is a future extension, not part of this ADR.

### 3. Gate location — where each check lives

- **Store (`model_info_store.py`):** `is_stale(last_updated, 14)`, `is_accurate_enough(record)` (ADR 0006 floors), `get_if_fresh(key, ttl_days=14)`. Pure per-record, no file concept.
- **build_all selective rebuild loop (`build_all.py`):** sole caller of `get_if_fresh` plus Rank 1–4 checks before reuse. Pseudocode:
  ```
  store = ModelInfoStore(path)
  discovered = {normalize_store_key(m["id"]): m for m in discover_fn(provider)}
  for key, meta in discovered.items():
      rec = store.get_by_key(key)
      if rec is None: build(key)  # Rank 2
      elif is_identity_bad(meta["id"]): build(key)  # Rank 1
      elif evidence_delta(rec, fresh_catalog_lookup(key)): build(key)  # Rank 4
      elif pricing_delta(rec.pricing, fresh_pricing(key)): build(key)  # Rank 4
      elif store.get_if_fresh(key, 14) is None: build(key)  # Rank 5
      else: reuse(rec)  # skip LLM, gap-fill benchmarks/pricing
  # Rank 3: after loop, mark stored keys for this provider not in discovered for GC
  ```
- **backfill (`backfill.py`):** no TTL gate. Applies only `should_cache` / `is_accurate_enough` per keep record and merges via `merge_records` + `aggregate_pricing`. File `evaluated_at` no longer gates. Historical file-level `is_stale` check removed (ADR 0006 migration).
- **discovery (`discovery.py`):** normalizes ids (including `_normalize_cloudflare_models` from #84) before store lookup; does not decide reuse.

### 4. Thresholds for Rank 4 deltas

- AA score: absolute delta ≥ 2.0
- Pricing blended (3:1): absolute delta ≥ 0.05 ($/1M) or relative ≥ 10%, whichever is smaller in absolute terms; either triggers stale
- Benchmark: new KEY_SIGNAL appears, or existing signal score changes ≥ 10%, or `benchmark_coverage` crosses 0.25 floor
- Evidence: stored `aa_model_id` null vs fresh non-null with URL, or hallucinated-URL denylist state flips
- All deltas compare fresh catalog/packet fetch (AA + models_dev + local benchmarks) against stored `ModelInfoRecord` snapshot; fresh fetch is cheap (local JSON), not an LLM call.

### 5. Telemetry

`build_all` logs per-provider counts: `discovered N | reused R | rebuilt B (new X, delta Y, ttl Z, identity W) | gc G`, plus allowlist/catalog source and store size.

## Considered Options

- File-level TTL kept alongside per-record (rejected — masks per-model staleness, ADR 0006 already removed).
- Per-provider variable TTL (e.g. 7d for fast-moving routers, 30d for stable) — deferred; one global 14d plus forced-stale overrides is simpler and matches current store homogeneity.
- Keeping removed ids forever (rejected — store bloat; 14d GC with provider-share check bounds growth).
- Pricing delta as separate top-rank signal (rejected — logically part of evidence delta; unification avoids double-counting).

## Consequences

- `model_info_store.py`: add helpers `is_identity_stale(model_id)`, `pricing_delta_exceeds(a,b)`, `evidence_delta_exceeds(rec, fresh)`; keep `is_stale` per-record.
- `build_all.py`: implement selective reuse loop as above; no file-level skip.
- `backfill.py`: confirm file-level skip removal (already per ADR 0006); no new TTL logic.
- Follow-up #86 (Prototype: Intelligent build_all) implements the loop thinly with mocked discovery and reports reuse/rebuild stats.
- No migration: existing store records keep their `last_updated`; identity-bad keys (UUID) are already non-cacheable via gate and will age out / be purged on next build.

## References

- #80 Wayfinder map, #81 audit, #82 coverage gaps, #83 Accurate-Enough Gate (ADR 0006), #84 Cloudflare identity fix
- `src/llm_discovery/model_info_store.py`, `backfill.py`, `build_all.py`, `discovery.py`
