# Research #137 — Judge query seam over Hybrid SQLite (Hybrid: JSON canonical + derived cache.db)

**Issue:** [#137 Design judge_llm query seam over the chosen store](https://github.com/SoongGuanLeong/llm-discovery/issues/137) — child of Wayfinder map [#132](https://github.com/SoongGuanLeong/llm-discovery/issues/132)
**Depends on:** [#134 Evaluate SQLite consolidation](https://github.com/SoongGuanLeong/llm-discovery/issues/134) (Keep JSON + Hybrid derived only if FTS needed)
**User recommendation adopted:** Keep `data/model_info_store.json` as canonical Source of Truth; generate `data/derived/cache.db` SQLite from it for DBeaver/SQL inspection. Do not wholesale-migrate. Shim aliases `flash`/`max`/`contributor_free` remain separate from Bifrost provider model list.
**Date:** 2026-09-06

---

## 1. Decision

**Hybrid SQLite — JSON canonical, SQLite derived.**

- Canonical write path: `data/model_info_store.json` slim v2 (`ModelInfoStore` + `_atomic_write_json`) stays the only durable store consulted for TTL reuse. Git-diffable, atomic, proven per ADRs 0005–0007.
- Derived read path: `data/derived/cache.db` (SQLite) is regenerated at the tail of `build_all` after backfill + GC, from the just-written JSON. It is gitignored, WAL-enabled, regenerable, and intended for ad-hoc SQL / DBeaver inspection and optional future FTS. It is not the source of truth.
- `config/providers.yaml` remains the sole user-touched file.
- Shim aliases (`flash`/`max`/`contributor_free` in `shim_map.json`) are Bifrost routing tiers, not a provider model list. `src/llm_discovery/bifrost/generator.py:group_keeps_by_tier` + `shim.py:pick_model_for_tier` keep them separate from `config.json` providers.

This keeps migration risk near zero while delivering the DBeaver/SQL convenience the recommendation asks for. Performance win stays where #135 measured it: warm TTL + parallel providers + `max_workers` 4→8 (3–5× warm), not JSON→SQLite (saves <1%).

---

## 2. Seam spec

### 2.1 Query API — what judge / pipeline asks

Judge does not query the store directly (see #134 §2a). The pipeline gates whether to call the judge at all:

```
# pipeline.py — before Judge.evaluate
key = normalize_store_key(model_id)                 # model_info_store.py:47-93
rec = store.get(key)                               # today: dict.get; hybrid: see below
if rec and is_strong(rec) and not is_stale(rec._meta.last_updated, 14):
    keep = build_cached_keep_record(rec)           # no LLM
else:
    keep = evaluate_model(...)                     # EvidenceCollector + resolve + Judge + Gate
```

**Hybrid query seam (backward compatible):**

- Primary: `ModelInfoStore.get(key)` continues to serve TTL decisions from the in-memory JSON dict (O(1), ~55 KB parse, microseconds). No code change for correctness.
- Optional read-through for analytics: `DerivedCache.query(key)` → `SELECT benchmarks, pricing, last_updated FROM models WHERE key = ?` on `data/derived/cache.db`. If the DB is missing/stale, fall back to JSON. This path is opt-in for tooling/DBeaver, not for the hot reuse gate.
- DBeaver/SQL use: `SELECT key, json_extract(pricing,'$.input'), json_extract(pricing,'$.output'), last_updated FROM models WHERE last_updated < date('now','-14 days')` etc. No pipeline change needed.

`normalize_store_key` stays in Python; SQL never normalizes.

### 2.2 Write path — who inserts, atomicity

- Writer: `pipeline.py` + `ModelInfoStore.put(key, merged)` where `merged = merge_records(cached, fresh)` + `_atomic_write_json` (mkstemp + fsync + os.replace). Pricing path `_refresh_pricing_if_stale` → `aggregate_pricing` re-average when TTL>14d; benchmarks path `_gap_fill_benchmarks` immutable gap-fill only.
- SQLite is never written by the judge or per-model path. After `backfill` + `gc(live_keys, 14)` in `build_all.py`, a single tail step `rebuild_derived_cache(store)` does:
  ```
  tmp = "data/derived/cache.db.tmp"
  conn = sqlite3.connect(tmp); conn.execute("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000")
  conn.executescript(DDL); conn.executemany("INSERT OR REPLACE INTO models VALUES (?,?,?,?)", rows_from_store)
  conn.commit(); os.replace(tmp, "data/derived/cache.db")
  ```
  Atomic replace avoids WAL/shm contention on the hot path (cf. `data/bifrost/config.db` 27 MB WAL owned by `nobody` under Podman). Store JSON remains the commit artifact; cache.db is ephemeral.

### 2.3 Schema / file layout

```
project-root/
  data/
    model_info_store.json   # canonical, committed ("!" in .gitignore), slim v2
    results/*.yaml          # ephemeral, gitignored
    derived/
      cache.db              # derived SQLite, gitignored, regenerable
      cache.db-wal / -shm   # WAL artifacts, gitignored
```

`.gitignore` additions:
```
data/derived/
!data/model_info_store.json
```

**DDL (SQLite, single table, no joins):**
```sql
CREATE TABLE IF NOT EXISTS models (
  key TEXT PRIMARY KEY,              -- normalize_store_key(model_id)
  benchmarks TEXT NOT NULL,          -- JSON: {aa_intelligence, swe_bench_verified, ...}
  pricing TEXT NOT NULL,             -- JSON: {input, output, currency, per_provider_overrides?}
  last_updated TEXT NOT NULL         -- ISO date from _meta.last_updated
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_models_last_updated ON models(last_updated);
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
```

- `WITHOUT ROWID` — PK is the key, saves one B-tree.
- JSON columns stay JSON text; pricing/benchmark logic stays Python after `SELECT` (SQL `AVG` cannot replicate `aggregate_pricing` outlier logic, and `_gap_fill_benchmarks` is Python dict merge).
- 24–314 rows today; even 10k rows is trivial. No FTS table until evidence is re-added to the store (slim v2 excludes evidence per ADR 0006; evidence lives in Ephemeral Reports + LLM packet).

### 2.4 FTS / indexing

- No FTS now. Evidence not in store; no query needs it.
- If future need is FTS over evidence descriptions, add FTS5 virtual table at that time, which presupposes re-adding evidence to the store — a schema decision, not a storage-engine decision. Derived cache would then add:
  ```sql
  CREATE VIRTUAL TABLE models_fts USING fts5(key, evidence, content='models', content_rowid='rowid');
  ```
  Triggers to keep FTS in sync on `INSERT OR REPLACE`. Not included in this prototype.

### 2.5 Gap-fill mapping — SQL vs file ops

| Invariant | JSON path today | Hybrid derived path |
|-----------|----------------|---------------------|
| Pricing-only update (TTL>14d re-average, else verbatim) | `_refresh_pricing_if_stale(cached, fresh_obs)` → `aggregate_pricing` Python | Same Python call; cache.db `pricing` column is just persisted view, not computed in SQL |
| Benchmarks immutable | `_gap_fill_benchmarks(cached_bm, fresh_bm)` — null→fill, never overwrite | Same Python merge; cache.db `benchmarks` JSON overwritten atomically at tail |
| Strong-only Keeper reuse | `classify_hit` + `get_if_fresh(key,14)` on JSON | Same gate on JSON; cache.db not consulted for gate |
| Hallucinated-evidence guard | `gate.py:is_accurate_enough` + `evidence_collector` before `put` | Same; cache.db inherits only Keeps that passed gate |
| Per-provider overrides | `aggregate_pricing` outlier → `per_provider_overrides` | Same Python logic; overrides stored inside `pricing` JSON |

Storage engine does not change policy. SQL is persistence/display, not policy.

### 2.6 Bifrost / shim separation

- `flash`/`max`/`contributor_free` are shim aliases in `data/bifrost/shim_map.json`, populated by `generator.group_keeps_by_tier` / `shim_map filtering` and consumed by `bifrost/shim.py:pick_model_for_tier` (keep-all across tiers, dedup within). They are not Bifrost provider models.
- Bifrost `config.json` providers block + `config.db` are the real provider model list (`/v1/models`). Fix for #133 (env var resolution + health-filter + stale config.db) is orthogonal to cache.db.
- `cache.db` does not feed Bifrost; `generate-bifrost-config.py` continues to read `data/results/*.yaml` + store.

---

## 3. Build integration (pseudocode diff)

```python
# src/llm_discovery/build_all.py — tail, after gc(live_keys, 14) and telemetry
from llm_discovery.derived_cache import rebuild_derived_cache  # new tiny module
try:
    rebuild_derived_cache(store, path="data/derived/cache.db")
except Exception as e:
    log.warning(f"derived cache rebuild skipped: {e}")  # never fail build
```

`derived_cache.py` (~60 lines): `DDL` above + `rows_from_store = [(k, json.dumps(v.benchmarks), json.dumps(v.pricing), v._meta.last_updated) for k,v in store.all().items()]` + atomic replace. No changes to `judge.py`, `gate.py`, `pipeline.classify_hit`, or provider discovery parallelism.

---

## 4. Operational notes

- **Backup/restore:** `model_info_store.json` is the backup (git history). `cache.db` is disposable: delete and rebuild from JSON. No `VACUUM` cadence needed at this size; if FTS added later, `VACUUM` on demand.
- **Podman:** `cache.db` is host-side tooling DB, not mounted into Bifrost container. No `nobody` ownership or WAL contention (unlike `data/bifrost/config.db`).
- **Cost/perf:** No repeated LLM/API calls saved by cache.db itself — saving comes from TTL hit rate + parallel providers + max_workers (see #135). Cache.db is convenience/observability (DBeaver, `SELECT` audits) at ~milliseconds cost.
- **Verification:** `sqlite3 data/derived/cache.db "SELECT count(*) FROM models"` should equal `jq '.models | length' data/model_info_store.json`; spot-check a key round-trips `benchmarks`/`pricing` JSON identical.

---

## 5. Alternatives considered (see #134)

- Keep JSON only — also valid, zero new file, but no DBeaver/SQL convenience.
- Hybrid `judge_index.db` — same idea, narrower name; adopted here as `cache.db` per recommendation (broader tooling use, not judge-only).
- Wholesale SQLite / SQLite+FTS / DuckDB — not recommended (2–4d migration, WAL contention, binary git bloat, no speedup; see #134 §5).

---

## 6. Hand-off checklist for implementer

- [ ] Add `data/derived/` to `.gitignore`, keep `!data/model_info_store.json`.
- [ ] Create `src/llm_discovery/derived_cache.py` with `rebuild_derived_cache` as above.
- [ ] Wire tail call in `build_all.py` (warn-only on failure).
- [ ] Verify DBeaver: open `data/derived/cache.db`, run sample queries from §2.1.
- [ ] Leave `config/providers.yaml` as sole user-touched file — no new user-touched SQLite.
- [ ] Keep shim aliases separate — no change to `generator.py` / `shim.py` provider list.

