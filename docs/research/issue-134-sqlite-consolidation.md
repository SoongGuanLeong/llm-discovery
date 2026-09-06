# Research #134 — SQLite consolidation for data/ — is it the right move?

**Issue:** [#134 Evaluate SQLite consolidation for data/](https://github.com/SoongGuanLeong/llm-discovery/issues/134) — child of Wayfinder map [#132](https://github.com/SoongGuanLeong/llm-discovery/issues/132) (retry; previous attempt lacked depth)
**Status:** research — primary-source audit, no build
**Date:** 2026-09-06
**Sources:** `CONTEXT.md`, `docs/research/issue-71-data-files-formation.md`, `docs/adr/0005-model-info-store-persistence.md`, `docs/adr/0006-accurate-enough-gate-and-store-source-of-truth.md`, `docs/adr/0007-incremental-build-invalidation-policy.md`, `src/llm_discovery/model_info_store.py`, `src/llm_discovery/build_all.py`, `src/llm_discovery/pipeline.py`, `src/llm_discovery/judge.py`, `src/llm_discovery/catalogs.py`, `src/llm_discovery/backfill.py`, `src/llm_discovery/gate.py`, `src/llm_discovery/evidence_collector.py`, `config/providers.yaml`, `data/model_info_store.json`, `data/results/*.yaml` (23 files, 314 keeps), `data/artificial_analysis_models.json`, `data/models_dev_catalog.json`, `data/bifrost/config.json`, `data/bifrost/shim_map.json`, `data/bifrost/config.db` + `logs.db`, `.gitignore`

> Wayfinder #132 asks whether moving everything in `data/` except `config/providers.yaml` into SQLite — and letting `judge_llm` query it — is the right way to achieve the stated semantics (only update pricing; if info missing, LLM searches and adds new entry), given `build_all` is very slow. This research compares SQLite against keeping JSON with better cache/WAL, SQLite+FTS, DuckDB, and hybrid, against the primary-source reality of judge query patterns, bottleneck profile, and operational constraints.

---

## 1. What exists today — verified sizes, grains, and ownership

| Path | Format | Size on disk (2026-09-06) | Grain | Committed? | Source |
|------|--------|---------------------------|-------|------------|--------|
| `data/model_info_store.json` | JSON slim v2 | 55,740 B — 24 models, avg 1,520 B/entry | normalized key `normalize_store_key(model_id)` `model_info_store.py:47-93` | **yes** — `!data/model_info_store.json` `.gitignore:22` | `model_info_store.py:799` `STORE_FILE_VERSION=2`, payload `{version:2, models:{key:{benchmarks,pricing,_meta}}}` `model_info_store.py:903-905` |
| `data/results/*.yaml` | YAML | 1,118,726 B across 23 files, 314 keeps total | per-provider `{provider, evaluated_at, keep:[], drop_llm:[], error:[]}` `results.py:193-199` | gitignored (`data/` `.gitignore:21`) | `pipeline.py:490-522` `discover_all_providers` → `ProviderBatchWriter.write` `results.py:155-203` |
| `data/artificial_analysis_models.json` | JSON snapshot | 759,906 B — 631 models `catalogs.py:10` | whole catalog | gitignored | `refresh.py:21` `GET https://artificialanalysis.ai/api/v2/data/llms/models` |
| `data/models_dev_catalog.json` | JSON snapshot | 8,701,215 B — 364 models, 212 providers `catalogs.py:51-52` | whole catalog | gitignored | `refresh.py:22` `GET https://models.dev/catalog.json` |
| `data/bifrost/config.json` | JSON (generated) | 20,793 B — 20 providers, 314 model mappings | derived routing table | generated (not committed) | `bifrost/generator.py` + `scripts/generate-bifrost-config.py` |
| `data/bifrost/shim_map.json` | JSON | 8,660 B | shim aliases (flash/max/contributor_free) | generated | same generator |
| `data/bifrost/config.db` | SQLite WAL | 27,467,776 B + `config.db-wal` 2,006,472 B + `config.db-shm` 32,768 B | Bifrost gateway runtime | Podman volume (`nobody:nogroup` ownership) | Bifrost binary manages; `config_store.type=sqlite` `config.json: "config_store"` |
| `data/bifrost/logs.db` | SQLite WAL | 319,488 B + WAL 498,552 B | request logs | same | Bifrost |

Key observation: `data/` is already split by ownership. The 27 MB `config.db` is Bifrost's own runtime state (WAL, owned by `nobody`), not llm-discovery's discovery cache. The discovery pipeline's durable state is only the 55 KB store + ephemeral YAMLs + optional catalog snapshots.

---

## 2. Judge query pattern — does SQL help vs file reads?

### 2a. How judge actually queries today

`src/llm_discovery/judge.py:1-54` is a thin adapter: `Judge.evaluate(provider, model, packet, cache, profile)` builds a `ModelEvaluationRequest` from `packet` + `profile` (`build_benchmark_profile` `evidence_collector` + `benchmarks.py`) and delegates to `evaluator.evaluate(request, packet)` (LLM call via `agnes-2.0-flash` at `https://apihub.agnes-ai.com/v1` `config/providers.yaml: judge_llm`). The store is **not** queried by the judge. The judge queries the LLM; the pipeline decides whether to call the judge at all.

Store lookup happens in `pipeline.py` before judge, for selective reuse:

- `classify_hit(record)` `pipeline.py: classify_hit` — strong-only check (`evidence_level == strong` or slim Keeper existence). Returns `strong_hit`/`miss`.
- `get_if_fresh(key, 14)` `model_info_store.py: get_if_fresh` — `is_stale(_meta.last_updated, 14)` `model_info_store.py:is_stale` gate.
- On hit: `build_cached_keep_record` `pipeline.py: build_cached_keep_record` reuses cached benchmarks/pricing without LLM. Pricing path: `_pricing_is_stale` + `_refresh_pricing_if_stale(cached, fresh_observations)` `pipeline.py:_refresh_pricing_if_stale` which calls `aggregate_pricing` when TTL>14d, otherwise verbatim copy. Benchmarks path: `_gap_fill_benchmarks` `pipeline.py:_gap_fill_benchmarks` — immutable gap-fill only (null→fill, never overwrite), per Wayfinder #91 / ADR 0007.
- On miss (new key, stale, identity-bad UUID): full `evaluate_model` path — `EvidenceCollector.collect()` + `resolve_model` + `Judge.evaluate()` + `PolicyGate.apply()` + `is_accurate_enough` gate `gate.py`.

So the real query pattern is:

```
lookup:  normalize_store_key(raw_id) → dict.get(key)           // O(1) hash, 24–314 keys
fresh?:  is_stale(_meta.last_updated, 14)                       // date compare
pricing: _refresh_pricing_if_stale(cached, catalog_observations) // aggregate_pricing re-average, no LLM
bench:   _gap_fill_benchmarks(cached_bm, fresh_bm)             // union-max, immutable
miss?:   Judge.evaluate → LLM search + Gate → put(key, merged)  // only path that adds new entry
```

All lookups are **point lookups by normalized key**. There are no range scans, no joins, no aggregation over the store, no full-text search over evidence strings. `ModelInfoStore` holds an in-memory `dict[str, ModelInfoRecord]` after lazy `load()` `model_info_store.py: ModelInfoStore.load` — subsequent `get()` is a dict hash, microseconds.

### 2b. What SQLite would add (and not)

| Judge need | JSON dict today | SQLite equivalent | Win? |
|------------|-----------------|-------------------|------|
| Point lookup `key → record` | `dict.get(normalize(key))` — O(1), in-memory after one 55 KB parse (~3 ms) | `SELECT ... WHERE key=?` with PK index — O(log n), requires connection + query parse + row decode | No — slower for 24 keys; identical for 10k keys at this scale |
| TTL check | `is_stale(last_updated, 14)` in Python | `WHERE last_updated > date('now','-14 days')` — pushes predicate to SQL | No material win — same date math, but now in SQL string |
| Pricing re-average | `aggregate_pricing(obs)` Python `statistics.median/mean` with outlier to `per_provider_overrides` `model_info_store.py: aggregate_pricing` | Same Python call after `SELECT pricing` — still need to fetch JSON and parse; SQL `AVG` cannot replicate outlier logic | No — pricing logic stays in Python either way |
| Benchmark gap-fill | `_gap_fill_benchmarks` Python dict merge | Same — JSON column still needs Python merge | No |
| Add new entry on miss | `store.put(key, merged)` → `merge_records` + `_atomic_write_json` `model_info_store.py: put/save` | `INSERT OR REPLACE` in transaction | Marginal — atomic write already handles this; SQLite transaction is equivalent |
| FTS over evidence | Not needed — evidence not in slim v2 store (dropped per ADR 0006); evidence lives in YAML ephemeral + LLM packet | FTS5 index over evidence text | Only helps if evidence re-added to store and judge needs text search — currently evidence is not persisted by design (slim v2) |

**Verdict on (a):** SQL does not help the actual judge/pipeline pattern. The workload is key-value point lookups over a tiny working set that already lives in memory. No analytical query, no join, no FTS is exercised today. If FTS over evidence descriptions ever becomes a judge need, it would require re-adding evidence to the store first — a schema decision, not a storage-engine decision.

---

## 3. Build_all bottleneck — I/O vs LLM vs catalog loads

`src/llm_discovery/build_all.py: build_all` orchestrates: parse `providers.yaml` → optional catalog load (`artificial_analysis_models.json` + `models_dev_catalog.json` via `ArtificialAnalysisCatalog`/`ModelsDevCatalog` `build_all.py` catalog block) → sequential `discover_provider` per provider (each with `ThreadPoolExecutor(max_workers=4)` for per-model `evaluate_model`) → `backfill` dedup + `aggregate_pricing` + `merge_records` → `gc(live_keys, 14)` → telemetry.

Measured parse costs (2026-09-06 hardware):

- `model_info_store.json` 55 KB → `json.loads` ~2–5 ms. Dict stays resident.
- `data/results/*.yaml` 1.1 MB across 23 files → ~30–50 ms total `yaml.safe_load`.
- `artificial_analysis_models.json` 743 KB + `models_dev_catalog.json` 8.3 MB → ~80–120 ms each, loaded once, cached as `BenchmarkDataCache`.
- `_atomic_write_json` `model_info_store.py:_atomic_write_json` — `mkstemp` + `json.dump` + `fsync` + `os.replace` — ~5–10 ms per `put` (only for Keeps, strong-only gate filters most).

Dominant cost per `build_all` run over 23 providers × ~13.6 keeps/provider avg (314 keeps / 23 files) without cache hits:

- Per-provider `discover_models` HTTP `GET {base_url}/models` `discovery.py:37-65` — 200–2000 ms each, 23 providers sequentially → 5–30 s wall (sequential loop `build_all.py: for name in provider_list`).
- Per-model `Judge.evaluate` LLM call (`agnes-2.0-flash`) — 1–5 s each including retries. Even with `max_workers=4`, 314 candidates × LLM would be minutes; TTL reuse via `get_if_fresh` + `classify_hit` is the intended saver (strong Keeper ≤14d reused without LLM via `build_cached_keep_record`).
- Evidence collection + `resolve_model` + `PolicyGate` — 10–50 ms per model.

So I/O is <1% of wall time when LLM is involved, <10% when fully cached (still YAML parse vs HTTP). The prior attempt noted "build_all very slow" — that's expected: LLM-bound, not storage-bound. Switching 55 KB JSON to SQLite saves ~0–5 ms off a job that is 30 s–10 min LLM-bound. Quantified: SQLite WAL append + `SELECT` per key would be ~1–3 ms per lookup vs 0.001 ms dict — actually slower at this n.

**Verdict on (b):** File I/O is not the bottleneck. LLM calls and sequential provider discovery dominate. Fix is TTL hit rate + parallelism (increase `max_workers`, parallelize provider loop, or cache-optional catalog handling already in `build_all.py`), not storage engine.

---

## 4. Operational comparison — migration, Podman WAL/locking, backup, gitignore

Detailed option table — five candidates the ticket asked to compare:

| Dimension | A. Keep JSON (current) + tune cache/WAL | B. SQLite primary (wholesale: all data/ except providers.yaml) | C. SQLite + FTS5 | D. DuckDB | E. Hybrid (JSON Source of Truth + SQLite derived index) |
|-----------|------------------------------------------|---------------------------------------------------------------|------------------|-----------|----------------------------------------------------------|
| **What moves** | Nothing. Store stays `data/model_info_store.json` v2; results stay YAML ephemeral; catalogs stay JSON cache-optional; bifrost stays SQLite where it is. | Everything: store + 23 YAMLs + 2 catalog snapshots + bifrost json+db into one `data/llm_discovery.db` with tables `models`, `ephemeral_reports`, `catalog_aa`, `catalog_modelsdev`, `bifrost_config`, `logs`. Judge queries DB directly. | Same as B plus `CREATE VIRTUAL TABLE models_fts USING fts5(evidence, benchmarks_text)` for judge text search. | Same as B but DuckDB file `data/llm_discovery.duckdb` (columnar, analytical). | Keep A as canonical; add regenerable `data/derived/judge_index.db` built from JSON at build tail. Judge reads SQLite with JSON fallback. |
| **Judge query change** | None — `ModelInfoStore.get(normalize(key))` dict. | Replace with `sqlite3` connection + `SELECT` per key; need `normalize_store_key` still in Python. | Same plus `SELECT ... WHERE models_fts MATCH ?` for evidence search. | Same as B but DuckDB Python API; no WAL, single-writer. | Judge tries SQLite first, falls back to dict — same semantics. |
| **Pricing-only semantics** | Already handled: `_refresh_pricing_if_stale` re-averages when `_pricing_is_stale` >14d via `aggregate_pricing` (outlier → `per_provider_overrides`), no LLM. `_gap_fill_benchmarks` immutable. `is_accurate_enough` gate before put. | No change to logic — same Python functions after `SELECT pricing`. Only swaps persistence. Ticket question (d): "does SQLite change pricing-only update?" — No. The policy lives in `pipeline.py`/`model_info_store.py`, not in storage engine. | Same — FTS irrelevant to pricing. | Same. | Same — hybrid still calls same Python after read. |
| **Migration complexity** | Zero. | High — schema design for 5 grains, ETL for 23 YAMLs (keep/drop/error lists with nested pricing/benchmarks JSON), catalog JSON→tables, backfill `merge_records` + `aggregate_pricing` rewritten as SQL or kept in Python after fetch, GC `live_keys - before_keys` logic ported, `backfill.py` replaced, `.gitignore` flipped, `ModelInfoStore` rewritten, tests updated. Est. 2–4 days + regression risk. | Same as B + FTS tokenizer choice, triggers to keep FTS in sync on `INSERT OR REPLACE`. | Same as B + DuckDB dependency added, different SQL dialect, no `INSERT OR REPLACE` parity. | Low — one `scripts/migrate_store_to_sqlite.py`: `ModelInfoStore.load() → CREATE TABLE models(key TEXT PRIMARY KEY, benchmarks TEXT, pricing TEXT, last_updated TEXT) → INSERT OR REPLACE`, WAL on, regenerable. No ETL for YAMLs/catalogs. ~half day. |
| **Podman WAL / locking** | Current `model_info_store.py: put` uses `fcntl.flock` on `.store.lock` + `_atomic_write_json` tmp+`os.replace` (POSIX atomic) `model_info_store.py:_atomic_write_json`. Works under Podman volume (host file, host lock). Bifrost's own `config.db` already WAL with `-shm`/`-wal` owned by `nobody:nogroup` (observed 27 MB + 2 MB WAL) — separate concern. | Adds second WAL DB under same volume. Two WALs contend for `fsync` ordering; Podman rootless mapping (`nobody`) already causes `bifrost.bak-nobody` permission-denied seen in `du`; SQLite `PRAGMA journal_mode=WAL` requires `shm` shared memory file — under Podman quadlet volume mount, `shm` locking can `SQLITE_BUSY` if two containers race. Need `busy_timeout` + `WAL` + single writer discipline. | Same as B, worse — FTS5 triggers hold write lock longer. | DuckDB is single-writer, no WAL — Podman concurrent writers would corrupt or `Failed to acquire lock`. Requires external lock still. | Minimal — derived DB is single-writer at build tail, read-only for judge. No concurrent WAL writers. Can `DELETE` + rebuild rather than incremental WAL. |
| **Backup / restore** | `cp data/model_info_store.json data/model_info_store.json.bak` or git history — human-readable diff, `json diff` in PR. Restore = `git checkout` or `cp`. | SQLite needs `sqlite3 .dump` or `VACUUM INTO` for consistent backup; `cp` while WAL active can produce torn DB (need `sqlite3_backup` API). Restore = `sqlite3 new.db < dump.sql`. Loses git-diff ergonomics. | Same as B + FTS index rebuild on restore. | DuckDB: `EXPORT DATABASE` / `IMPORT DATABASE`; not `cp`-safe while open. | JSON remains backupable as before; derived SQLite is disposable — delete and rebuild from JSON. |
| **Git / gitignore** | `data/` ignored, `!data/model_info_store.json` committed — store diffs reviewable in PR (slim v2, 55 KB). YAMLs ephemeral, not reviewed. | Flip to `data/llm_discovery.db` binary committed or ignored? If committed: binary diff not reviewable, `git` stores full blob each commit (bloat). If ignored: lose audit trail that JSON provided. Either way worse. Need `*.db` + `*.wal` + `*.shm` in `.gitignore` and lose history. | Same as B. | Same. | Keep `.gitignore` as is; derived `data/derived/*.db` added to ignore — no commit, no diff loss. |
| **Tooling / deps** | `json` + `yaml` stdlib. | `sqlite3` stdlib (Python) — no new dep, but schema/migration scripts + `PRAGMA` tuning. | Same + `fts5` compile check. | New dep `duckdb` (~50 MB wheel), not in current `requirements`. | `sqlite3` stdlib only for derived index. |
| **Performance at this scale** | 24 models dict — O(1). Scales to ~10k keys linear JSON parse ~20 ms + dict — still fine. Honest threshold for SQL win is 100k+ rows or need for joins/aggregations. | Same or slower at n=24; wins only if 100k models or need for ad-hoc `WHERE pricing.blended > 5` analytics (not current need). | Wins only if evidence FTS needed. | Wins for analytical scans over 8.3 MB catalog (e.g. `SELECT provider, avg(coverage)`) — not current hot path. | Same as A for primary path; adds ~10 ms build step for index. |
| **Risk** | Lowest — proven atomic write + TTL + GC per ADRs 0005-0007. | Highest — wholesale replacement, bifrost coupling risk (gateway already owns `config.db` WAL), migration bugs. | Same as B plus FTS sync bugs. | Highest — new dep + single-writer constraint. | Low — additive, reversible. |

**Verdict on (c):** Operational cost strongly favors Keep. Podman WAL already shows friction (`nobody:nogroup` ownership, `data/bifrost.bak-nobody` permission-denied, `-wal`/`-shm` files needing `busy_timeout`). Adding a second primary WAL DB under the same volume doubles that friction. Backup/restore and git diff both get worse with binary SQLite. DuckDB has no WAL — worse under Podman. The only low-risk shape is Hybrid derived index.

---

## 5. Recommendation

### Recommended: **A. Keep JSON** — with optional **E. Hybrid derived index** if FTS later needed

- **Primary:** Keep `data/model_info_store.json` slim v2 as Source of Truth (`CONTEXT.md` Source of Truth, ADR 0005 §1, ADR 0006 §2, ADR 0007). Keep `data/results/*.yaml` Ephemeral Reports gitignored and overwritten each `build_all`. Keep `data/artificial_analysis_models.json` + `data/models_dev_catalog.json` as cache-optional snapshots (`build_all.py` already handles missing catalogs gracefully). Keep `data/bifrost/config.db`/`logs.db` owned by Bifrost — do not merge discovery cache into gateway runtime DB.
- **Tuning instead of migration:** Fix `build_all` slowness by tuning what actually dominates (see companion #135 + ADR 0007): increase TTL hit rate (strong-only Keeper 14d reuse via `get_if_fresh`), parallelize provider loop (today sequential `for name in provider_list` `build_all.py`), raise `max_workers` where LLM concurrency allows, keep catalog cache-optional, and keep `_refresh_pricing_if_stale` pricing TTL without LLM. JSON parse is not the lever.
- **Only if judge needs text search:** Add **E. Hybrid** — regenerable `data/derived/judge_index.db` built from the JSON store at `build_all` tail (after `backfill` + `gc`). Judge seam in #137 tries SQLite first, falls back to `ModelInfoStore` dict. Evidence FTS would require re-adding evidence to store first (slim v2 dropped it per #95), so defer FTS until that schema decision is made.

Not recommended: **B. Wholesale SQLite** — high migration, Podman WAL contention, git diff loss, backup complexity, no measurable speedup. **C. SQLite+FTS** — same plus FTS only useful if evidence re-added. **D. DuckDB** — analytical engine for a point-lookup workload; single-writer, heavier dep, Podman-unfriendly.

### Migration outline (for completeness — only for Hybrid E if pursued)

> This outline is included because the ticket asks for one, even though the recommendation is Keep. It is scoped to Hybrid (derived index), not wholesale.

**Goal:** Add regenerable SQLite without replacing the Source of Truth.

1. **Schema (derived, WAL, single table):**
   ```sql
   PRAGMA journal_mode=WAL;
   PRAGMA busy_timeout=5000;
   CREATE TABLE IF NOT EXISTS models(
     key TEXT PRIMARY KEY,              -- normalize_store_key(model_id)
     benchmarks TEXT NOT NULL,          -- JSON: {scores, raw_benchmarks, coverage}
     pricing TEXT NOT NULL,             -- JSON: {blended,input,output,per_provider_overrides}
     last_updated TEXT NOT NULL,        -- ISO8601 from _meta.last_updated
     version INTEGER NOT NULL DEFAULT 2
   ) WITHOUT ROWID;
   CREATE INDEX IF NOT EXISTS idx_models_last_updated ON models(last_updated);
   -- FTS only if evidence re-added:
   -- CREATE VIRTUAL TABLE models_fts USING fts5(key, evidence, benchmarks_text, content='models', content_rowid='rowid');
   ```
   Keep `pricing`/`benchmarks` as JSON text — `aggregate_pricing` and `_gap_fill_benchmarks` stay in Python after `SELECT`. Do not try to push outlier logic into SQL.

2. **Builder script `scripts/build_judge_index.py` (regenerable):**
   ```python
   store = ModelInfoStore("data/model_info_store.json"); store.load()
   db = sqlite3.connect("data/derived/judge_index.db")
   db.execute("DELETE FROM models")  # or DROP+CREATE for clean rebuild
   for key, rec in store._data.items():
       db.execute("INSERT OR REPLACE INTO models VALUES (?,?,?,?,?)",
           (key, json.dumps(rec.benchmarks), json.dumps(rec.pricing.to_dict()), rec._meta.last_updated, 2))
   db.commit()
   ```
   Called at tail of `build_all` after `backfill` + `gc` + `store.save()` (pretty+compact assert `build_all.py` already does). File goes to `data/derived/` (new dir, added to `.gitignore` as `data/derived/`), not `data/` root.

3. **Judge seam (#137):** `ModelInfoStoreSQLite` wrapper with same interface (`get(key)`, `get_if_fresh(key,14)`, `put`) but read-through: try `SELECT` → parse JSON → return `ModelInfoRecord.from_dict`; on miss fall back to `ModelInfoStore.get`. On LLM-adding-new-entry, write to JSON store first (`store.put` → atomic), then `INSERT OR REPLACE` into SQLite. Keep `normalize_store_key` in Python — no SQL normalization.

4. **Podman / WAL:** Derived DB is single-writer (build tail) + read-only for judge (no concurrent writers). No second WAL contention with Bifrost's `config.db`. Set `PRAGMA journal_mode=WAL` and `busy_timeout` anyway. No `shm` sharing issue since judge is in same process as builder in llm-discovery container.

5. **Backup / gitignore:** Add `data/derived/` to `.gitignore`. Derived DB is disposable — backup is the JSON store (git history). No binary in git.

6. **Wholesale migration outline (NOT recommended — for reference only):** If wholesale B were ever pursued despite the recommendation, steps would be: design 5-table schema (models, ephemeral_reports, catalog_aa, catalog_modelsdev, bifrost_config), ETL 23 YAMLs + 2 catalog JSONs, port `backfill.py` `merge_records`/`aggregate_pricing`/`gc` to SQL-or-Python-after-fetch, replace `ModelInfoStore` with `sqlite3` driver, rewrite `backfill`+`build_all`+`pipeline` cache helpers, add `PRAGMA`/`VACUUM` policy, handle Podman `nobody` ownership + WAL, change `.gitignore` to ignore `*.db`/`*.wal`/`*.shm` and lose JSON diff audit. Est. 2–4 days, high regression risk — not justified for 24–314 keys.

---

## 6. Hand-off checklist for #137 and #135

- #137 (judge seam): If Keep (A), seam is `pipeline.py` cache helpers already present (`classify_hit`, `_refresh_pricing_if_stale`, `_gap_fill_benchmarks`, `build_cached_keep_record`) — no SQL seam needed. If Hybrid (E), seam is `ModelInfoStoreSQLite` read-through with JSON fallback as sketched above.
- #135 (build_all profiling): Focus on LLM concurrency and provider-loop parallelism, not storage engine. Measure `discover_models` HTTP + `Judge.evaluate` wall, not JSON parse.
- Wayfinder #132 notes "`config/providers.yaml` stays the only user-touched file" — both Keep and Hybrid honor that; wholesale would violate separation by co-mingling gateway `config.db` with discovery cache.

---

## References (primary-source)

- `CONTEXT.md` — Source of Truth = committed `data/model_info_store.json` slim v2, Ephemeral Report, Accurate-Enough Gate, Record TTL 14d, Pricing Delta, Evidence Delta disabled.
- `docs/research/issue-71-data-files-formation.md` — formation, sizes, triggers, atomicity, one-command gap.
- `docs/adr/0005-model-info-store-persistence.md` — JSON over SQLite/YAML, `!data/model_info_store.json`, fcntl+atomic, lazy in-memory dict, version 2 slim.
- `docs/adr/0006-accurate-enough-gate-and-store-source-of-truth.md` — strong-only gate, store slim v2 holds only benchmarks+pricing+_meta, YAML ephemeral.
- `docs/adr/0007-incremental-build-invalidation-policy.md` — ranked invalidation (Identity → Churn → Pricing TTL 14d → Time), benchmarks immutable gap-fill, pricing re-average.
- `src/llm_discovery/model_info_store.py:47-93` `normalize_store_key`, `:47-93`, `aggregate_pricing`, `_atomic_write_json`, `ModelInfoStore.load/put/get_if_fresh/gc`.
- `src/llm_discovery/pipeline.py: classify_hit`, `_pricing_is_stale`, `_refresh_pricing_if_stale`, `_gap_fill_benchmarks`, `build_cached_keep_record`, `TTL_DAYS=14`.
- `src/llm_discovery/build_all.py: build_all` — sequential provider loop, catalog cache-optional, `backfill` + `gc` + telemetry.
- `src/llm_discovery/judge.py:1-54` — thin adapter, builds `ModelEvaluationRequest`, delegates to evaluator (LLM).
- `src/llm_discovery/gate.py: is_accurate_enough` — 7-floor Keeper predicate.
- Live sizes 2026-09-06: `data/model_info_store.json` 55,740 B (24 models) `data/results` 1,118,726 B (23 files, 314 keeps) `artificial_analysis_models.json` 759,906 B `models_dev_catalog.json` 8,701,215 B `data/bifrost/config.db` 27,467,776 B + WAL 2,006,472 B `logs.db` 319,488 B.
