# ADR 0009: Per-evidence TTL split + per-model staleness (supersedes ADR 0007)

## Status
Accepted — Issue #221 (spec #218), 2026-09-16. Supersedes ADR 0007 incremental build invalidation policy.

## Context
ADR 0007 defined single Record TTL (14d per ADR, 28d in code) + global catalog_stale flag that re-evaluates every Keeper when any catalog ages. This causes broad rebuilds for unchanged models. Benchmarks immutable gap-fill was disabled but still re-derived on TTL expiry. Pricing TTL forced full re-evaluation. Issue #221 splits per-type horizons.

## Decision

### 1. Per-type horizons (issue #221)
- **Pricing:** 3-7d via `aggregate_pricing` (PRICING_TTL_DAYS=7, PRICING_TTL_MIN=3). Re-average when stale, no LLM.
- **Catalog row:** 7-14d per-model diff (CATALOG_ROW_TTL_DAYS=14, MIN=7). Per-model check, not global any-catalog-old flag.
- **Benchmarks:** 60-90d immutable gap-fill (BENCHMARK_TTL_DAYS=90, MIN=60). Not re-derived each TTL expiry; fill missing only.
- **Judgement:** until evidence_hash changes. Stores `judge.evidence_hash = sha256(AA score + bench scores + pricing blended + claim URLs)[:16]`. On cache hit if evidence_hash unchanged and pricing within TTL, reuse without LLM even if catalog fetched_at >14d.

### 2. Global flag removed
- `build_all.catalog_stale` global flag removed; `catalog_stale_days` refresh remains warn-only but does not force re-eval of all Keeps.
- Per-model staleness via `is_pricing_stale`, `is_catalog_row_stale`, `is_benchmark_stale`, `is_judgement_stale` in `model_info_store.py`.
- `EvaluatorCoordinator.catalog_stale` kept for compat but ignored; judgement reuse now checks evidence_hash.

### 3. Store shape
- `JudgeSnapshot.evidence_hash` added, persisted via `to_dict`/`from_dict`, computed in `from_provider_record`.
- `compute_evidence_hash` hashes sorted payload; `is_pricing_stale` uses PRICING_TTL_DAYS=7.
- Benchmark gap-fill respects 60-90d TTL, not re-derived on pricing stale.

### 4. Telemetry
- Pricing re-average vs judgement reuse distinguished; catalog row diff prevents broad rebuild.

## Consequences
- Pricing re-average triggers at 7d while judgement reused when evidence_hash unchanged.
- No global rebuild on catalog fetched_at >14d; unchanged models reused with frozen clock.
- Benchmarks not re-derived each TTL expiry.
- ADR 0007 signal ranking rank 4-6 replaced by per-evidence horizons.

## References
- Issue #221, spec #218, ADR 0007, ADR 0008 (thresholds frozen)
- `src/llm_discovery/model_info_store.py` `compute_evidence_hash`, `src/llm_discovery/evaluator.py`, `src/llm_discovery/build_all.py`
