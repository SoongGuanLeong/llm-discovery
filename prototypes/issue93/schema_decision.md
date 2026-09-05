# Schema Decision #93 — Slim Source of Truth for cross-provider cache + Bifrost routing

Status: Proposed (Wayfinder #88, Task #93 — decision not implementation)
Date: 2026-09-05
Context: #89 #90 #91 #92, ADRs 0005-0007, CONTEXT.md

## 1. Question restated

How to preserve original provider `model_id` per provider so Bifrost can route `POST /chat/completions {"model": raw_id}` while store reuses Keeper evaluation across providers via normalized key?

Options debated: (a) store map `provider -> raw_id`, (b) canonical id + aliases list, (c) both, (d) no map.

#91 narrowed: user chose **store = benchmarks+pricing only, yaml = raw id**. This doc locks shape, merge rule, GC, migration.

## 2. Decision — slim store, no alias map

### 2.1 Shape — Store v2 (slim)

```json
{
  "version": 2,
  "models": {
    "<normalize_store_key>": {
      "benchmarks": { "scores": {...}, "raw_benchmarks": [...], "benchmark_coverage": 0.25 },
      "pricing": { "blended": 1.2, "input": 0.8, "output": 2.0, "per_provider_overrides": {} },
      "_meta": { "first_seen": "ISO8601", "last_updated": "ISO8601", "version": 2 }
    }
  }
}
```

**Kept:** `benchmarks` (BenchmarkSnapshot), `pricing` (PricingSnapshot), `_meta.first_seen/last_updated/version`.
**Dropped vs v1:** `aa_model_id`, `aa_score`, `coding_score`, `evidence`, `evidence_level`, `confidence`, `tier`, `_meta.source_providers`, `_meta.source_evidence_levels`.
All dropped fields remain in Ephemeral Report yaml per provider (see 2.2) — they are not needed for TTL reuse after #91 (benchmarks immutable, pricing TTL only).

Rationale:
- #90 Bifrost contract = raw `model_id` + provider pair. Provider known from routing config (`config/providers.yaml` base_url/secret); raw id lives in yaml where it was always kept. Store never needed to map.
- #91 hit policy = strong-only + benchmarks immutable + pricing TTL 14d. Evidence/tier/confidence not consulted for reuse — hit is existence of slim record + TTL check. `aa_model_id`/`coding_score` floors already enforced at write time (only Keepers written).
- Removing `source_providers` decouples store from provider binding; GC instead scans live yaml set (2.4).

Rejected:
- `provider->raw_id` map in store — duplicates yaml, drift risk, bloats every key xN providers.
- `aliases: [raw_ids]` — same duplication, normalization already collapses; raw ids belong in yaml where Bifrost already reads.

### 2.2 Yaml (Ephemeral Report) — stays source for Bifrost

- Path `data/results/<provider>.yaml` unchanged, shape per `ProviderBatchWriter._to_record` (model_id raw, aa_model_id, benchmarks, pricing, evidence etc.). Still gitignored but regenerated every `build_all` before Bifrost reads.
- Bifrost routing reads yaml (or build_all in-memory result) per provider, not store. Store key never used for routing.
- Normalization of stored ids: store keys are `normalize_store_key(raw)` (lowercased, prefix/free stripped, stepfun normalized). Raw ids in yaml kept verbatim (exact case/prefix/free) per #90.

### 2.3 Merge rule on `backfill` / `store.put`

Given slim store, merge per key:

- Benchmarks: **gap-fill union** (immutable per #91). Copy incoming `scores[k]` only when existing `scores[k]` is missing/null. Never overwrite existing score even if fresh differs (no Evidence Delta rebuild). New keys in incoming added. `raw_benchmarks` union dedup by string. `benchmark_coverage` = max existing/incoming.
- Pricing: **re-average when multiple observations for same key**. `backfill` collects pricing_groups per normalized key across yaml files, calls `aggregate_pricing` (outlier-aware mean, per_provider_overrides). Single observation → verbatim. `store.put` from pipeline seam does verbatim copy unless stale (pricing TTL 14d re-avg via catalog observations — see #92 stub).
- _meta: `first_seen` = min(existing, incoming), `last_updated` = now (ISO8601) on any merge, `version`=2.
- Gate before put: only Keepers (strong gate via `is_accurate_enough`) may enter store. Moderate/weak/router `kilo-auto/free` never written (even if decision keep). `should_cache` check retained.

### 2.4 GC when provider drops model

V1 GC used `_meta.source_providers` to check share across providers. V2 loses that field — new rule scans live yaml set:

- On each `build_all` after yaml writes + backfill, compute `live_keys = {normalize_store_key(r.model_id) for each provider yaml keep[]}`.
- For each store key: if `key not in live_keys` and `is_stale(_meta.last_updated, 14)` → delete. If shared by another provider, `key in live_keys` stays true → retain.
- No background GC thread; piggy-backed on build_all. Keeps 14d retention (ADR 0007 Rank 3) without provider binding in store.

Concurrency: `ModelInfoStore.save` already atomic tmp+rename + fcntl; build_all runs providers sequentially then single-threaded backfill/GC — no cross-provider write race.

### 2.5 Migration existing store (v1 -> v2)

Current `data/model_info_store.json` is v1 (evidence/_meta/source_providers present, includes moderate records e.g. agnes-2.5-flash).

Migration script (one-shot, idempotent):

1. Load v1 file (`version` 1 wrapper or bare dict).
2. For each key, run Gate: keep only if `evidence_level==strong` and `is_accurate_enough(record)` true (purges moderate/weak, cs_null, no_pricing not free, uuid, hallucinated). This is ADR 0006 hard purge already pending.
3. Project to slim: `{benchmarks: record.benchmarks.to_dict(), pricing: record.pricing.to_dict(), _meta: {first_seen, last_updated, version:2}}`.
4. Write to same path with `{version:2, models: slim}` via atomic replace, keep `.bak`.
5. Bump `STORE_FILE_VERSION=2` in `model_info_store.py` and update `ModelInfoRecord.from_dict` compat (read v1 fallback).

Size impact: ~60% smaller file (evidence/tier removed), audit trail stays in yaml + git history.

## 3. Consequences

- `model_info_store.py`: add v2 schema, narrow `ModelInfoRecord` to slim fields (or keep compatibility shim that ignores dropped fields on read), remove `source_providers` logic, update GC to yaml-scan helper.
- `backfill.py`: already gap-fill + aggregate_pricing; no change except dropping evidence fields from output.
- `pipeline_cache_prototype.py` / #92 stub: already slim-aware (benchmarks+pricing only).
- No code change in Bifrost — it continues reading yaml.
- Follow-up ticket: implement migration + file version bump (execution, not wayfinding).

## 4. Links

- Prototype seam: `prototypes/issue92/cache_seam_stub.py` (cache check before LLM, reuses slim benchmarks+pricing)
- ADRs: 0005 (persistence), 0006 (gate+store primary), 0007 (invalidation precedence now Identity->Churn->Pricing TTL only)
- Store sample: `data/model_info_store.json` v1, yaml sample `data/results/kilo_ai.yaml`

## 5. Open risk

If yaml ever stops being regenerated (build_all skipped), Bifrost would lack raw ids — slim store cannot rescue. Accept: build_all is sole writer of yaml; Bifrost infra must depend on successful build.
