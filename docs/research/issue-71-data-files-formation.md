# Research #71 — How data files are formed today (refresh, discovery, backfill)

**Issue:** [#71 Research: How data files are formed today](https://github.com/SoongGuanLeong/llm-discovery/issues/71) — child of Wayfinder map [#70 Unified cached data strategy](https://github.com/SoongGuanLeong/llm-discovery/issues/70) (frontier ticket)  
**Parent map:** [#63 Wayfinder: Cross-provider model-info reuse store](https://github.com/SoongGuanLeong/llm-discovery/issues/63) (closed) and children #64–#69  
**Status:** research — primary-source audit, no build  
**Date:** 2026-09-04  
**Sources:** source code, `config/providers.yaml`, `data/` directory listing and live sizes, `.gitignore`, and `src/llm_discovery/*` (refresh, results, backfill, pipeline, catalogs, benchmarks, discovery, model_info_store). All claims cited `file:line`.

> Wayfinder #70 asks whether a single-file, token-friendly, one-command, SCD1-latest cached store is feasible for 100–200 providers. This research answers by mapping what exists today: what files are in `data/`, how each is produced, where refresh / discovery / backfill overlap, why `cli refresh` alone is insufficient, and what the designed-but-empty `model_info_store.json` would replace.

---

## 1. What exists in `data/` today — sizes, formats, grain

Verified on disk 2026-09-04 (`ls -R data`, `du -sb`, `wc -c`):

| Path | Format | Size on disk | Grain | Committed? |
|------|--------|--------------|-------|------------|
| `data/artificial_analysis_models.json` | JSON | 743 KB (759,906 B) — 631 models `src/llm_discovery/catalogs.py:10` `data/*.json` header `fetched_at` | one file, snapshot of AA API `GET https://artificialanalysis.ai/api/v2/data/llms/models` `src/llm_discovery/refresh.py:21` | gitignored via `data/` `.gitignore:21` |
| `data/models_dev_catalog.json` | JSON | 8.3 MB (8,701,215 B) — 364 models, 212 providers `src/llm_discovery/catalogs.py:51-52` | one file, snapshot of `https://models.dev/catalog.json` `src/llm_discovery/refresh.py:22` | gitignored |
| `data/benchmarks.json` | JSON | 368 KB (376,792 B) — 699 entries, key = model slug | derived: union of AA evaluations + models.dev benchmarks `src/llm_discovery/benchmarks.py:176-241` | gitignored |
| `data/nararouter_raw.json` | JSON | 11 KB (10,486 B) | raw dump: `GET {base_url}/models` for NaraRouter (`{object:"list", data:[{id, owned_by, context_window}]}`) | gitignored |
| `data/nararouter_raw_full.json` | JSON | 11 KB (10,486 B) | duplicate full dump variant | gitignored |
| `data/nararouter_pricing_raw.json` | JSON | 42 KB (42,300 B) | raw pricing/plan dump (not via refresh) | gitignored |
| `data/artifacts/nararouter_plans.json` | JSON | 5.6 KB | derived artifact: `GET https://router.bynara.id/api/plans` snapshot `src/llm_discovery/discovery.py:144-148` | gitignored (under `data/`) |
| `data/results/*.yaml` | YAML | 51 KB total across 17 files (17 = `ls data/results/*.yaml | wc -l`); per-file 312–9285 B, median ≈ 394 B. Outliers: `cloudflare.yaml` 30,519 B (980 lines, many `error` entries), `agnes.yaml` 5,969 B, `nararouter.yaml` 9,285 B | one file per provider (`{provider}.yaml`) — grain = provider. Each file shape: `{provider, evaluated_at, keep:[], drop_llm:[], error:[]}` `src/llm_discovery/results.py:112-203` | gitignored (under `data/`) — but consumed as source of truth for backfill |
| `data/model_info_store.json` | JSON (**designed, not present**) | 0 B — file missing on disk (`ls: cannot access`), `test -f` → missing | grain = normalized model name (deduped cross-provider). Shape: `{version:1, models:{ normalize_store_key(model_id): ModelInfoRecord }}` `src/llm_discovery/model_info_store.py:756,854-855` | **exception**: `!data/model_info_store.json` in `.gitignore:22` — intended to be committed snapshot |

`.gitignore:21-22` is the source of the split:

```gitignore
data/
!data/model_info_store.json
```

Everything under `data/` is ignored except the store. That explains why catalog snapshots (8 MB) never appear in commits while the store is meant to be reviewed as a diff.

### Grain summary

- **Snapshots (refresh):** grain = entire catalog (one JSON per upstream). No per-model sharding.
- **Reports (discovery):** grain = provider (one YAML per provider). Lists are `keep / drop_llm / error`.
- **Artifacts:** grain = one-off fetch (NaraRouter plans).
- **Store (designed):** grain = normalized model name (cross-provider dedup).

---

## 2. How each file is produced — entry point, code path, atomicity, trigger

### 2a. Catalog snapshots via `refresh.py` (AA, models.dev, benchmarks)

**Entry points:**

- CLI: `llm-discovery refresh` → `cli.py:84-110` delegates to `refresh_all()` `src/llm_discovery/cli.py:85`
- Direct: `python -m llm_discovery.refresh` → `refresh.py:190-216` argparse
- Programmatic: `refresh_all(data_dir, only={"aa","models_dev","benchmarks"})` `src/llm_discovery/refresh.py:171-187`

**Code paths (in order, `refresh_all` `src/llm_discovery/refresh.py:172,179-186`):**

1. **AA:** `refresh_artificial_analysis()` `src/llm_discovery/refresh.py:130-139` → `fetch_artificial_analysis()` `src/llm_discovery/refresh.py:86-99` (httpx GET with optional `AA_API_KEY` header `src/llm_discovery/refresh.py:92-95`, normalize via `_normalize_aa_payload()` `src/llm_discovery/refresh.py:48-83`) → `_atomic_write_json(output, data, backup)` `src/llm_discovery/refresh.py:135`
2. **models.dev:** `refresh_models_dev()` `src/llm_discovery/refresh.py:142-151` → `fetch_models_dev()` `src/llm_discovery/refresh.py:102-127` → `_atomic_write_json`
3. **benchmarks:** `refresh_benchmarks()` `src/llm_discovery/refresh.py:154-168` → `BenchmarkDataCache.collect_from_local(aa, models_dev)` `src/llm_discovery/refresh.py:160` where `BenchmarkDataCache` is defined in `src/llm_discovery/benchmarks.py:157-242`. It iterates `models_dev.models` (`src/llm_discovery/benchmarks.py:181-204`) and `aa.models` (`src/llm_discovery/benchmarks.py:207-241`) merging canonical keys via `BENCHMARK_NAME_MAP` `src/llm_discovery/benchmarks.py:25-70`. Then `_atomic_write_json` to `benchmarks.json`.

**Atomicity & durability:**

- `_atomic_write_json` in refresh `src/llm_discovery/refresh.py:25-45` does: `shutil.copy2(path, path.bak)` if exists and `backup=True` `src/llm_discovery/refresh.py:30-32`, then `tempfile.mkstemp(dir=path.parent, suffix=".tmp")` `src/llm_discovery/refresh.py:33`, `json.dump` + `flush` + `os.fsync` `src/llm_discovery/refresh.py:36-39`, then `Path(tmp).replace(path)` `src/llm_discovery/refresh.py:40`. Backup file is `.bak` (e.g. `benchmarks.json.bak`).
- Benchmark derived step clears cache first: `cache._data = {}; cache._loaded = True` `src/llm_discovery/refresh.py:158-159` — rebuild is not incremental (SCD1 by overwrite).

**Trigger:** manual operator command. No cron, no pipeline hook, no file watcher. Requires network and (for AA) an API key; 401 prints hint `src/llm_discovery/refresh.py:208-209`.

**Data dir default:** `DATA_DIR = Path("data")` `src/llm_discovery/refresh.py:20`, `src/llm_discovery/cli.py:7`, `src/llm_discovery/benchmarks.py:23`. Relative to CWD; tests may inject another dir via `--data-dir` `src/llm_discovery/refresh.py:193`, `src/llm_discovery/cli.py:69`.

### 2b. Per-provider reports via discovery → `results.py`

**Entry points:**

- Single provider: `pipeline.discover_provider(provider_name, config, aa, models_dev, max_workers=4)` `src/llm_discovery/pipeline.py:362-487`
- All providers: `pipeline.discover_all_providers(config, aa, models_dev, output_dir=Path("data/results"))` `src/llm_discovery/pipeline.py:490-522` — loop over `config.providers` `src/llm_discovery/pipeline.py:505`, calls `discover_provider` then `save_provider_result(result, name, output_dir)` `src/llm_discovery/pipeline.py:521`
- Tracer bullet (1 model): `pipeline.discover_single()` `src/llm_discovery/pipeline.py:282-359` (returns record, does not write YAML)

**Discovery layer** (`src/llm_discovery/discovery.py`):

- Generic OpenAI-compatible: `discover_models(base_url, api_key)` `src/llm_discovery/discovery.py:37-65` → `GET {base_url}/models` with `Authorization: Bearer` `src/llm_discovery/discovery.py:51`, handle `data["data"]` or `data["models"]` `src/llm_discovery/discovery.py:57-64`, normalize via `_normalize_models()` `src/llm_discovery/discovery.py:7-34`
- Cloudflare: `discover_cloudflare_models(account_id, api_key)` `src/llm_discovery/discovery.py:68-92` → `GET /accounts/{id}/ai/models/search` (`result` key) `src/llm_discovery/discovery.py:82-91`
- NaraRouter: `discover_nararouter_models()` `src/llm_discovery/discovery.py:158-176` wraps `discover_models` + `get_nararouter_free_allowlist()` `src/llm_discovery/discovery.py:116-155` which fetches `GET https://router.bynara.id/api/plans` `src/llm_discovery/discovery.py:97` and extracts `code=="free"` allowlist; on failure uses hard-coded snapshot `NARAROUTER_FREE_SNAPSHOT` `src/llm_discovery/discovery.py:100-110` and best-effort writes artifact `data/artifacts/nararouter_plans.json` `src/llm_discovery/discovery.py:144-148`

**Evaluation pipeline** (`src/llm_discovery/pipeline.py:103-138`):

For each model: `resolve_model` → `EvidenceCollector.collect()` → optional vision/bypass `src/llm_discovery/pipeline.py:122-129` → `Judge.evaluate()` → `PolicyGate.apply()` → record with `{provider_model_id, decision, tier, aa_model_id, aa_score, coding_score, pricing, benchmarks, confidence, evidence_level, evidence, stage}` (see `_llm_error_record` shape `src/llm_discovery/pipeline.py:141-160` and `deterministic_drop_record`). Free-model filtering splits before LLM: `_split_by_free_rule()` `src/llm_discovery/pipeline.py:608-626` (marker `":free"|"-free"|"_free"|"/free"` `src/llm_discovery/pipeline.py:581`, navy_ai special: `premium is False` `src/llm_discovery/pipeline.py:596-597`). Dropped models are never sent to LLM nor written to the keep list.

**Write path** (`src/llm_discovery/results.py`):

- `ProviderBatchWriter.write(result, provider, output_dir)` `src/llm_discovery/results.py:155-203` is the primary writer (T3 batch). It projects each record via `_to_record()` `src/llm_discovery/results.py:126-153` (strips `model_id/provider` duplication from benchmarks `src/llm_discovery/results.py:129-130`, normalizes tier `src/llm_discovery/results.py:140` via `_normalize_tier` `src/llm_discovery/results.py:10-13`, normalizes model id `src/llm_discovery/results.py:138` via `_normalize_model_id` (stepfun→step) `src/llm_discovery/results.py:15-20`, filters `drop_llm` to exclude `free-model-rule` phantoms `src/llm_discovery/results.py:186-191`).
- Payload is `{provider, evaluated_at, keep:[], drop_llm:[], error:[]}` `src/llm_discovery/results.py:193-199` written with `yaml.safe_dump(sort_keys=False)` to `{output_dir}/{provider}.yaml` `src/llm_discovery/results.py:202`. No atomic tmp/replace, no fsync, no lock — plain `Path.write_text()`.
- Legacy wrappers: `save_provider_result()` `src/llm_discovery/results.py:223-233`, `save_all_providers_result()` `src/llm_discovery/results.py:236-244`, and `SingleModelWriter` (T2) `src/llm_discovery/results.py:62-109` (unused in batch path).
- `discover_all_providers` creates the dir: `output_dir.mkdir(parents=True, exist_ok=True)` `src/llm_discovery/pipeline.py:498`.

**Atomicity:** none beyond directory creation. Concurrent providers under `ThreadPoolExecutor` in `discover_provider` (per-model evaluation) `src/llm_discovery/pipeline.py:447-461` share the same file only at final `save_provider_result` which is called sequentially per provider in `discover_all_providers` loop `src/llm_discovery/pipeline.py:505-521`, so no write race on results YAMLs. Per-provider files are independent.

**Trigger:** manual or scripted call to `discover_provider` / `discover_all_providers`. Not invoked by `refresh`. No file watcher or automatic hook; operator must run discovery after refresh.

### 2c. Backfill / dedup store via `backfill.py` (and `model_info_store.py` persistence)

**Entry points:**

- CLI: `python -m llm_discovery.backfill` → `backfill.py:189-197` argparse (`--results-dir data/results --store-path data/model_info_store.json` `src/llm_discovery/backfill.py:193-194`)
- Programmatic: `backfill(results_dir="data/results", store_path="data/model_info_store.json")` `src/llm_discovery/backfill.py:46-48`

**Code path** (`src/llm_discovery/backfill.py:46-186`):

1. Instantiate `ModelInfoStore(store_path)` `src/llm_discovery/backfill.py:67` (lazy load, see persistence below).
2. Enumerate `sorted(results_dir.glob("*.yaml"))` `src/llm_discovery/backfill.py:70`.
3. For each YAML, parse via `_parse_results_file()` `src/llm_discovery/backfill.py:22-43` (supports standard `{keep:[]}` `src/llm_discovery/backfill.py:31-37` and legacy single-record shape with `decision=="keep"` `src/llm_discovery/backfill.py:39-42`).
4. For each `keep` record: check `should_cache(evidence_level, confidence)` `src/llm_discovery/backfill.py:92` (`src/llm_discovery/model_info_store.py:230-237` gates strong/moderate only). Skipped weak/none increments `weak_skipped` `src/llm_discovery/backfill.py:93`.
5. Dedup key: `normalize_store_key(model_id)` `src/llm_discovery/backfill.py:95` (fallback to `aa_model_id` `src/llm_discovery/backfill.py:98`).
6. Build `ModelInfoRecord.from_provider_record(rec, provider, evaluated_at)` `src/llm_discovery/backfill.py:104` (`src/llm_discovery/model_info_store.py:520-566` maps pricing, benchmarks, tier, evidence, and builds `StoreMeta{first_seen, last_updated, source_providers, source_evidence_levels}` `src/llm_discovery/model_info_store.py:549-554`).
7. Bucket by key, then per-key: sequential `merge_records(existing, incoming)` `src/llm_discovery/backfill.py:143` (`src/llm_discovery/model_info_store.py:612-677` per-field best-of by evidence rank→confidence→newer timestamp), plus `aggregate_pricing(obs_list)` `src/llm_discovery/backfill.py:149` (`src/llm_discovery/model_info_store.py:271-351`, outlier if >50% from median and >$0.20/$0.15 `src/llm_discovery/model_info_store.py:249-251`), wrap as `PricingSnapshot` `src/llm_discovery/backfill.py:152-157`, and `store.put(key, merged)` `src/llm_discovery/backfill.py:170`.
8. Returns stats: `{files_processed, total_keep_records, unique_models, merged_conflicts, pricing_avgs, pricing_outliers/outliers, weak_skipped, evaluated_at_range, store_path, store_size}` `src/llm_discovery/backfill.py:172-186`.

**Persistence inside the put path** (`src/llm_discovery/model_info_store.py:851-1038`):

- `ModelInfoStore` is lazy (`_ensure_loaded` `src/llm_discovery/model_info_store.py:909-911`). On first `get()/put()`, `load()` reads the JSON file if present (`src/llm_discovery/model_info_store.py:868-900` handles both new wrapper `{version, models:{}}` `src/llm_discovery/model_info_store.py:882-884` and legacy bare dict without wrapper), else starts empty.
- `put()` `src/llm_discovery/model_info_store.py:948-986` gates via `should_cache` `src/llm_discovery/model_info_store.py:950`, best-effort re-reads file to avoid lost updates when multiple Store instances race (`src/llm_discovery/model_info_store.py:954-980`), then `merge_records` and `save()` (`src/llm_discovery/model_info_store.py:984-986` → `_atomic_write_json` `src/llm_discovery/model_info_store.py:831-849` which uses `tempfile.mkstemp(prefix=".tmp-store-")` and `os.replace` + `fsync`).
- File-level header: `STORE_FILE_VERSION = 1` `src/llm_discovery/model_info_store.py:799`, payload `{version:1, models:{key: record.to_dict()}}` `src/llm_discovery/model_info_store.py:903-905`.

**Atomicity:** per-key atomic write + best-effort file lock via `fcntl.flock` helper (`_acquire_lock` / `_release_lock` `src/llm_discovery/model_info_store.py:817-829`) — but note: `put()` in the current code only re-reads without holding the lock around the full read-merge-write sequence (the lock helpers exist but are not called in `put()`, only defined). The ThreadPoolExecutor race is mitigated by timestamp comparison on re-read, not by an exclusive lock.

**Trigger:** manual one-shot. Not called by refresh or discovery; not wired into `cli.py` at all. Must be run separately after results YAMLs exist.

### 2d. Ancillary writes

- **Benchmark rebuild** is already covered under refresh (derived).
- **NaraRouter plans artifact** `discovery.get_nararouter_free_allowlist` writes `data/artifacts/nararouter_plans.json` best-effort `src/llm_discovery/discovery.py:144-148` — plain `write_text`, no atomic replace.
- **Raw dumps** (`nararouter_raw.json`, `nararouter_pricing_raw.json`) have no writer in the audited code — they are operator-created snapshots (observed on disk, 11 KB / 42 KB) likely from manual `curl` to `{base_url}/models`; not produced by `refresh.py` or `discovery.py`.

---

## 3. Refresh vs discovery vs backfill — overlap and gaps

| Dimension | Refresh (`refresh.py` + `benchmarks.py`) | Discovery (`pipeline.py` + `results.py` + `discovery.py`) | Backfill + Store (`backfill.py` + `model_info_store.py`) |
|-----------|---------------------------------------------|-------------------------------------------------------------|------------------------------------------------------------|
| **Consumes** | Upstream APIs: AA `DEFAULT_AA_URL` `src/llm_discovery/refresh.py:21` + models.dev `DEFAULT_MODELS_DEV_URL` `src/llm_discovery/refresh.py:22` | `GET {base_url}/models` per provider `src/llm_discovery/discovery.py:37-65`, local catalogs `ArtificialAnalysisCatalog` `src/llm_discovery/catalogs.py:6-44` + `ModelsDevCatalog` `src/llm_discovery/catalogs.py:47-77`, judge LLM, benchmark cache `src/llm_discovery/benchmarks.py:157-176` | Local `data/results/*.yaml` only `src/llm_discovery/backfill.py:70`. No network |
| **Produces** | `artificial_analysis_models.json` (743 KB), `models_dev_catalog.json` (8.3 MB), `benchmarks.json` (368 KB) — all JSON, atomic via temp+replace `src/llm_discovery/refresh.py:25-45` | `data/results/{provider}.yaml` per provider (17 files today) via `ProviderBatchWriter.write` `src/llm_discovery/results.py:155-203` | `data/model_info_store.json` (`{version, models:{}}`) via `ModelInfoStore.save` `src/llm_discovery/model_info_store.py:902-907` |
| **Grain** | snapshot / snapshot / derived union | provider (one YAML per provider) | normalized model name (cross-provider dedup) `src/llm_discovery/model_info_store.py:47-93` |
| **Trigger** | `llm-discovery refresh` `src/llm_discovery/cli.py:84-110` | `discover_provider` / `discover_all_providers` `src/llm_discovery/pipeline.py:362-521` | `python -m llm_discovery.backfill` `src/llm_discovery/backfill.py:189-201` |
| **Atomicity** | tmp+fsync+replace + .bak `src/llm_discovery/refresh.py:25-45` | plain `yaml.safe_dump` `src/llm_discovery/results.py:202` | atomic JSON put `src/llm_discovery/model_info_store.py:831-849` + best-effort re-read, but no held lock |
| **Provider awareness** | none — catalogs are global | provider-aware; free-model rule is provider-scoped (`navy_ai` vs generic) `src/llm_discovery/pipeline.py:584-626` | provider stored as provenance `StoreMeta.source_providers` `src/llm_discovery/model_info_store.py:386-413` |

**Overlaps:**

- Benchmarks sits at the intersection: built by both refresh (fresh snapshot) and consumed by discovery (cache for `EvidenceCollector` / `Judge` via `BenchmarkDataCache.collect_from_local` `src/llm_discovery/pipeline.py:348-349,440-441`). Refresh rebuilds it from the same two catalogs discovery would read.
- `results.yaml` fields (pricing, benchmarks, tier, evidence) are the input to the store's `ModelInfoRecord.from_provider_record` `src/llm_discovery/model_info_store.py:520-566` — so discovery output is the backfill's source. No other file feeds the store.

**Gaps:**

- No path connects the three without the operator. Refresh never calls discovery; discovery never triggers backfill/put; backfill never refreshes catalogs. Three manual steps to reach the store.
- No staleness enforcement links them. `is_stale(last_updated, ttl_days)` `src/llm_discovery/model_info_store.py:802-815` exists but `ttl_days=None` → never stale by default; backfill's provenance comment notes per-key `evaluated_at` range not recomputed `src/llm_discovery/backfill.py:164-169`.
- Result YAMLs and catalog snapshots can drift: running discovery after catalogs age still writes YAMLs with old `aa_score` without any freshness guard.

---

## 4. One-command gap (CLI `refresh` only catalogs)

`cli.py:68-75` defines the only refresh surface:

```python
refresh_parser = subparsers.add_parser("refresh", help="Refresh catalog snapshots (AA + models.dev + benchmarks).")
`src/llm_discovery/cli.py:68`
`--only` choices are `-– only={"aa","models_dev","benchmarks"}` `src/llm_discovery/cli.py:75`, `src/llm_discovery/refresh.py:199`
```

For comparison, Wayfinder #70 requires "one command builds all from `config/providers.yaml`" (4 criteria: token-friendly R/W, one-command build, single-file grain=model, SCD1 latest-only + avg pricing).

**What `llm-discovery refresh` does** (`cli.py:84-96` → `refresh_all` `src/llm_discovery/refresh.py:171-187`) — fetches catalogs + derived benchmarks only. No provider enumeration, no `discover_all_providers`, no `backfill`.

**What one command would need to do:**

1. `refresh_all()` (already present)
2. `discover_all_providers(config, aa, models_dev)` (exists but not wired to CLI at all — no `cli.py` subparser for `discover`, no `argparse` glue for `--max-workers` etc. Discovery is only callable from library code / scripts).
3. `backfill(results_dir, store_path)` (exists as a module entry point `src/llm_discovery/backfill.py:189-197` but not importable via `llm-discovery` CLI).

Because (1) is isolated, (2)–(3) remain manual two more steps. Refresh's `--dry-run` `src/llm_discovery/refresh.py:198,132-134,144-146,161-163` even explicitly avoids writing, which underlines that the command was scoped to catalog safety, not to end-to-end build.

**Consequences for #70's criteria:**

- Single-file grain=model cannot be achieved with `refresh` alone — the store file remains missing until backfill runs.
- SCD1 latest-only + avg pricing (backfill merge + pricing avg) never executes.
- Token/storage hypothesis (deduped store vs 8 MB catalog) cannot be validated without the extra steps.

The gap is a CLI integration gap, not a capability gap: all three steps work in isolation; only wiring is absent.

---

## 5. Store dedup file `data/model_info_store.json` — designed schema, key, pricing, merge, but empty/not seeded

### 5a. Location and committed status

`RECOMMENDED_STORE_PATH = "data/model_info_store.json"` `src/llm_discovery/model_info_store.py:756`, `RECOMMENDED_STORE_PATH_OBJ` `src/llm_discovery/model_info_store.py:800`. The file is documented as a committed snapshot (JSON, atomic write with .bak) `src/llm_discovery/model_info_store.py:683-684,791-796`. Alternatives rejected in code: `data/model_cache.yaml` (gitignored) rejected because it hides drift; YAML committed rejected due to quoting / atomicity tradeoffs `src/llm_discovery/model_info_store.py:760-763`. Wrapper payload is `{"version":1, "models":{…}}` `src/llm_discovery/model_info_store.py:903-905` with `STORE_FILE_VERSION=1` `src/llm_discovery/model_info_store.py:799`.

On disk today: **missing** (no file at that path). This is consistent with `data/` being gitignored — the exception `!data/model_info_store.json` `src/llm_discovery/.gitignore:22` would allow it, but nothing has seeded it.

### 5b. Key: `normalize_store_key`

Defined at `src/llm_discovery/model_info_store.py:47-125` (spec references issue #66). Behavior:

- Lowercases unconditionally (`raw = model_id.strip().lower()` `src/llm_discovery/model_info_store.py:97`)
- Strips trailing free markers `[:/_-]free` case-insensitive, before and after stepfun mapping (`re.sub(r"[:/_-]free$", "", raw)` `src/llm_discovery/model_info_store.py:99,103,122`)
- Stepfun→step normalization: `stepfun-`→`step-`, `stepfun/`→`step/` (`_normalize_model_id_stepfun` `src/llm_discovery/model_info_store.py:38-44`, applied at `src/llm_discovery/model_info_store.py:101,119`)
- Strips provider prefix: last segment after `/` (`slug = raw.rsplit("/",1)[-1]` `src/llm_discovery/model_info_store.py:107`), plus colon handling for `gpt-4o:free` (`slug.split(":")` check `src/llm_discovery/model_info_store.py:111-117`)
- Trims dangling punctuation (`strip("-_./:")` `src/llm_discovery/model_info_store.py:124`)
- Does **not** strip vendor suffixes `-contributor` / `-next` at key level (`src/llm_discovery/model_info_store.py:65-70,88-89`): `muse-spark-1.2-contributor` stays distinct from `muse-spark-1.2` to avoid silent collisions. Alias coalescing would require `model_matching.normalize_model_id` first plus opt-in helper `normalized_key_with_matcher()` `src/llm_discovery/model_info_store.py:128-149`.

Examples in docstring: `"openai/gpt-4o:free" → "gpt-4o"`, `"stepfun/step-2.5-free" → "step-2.5"` `src/llm_discovery/model_info_store.py:83-86`.

Classifiers long recovered was in the airplay eyeballs. Referencing all constraints tracked in the segment.

### 5c. Schema

Core record `ModelInfoRecord` `src/llm_discovery/model_info_store.py:458-566` (trimmed from `ProviderBatchWriter._to_record` `src/llm_discovery/model_info_store.py:463-467`):

- `aa_model_id`, `aa_score`, `coding_score`, `benchmarks: BenchmarkSnapshot`, `evidence: list[str]`, `evidence_level`, `confidence`, `tier`, `pricing: PricingSnapshot`, `_meta: StoreMeta` `src/llm_discovery/model_info_store.py:477-488` — verified vs actual poetry verification utilized.
- Omits per-provider `decision/drop/error`, `evaluated_at` (moved into `_meta`), `stage`, provider name (provenance goes to `_meta.source_providers`) — Architectural simple and easy knowing enterprise sourcing.
Biographical instruction amplified.

Supporting types:

- `BenchmarkSnapshot` `src/llm_discovery/model_info_store.py:357-382` (scores, raw_benchmarks, benchmark_coverage, coverage_with_supplements)
- `PricingSnapshot` `src/llm_discovery/model_info_store.py:416-455` (blended/input/output + `per_provider_overrides`, compat aliases `price_1m_blended_3_to_1` etc.)
- `StoreMeta` `src/llm_discovery/model_info_store.py:385-413` (first_seen, last_updated, source_providers[], source_evidence_levels[], version)

Canonical JSON example is in-code: `STORE_SCHEMA_DOC` `src/llm_discovery/model_info_store.py:683-723` (shows key `muse-spark-1.2` with all fields + `_meta`) and YAML view `EXAMPLE_YAML_SNIPPET` `src/llm_discovery/model_info_store.py:725-753`.

Field inclusion matrix by evidence level `FIELD_INCLUSION_MATRIX` `src/llm_discovery/model_info_store.py:169-218`, cacheable gate `CACHEABLE_LEVELS={"strong","moderate"}` `src/llm_discovery/model_info_store.py:163`. `should_cache(level, confidence)` returns True only for strong/moderate `src/llm_discovery/model_info_store.py:230-237`; `evidence_level_rank()` gives ordering `src/llm_discovery/model_info_store.py:240-242`.

### 5d. Pricing aggregation

Thresholds (`src/llm_discovery/model_info_store.py:249-251`):

```python
PRICING_OUTLIER_BLEND_THRESHOLD = 0.20  # $/1M
PRICING_OUTLIER_IO_THRESHOLD = 0.15
PRICING_OUTLIER_RATIO = 0.50  # >50% from median
```

Outlier predicate `is_pricing_outlier()` `src/llm_discovery/model_info_store.py:254-268` checks blended ratio + absolute threshold and io pair if provided; `aggregate_pricing(observations)` `src/llm_discovery/model_info_store.py:271-351` normalizes to `{blended,input,output,provider}` (with AA raw keys fallback `price_1m_blended_3_to_1` etc.), single observation stored as-is (`per_provider_overrides:{}` `src/llm_discovery/model_info_store.py:300-307`), n≥2 outliers moved to `per_provider_overrides`, mean of non-outliers returned, and if all outliers degenerate case keeps all `src/llm_discovery/model_info_store.py:340-342`.

### 5e. Merge

`merge_records(existing, incoming)` `src/llm_discovery/model_info_store.py:612-677`:

- Per-field best-of + gap-fill: if existing null and incoming has value, incoming wins (`if e_val is None and i_val is not None: return i_val` `src/llm_discovery/model_info_store.py:631-632`); both present → higher `evidence_level_rank` wins `src/llm_discovery/model_info_store.py:636-639`, tie → higher `confidence` `src/llm_discovery/model_info_store.py:642-645`, tie → newer `last_updated` `src/llm_discovery/model_info_store.py:648-650`
- Benchmarks union-max per key `_benchmark_union_max` `src/llm_discovery/model_info_store.py:572-609` (max score per canonical key, dedup raw_benchmarks by string repr, max coverage)
- Pricing at merge is gap-fill only; true averaged pricing is done at aggregation/backfill step (`pricing via gap-fill only, aggregated pricing handled at store-level` `src/llm_discovery/model_info_store.py:618,663`)
- Provenance: union sorted `source_providers` / `source_evidence_levels`, `last_updated = max(...)`, `first_seen` preserved, `version = max(...)` `src/llm_discovery/model_info_store.py:664-673`

### 5f. Why empty today

- No committed snapshot exists because nothing has ever written it in this checkout; backfill has not been run (no stats output found on disk, file missing). The code path is fully implemented but not triggered by any CLI command or CI.
- Backfill's seeding assumptions show the store is designed to be seeded from the 17 existing YAMLs — but those YAMLs today are mostly empty/error stubs (14 files are 388–404 B with only `error:` lists; only `agnes.yaml` and `nararouter.yaml` have `keep` entries). A backfill run would ingest few deduplicated strong/moderate records (estimated 10–20 unique models) until discovery is re-run with valid judge keys.

---

## 6. Token/storage cost at 100–200 providers scale (estimate from current sizes)

Measured anchors (2026-09-04):

- Catalog snapshots (static, provider-count invariant): 743 KB + 8,301 KB + 377 KB ≈ **9.4 MB** total `data/*.json` (plus 5.6 KB artifact). This does **not** grow with provider count — it is upstream-size bounded.
- Reports (scales with providers, per-provider YAML): 51 KB across 17 providers. Excluding the bloated `cloudflare.yaml` error dump (30 KB of 401-style errors), realistic report cost is:
  - Success case: `agnes.yaml` 5.9 KB for 6 keep entries (~1 KB per keep model), `nararouter.yaml` 9.3 KB for ~5 keep entries (~1.8 KB per keep). Average ≈ **1–2 KB per kept model**, ~4–6 keep per provider → ~**5–10 KB per provider YAML** when evaluation succeeds.
  - Failure/unauth stub: ~400 B per provider (what 14/17 files are today). This underestimates live cost but shows per-provider floor.

### Projections

| Providers | Reports YAML total | + snapshots | Raw total (reports + snapshots) | Deduped store alternative |
|-----------|-------------------|-------------|----------------------------------|---------------------------|
| 17 today | 51 KB (many stubs) | 9.4 MB | ~9.5 MB | ~0 (empty) |
| 17 if all successful | ~85–170 KB (17 × 5–10 KB) | 9.4 MB | ~9.5–9.6 MB | ~40–90 KB — see below |
| 100 providers | ~500 KB–1 MB | 9.4 MB | ~9.9–10.4 MB | ~250–600 KB |
| 200 providers | ~1–2 MB | 9.4 MB | ~10.4–11.4 MB | ~500 KB–1.2 MB |

**Store sizing basis:** today `agnes.yaml` + `nararouter.yaml` have ~11 keep entries deduped by `normalize_store_key` → expect ~8 unique models. Their on-disk YAML is ~15 KB; JSON deduped with the `ModelInfoRecord` shape (same fields minus `evaluated_at` per-provider duplication, plus compressed `_meta`) would be ~**5–8 KB** for those 8 models (~0.7–1 KB per unique model JSON record). The schema doc example `src/llm_discovery/model_info_store.py:684-722` (one record with full fields) serializes to ~0.9 KB minified.

At 100–200 providers, unique model count grows sublinearly because many providers resell the same models (OpenAI, Qwen, Llama, etc.). Empirical bound from `models_dev_catalog.json` (212 providers in that catalog host only 364 distinct models `du -sb` above) → unique model ceiling ≈ 300–500 even at 200 providers locally. So store scales with **unique models**, not providers: 300 models × 1 KB ≈ 300 KB + header.

**Per-criterion read:**

- **(1) Efficient storage token-friendly R/W** — Snapshots dominate (9.4 MB). Deduped store is 3–8× smaller than reports for the same logical data, and shard-free. Provider YAMLs are the token-heavy surface for LLMs to read: 17 small YAML reads vs one JSON store read. At 200 providers, a single `model_info_store.json` (~500 KB) is far more token-friendly than 200 YAML files (1–2 MB + overhead of multi-file read). Reports YAMLs also carry `error` bloat (cloudflare proves 30 KB from failed auth alone) — store excludes errors entirely.
- **(2) One-command builds all** — not met today; see §4.
- **(3) Single-file grain=model_name feasible** — yes. Store granularity is already `{key: record}` map `src/llm_discovery/model_info_store.py:903-905`; no sharding needed. 500 KB JSON remains comfortably below typical context windows.
- **(4) SCD1 latest-only + avg pricing** — not realized (store empty); mechanism exists (`merge_records` + `aggregate_pricing`) but not exercised.

**Storage vs token nuance:** 8.3 MB `models_dev_catalog.json` is the cost elephant. It is the intended trade-off: local deterministic catalog avoids network / web search at evaluation time (benchmark cache built without network), but it dwarfs discovery output. The store does not shrink it — it shrinks the per-provider report duplication on top.

---

## 7. Raw dumps vs unified store

| Raw dumps (today) | Unified store (designed) |
|-------------------|--------------------------|
| `nararouter_raw.json`, `nararouter_raw_full.json` (10 KB each), `nararouter_pricing_raw.json` (42 KB), `data/artifacts/nararouter_plans.json` (5.6 KB) | `data/model_info_store.json` (`{version, models:{}}` `src/llm_discovery/model_info_store.py:903-905`) |
| Producer: unknown/manual (no writer in `refresh.py` or `discovery.py` other than the 5.6 KB artifact via `discovery.py:144-148`) | Producer: `backfill.py:46-186` or future in-pipeline `ModelInfoStore.put()` |
| Grain: per-upstream response dump (provider-specific raw shape, not normalized) | Grain: deduped model name via `normalize_store_key` `src/llm_discovery/model_info_store.py:47-125` |
| Format: JSON dumps of fetch responses (no schema) | Format: typed store with `ModelInfoRecord` / `BenchmarkSnapshot` / `PricingSnapshot` / `StoreMeta` `src/llm_discovery/model_info_store.py:357-514` |
| Retention: gitignored under `data/` `src/llm_discovery/.gitignore:21` — not auditable in PRs | Retention: committed snapshot via `!data/model_info_store.json` `src/llm_discovery/.gitignore:22` — auditable diff, versioned via `STORE_FILE_VERSION=1` `src/llm_discovery/model_info_store.py:799` |
| Use today: debugging / NaraRouter hard-coded allowlist fallback `NARAROUTER_FREE_SNAPSHOT` `src/llm_discovery/discovery.py:100-110` | Use designed: cross-provider reuse cache for `evaluate_model` seam (`prototype #67`), gap-fill, avg pricing |

**Should raw dumps merge into the store?** No. They are upstream fetch artifacts and observation logs; the store is evaluation-derived, normalized, and scored. Conflating them would force raw response shapes into the typed store schema. The right relationship is: raw dumps → discovery (filtered via allowlist) → `results.yaml` → store. Retention of raw dumps optionally as `data/artifacts/*` is defensible for audit, but not as part of the store file. Keeping both layers — artifact logs under `data/artifacts/` (gitignored) and the store's committed snapshot — preserves traceability without bloating the store.

**Current gap:** the three `nararouter_*.json` dumps have no provenance (no writer recorded, no fetched-at, no .bak rotation). If they are to be kept as artifacts, they should be explicitly produced by a discoverable writer (or documented as manual) to avoid silent staleness.

---

## 8. Cross-cutting findings for Wayfinder #70

1. **Format uniformity:** snapshots are JSON with atomic writes + .bak (`src/llm_discovery/refresh.py:25-45`); store is JSON with atomic writes (`src/llm_discovery/model_info_store.py:831-849`); reports are YAML with no atomicity (`src/llm_discovery/results.py:202`). Mixing JSON for machine-managed layers and YAML for human-reviewed keep-lists is intentional (reviews cite YAML for keep-list usability in `results.py:46-47` header).

2. **Provider config is the source of truth for scaling:** `config/providers.yaml` lists 17 providers today (`src/llm_discovery/config/providers.yaml` entire file; 1951 B), but `discover_all_providers` loops `config.providers` `src/llm_discovery/pipeline.py:505` so 100–200 is a config-only change. No code limits provider count other than network/timeout.

3. **One-command wiring is the blocking decision for #70:** all three layers work; only CLI integration is missing. A minimal `llm-discovery sync` (refresh → discover_all → backfill) would close criteria (2) and exercise (3)–(4).

4. **Backfill stats are the seed verifier:** `backfill()` returns `{files_processed, total_keep_records, unique_models, merged_conflicts, pricing_avgs, outliers, weak_skipped, evaluated_at_range, store_size}` `src/llm_discovery/backfill.py:172-186`. These should be logged in CI as the litmus test for store health after any bulk refresh.

5. **Freshness still open:** `is_stale()` `src/llm_discovery/model_info_store.py:802-815` defaults to never stale; Wayfinder #70's ">2-week freshness + avg pricing" and map #63's "cache invalidation & freshness" remain decisions, not code. The store can honor any TTL via `get_if_fresh(ttl_days)` `src/llm_discovery/model_info_store.py:938-944` once #68 locks the value.

---

## Appendix — verification steps

```bash
ls -R data; ls -lh data/*.json; ls -lh data/results/*.yaml; cat .gitignore
python3 -c "import json; d=json.load(open('data/artificial_analysis_models.json')); print(len(d['models']))"
python3 -c "import json; d=json.load(open('data/models_dev_catalog.json')); print(len(d['models']), len(d['providers']))"
ls -lh data/model_info_store.json   # missing — store not seeded
grep -rn "data/" src --include="*.py" | grep -E "DATA_DIR|data/results|model_info"
gh issue view 63 --json body   # map + children 64-69
gh issue view 70 --json body   # destination + not-yet-specified
```

---

*Primary-source audit, 2026-09-04 — all file:line citations point to the checkout at audit time. Re-run `ls -R data` and `grep -rn DATA_DIR src` after any refresh/discovery/backfill to refresh this doc.*
