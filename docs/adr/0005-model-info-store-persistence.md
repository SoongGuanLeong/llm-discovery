# ADR 0005: Model-info store persistence, invalidation, concurrency, versioning

## Status
Accepted — Issue #68 (part of #63 Wayfinder) • Updated by #95/#98 to slim v2 (version 2)

## Context
Store schema locked in #66 (key = normalize_store_key, field inclusion matrix, merge per #64, pricing per #65). Open fog: where file lives, TTL vs never-expire, how parallel discover handles concurrent writes, version migration, read path.

Candidates:
- Location: data/model_cache.yaml vs data/cache/model_info.json vs SQLite; gitignored local cache vs committed artifact; format JSON vs YAML vs SQLite
- Invalidation: 90d TTL vs ∞ (never expire) vs evidence-strength overwrite
- Concurrency: file lock vs atomic write vs per-provider tmp then merge
- Versioning: file header version vs per-record version vs no version
- Read: eager vs lazy vs per-model file lookup

## Decision

### 1. Location & format — `data/model_info_store.json`, JSON, committed
- Path `data/model_info_store.json` (RECOMMENDED_STORE_PATH). JSON over YAML/SQLite.
- Rationale: JSON precedent = data/benchmarks.json, artificial_analysis_models.json; atomic write easier than YAML (no quoting drift), no SQL migration overhead for O(1k) keys, machine-managed store benefits from committed snapshot for audit/repro (vs gitignored cache hides drift). Dotfile exception added to .gitignore (`!data/model_info_store.json`) while data/ stays ignored.
- Alternatives rejected: `data/model_cache.yaml` gitignored — hides drift; `data/cache/model_info.json` extra dir no value; SQLite — overkill, tooling burden; YAML committed — quoting fragile, harder atomic.
- Access: `ModelInfoStore(path=None)` defaults to RECOMMENDED_STORE_PATH; tests/scripts pass tmp Path; pipeline/discover inject store at startup.

### 2. Invalidation — per-record 14-day TTL, pricing re-average, benchmarks immutable (slim v2, #91/#95)
- Default TTL = 14 days via `StoreMeta.last_updated` (DEFAULT_TTL_DAYS=14). `is_stale(last_updated, 14)` true when age >14d.
- Benchmarks immutable gap-fill only; pricing refreshes every 14d via `aggregate_pricing` re-average (no LLM) per Wayfinder 91. Evidence Delta disabled.
- Merge: `merge_records` does benchmarks union-max + pricing re-avg + freshness min/max; slim record holds only benchmarks, pricing, _meta.
- Optional staleness: `store.get_if_fresh(key, 14)` is sole reuse check in build_all.

### 3. Concurrency — fcntl lock + atomic tmp+rename, sequential providers, pooled models
- `discover_all_providers` runs providers sequentially (no cross-provider ThreadPool today). Within a provider, `discover_provider` uses ThreadPoolExecutor(max_workers=4) for `evaluate_model` — concurrent puts to same store file possible if store shared.
- Guard: atomic write via temp file in same dir + `os.replace` (POSIX atomic); best-effort `fcntl.flock(LOCK_EX)` on write filehandle where available, fallback no-lock (Linux). No per-provider tmp then merge needed — single file with lock serializes writes; contention low (few puts per provider, strong/moderate only).
- Alternative considered: per-provider tmp JSON then merge at end — more complex, merge duplication with #64 logic, deferred unless true parallel-providers mode added.
- On read, no lock needed (lazy load, in-memory dict after first load).

### 4. Versioning / migration — file-level `version` + per-record `_meta.version` (v2 slim)
- File payload `{"version": 2, "models": {key: {benchmarks, pricing, _meta}}}`. STORE_FILE_VERSION=2 (slim). Per-record `_meta={first_seen, last_updated, version:2}` only. Dropped vs v1: aa_model_id, aa_score, coding_score, evidence, evidence_level, confidence, tier, _meta.source_providers/source_evidence_levels.
- Compat read: v1 files (with dropped fields) still load via `ModelInfoRecord.from_dict` ignoring dropped keys; migration script `scripts/migrate_store_v2.py` did one-shot purge+project with .bak and atomic rename.
- On load: if file missing → empty; if bare dict without wrapper → legacy compat; version mismatch handled. No further migration needed beyond v2.

### 5. Read path — lazy load, in-memory dict, normalized lookup
- `ModelInfoStore` lazy loads on first `get()/keys()/size()`. In-memory `dict[str, ModelInfoRecord]` for O(1) lookup. `get(provider_model_id)` normalizes via `normalize_store_key` then dict lookup; `get_by_key(store_key)` raw.
- No per-model file I/O, no eager load at import. `load()`/`save()` public for backfill script and tests.
- Cache gate: slim v2 stores only Keepers; `put()` merges via `merge_records` (benchmarks union-max + pricing re-avg) then atomic save. No `should_cache` gate at store layer — gate at pipeline/backfill keeps only Keepers before put.

## Consequences (updated for slim v2)
- Commit `data/model_info_store.json` v2 as slim Source of Truth (benchmarks+pricing+_meta only, ~60% smaller); backfill merges via union-max + re-avg.
- TTL 14d per-record via last_updated; pricing TTL triggers re-average, not LLM rebuild; benchmarks immutable.
- Parallel model eval safe with lock+atomic; GC share-aware via live yaml scan (no source_providers).
- Version 2 header; compat read for v1 retains migration replay ability.

## References
- #63 wayfinder map, #66 schema, #64 evidence trust, #65 pricing, #67 seam, #68 this ADR, #69 backfill