# ADR 0005: Model-info store persistence, invalidation, concurrency, versioning

## Status
Accepted — Issue #68 (part of #63 Wayfinder)

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

### 2. Invalidation — never expire by default, optional TTL, stronger-evidence merge
- Default TTL = None (never stale). `is_stale(last_updated, ttl_days=None)` returns False when ttl None.
- Rationale: prompt notes data hardly changes, new model name = new record (key collision rare). Time-based expiry would churn strong evidence needlessly and waste judge tokens.
- Overwrite semantics: same key with new stronger evidence wins via `merge_records` per #64 (ordinal strong>moderate, tie confidence>newer). Weaker incoming fills gaps only. Not blind overwrite.
- Optional staleness: caller may pass ttl_days=90 to treat old strong as miss (`get_if_fresh(ttl_days)`). Prototype stub used 90d; not default. Supported for future drift detection but not enforced.
- Scripts/query.py and tests: `store.get(model_id)` never stale; `store.get_if_fresh(model_id, ttl_days=90)` when freshness required.

### 3. Concurrency — fcntl lock + atomic tmp+rename, sequential providers, pooled models
- `discover_all_providers` runs providers sequentially (no cross-provider ThreadPool today). Within a provider, `discover_provider` uses ThreadPoolExecutor(max_workers=4) for `evaluate_model` — concurrent puts to same store file possible if store shared.
- Guard: atomic write via temp file in same dir + `os.replace` (POSIX atomic); best-effort `fcntl.flock(LOCK_EX)` on write filehandle where available, fallback no-lock (Linux). No per-provider tmp then merge needed — single file with lock serializes writes; contention low (few puts per provider, strong/moderate only).
- Alternative considered: per-provider tmp JSON then merge at end — more complex, merge duplication with #64 logic, deferred unless true parallel-providers mode added.
- On read, no lock needed (lazy load, in-memory dict after first load).

### 4. Versioning / migration — file-level `version` + per-record `_meta.version`
- File payload `{"version": 1, "models": {key: record}}`. STORE_FILE_VERSION=1. Per-record `StoreMeta.version=1` already (issue #66).
- On load: if file missing → empty; if bare dict without wrapper → legacy compat; if version mismatch → log/warn, keep reading (forward migration bump when schema changes). Future schema change bumps STORE_FILE_VERSION and adds migration branch.
- No migration needed today; version field reserves drift handling without breaking backfill (#69).

### 5. Read path — lazy load, in-memory dict, normalized lookup
- `ModelInfoStore` lazy loads on first `get()/keys()/size()`. In-memory `dict[str, ModelInfoRecord]` for O(1) lookup. `get(provider_model_id)` normalizes via `normalize_store_key` then dict lookup; `get_by_key(store_key)` raw.
- No per-model file I/O, no eager load at import. `load()`/`save()` public for backfill script and tests.
- Cache gate: `put()` refuses weak/none (should_cache), merges via merge_records, then atomic save. `upsert_from_provider_record()` convenience for backfill/pipeline.

## Consequences
- Commit `data/model_info_store.json` as source of truth; backfill (#69) seeds it from 15+ YAMLs via merge rules.
- No periodic TTL churn; old strong evidence reused until stronger contradicts it.
- Parallel model eval within provider safe with lock+atomic; provider-level parallelism future-proofed.
- Version bump path ready; scripts get stable JSON, tools layer stays thin.

## References
- #63 wayfinder map, #66 schema, #64 evidence trust, #65 pricing, #67 seam, #68 this ADR, #69 backfill
