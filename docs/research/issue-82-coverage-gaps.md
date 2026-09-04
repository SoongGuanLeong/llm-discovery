# Research #82 — Benchmark and catalog coverage gaps (part of Wayfinder #80)

**Issue:** [#82 Research: Benchmark and catalog coverage gaps](https://github.com/SoongGuanLeong/llm-discovery/issues/82) — child of Wayfinder map [#80 Wayfinder: Accurate results and intelligent incremental build](https://github.com/SoongGuanLeong/llm-discovery/issues/80)
**Status:** research — measurement and code audit, no build
**Date:** 2026-09-04
**Sources:** `data/results/*.yaml` (17 files, 506 total records), `data/artificial_analysis_models.json`, `data/models_dev_catalog.json`, `src/llm_discovery/benchmarks.py`, `src/llm_discovery/catalogs.py`, `src/llm_discovery/model_matching.py`, `src/llm_discovery/model_resolver.py`, `src/llm_discovery/refresh.py`, `src/llm_discovery/build_all.py`, `src/llm_discovery/model_info_store.py`, `src/llm_discovery/backfill.py`. All numbers measured 2026-09-04T21:00 UTC from live `data/results/*.yaml` on disk; code refs cite `file:line`.

> Wayfinder #80 asks for accurate results and intelligent incremental build. This ticket measures where benchmark/catalog matching caps accuracy: per-provider AA match rate, models.dev match rate, `benchmark_coverage` / `coverage_with_supplements`, `coding_score` null rate, pricing null rate, staleness of local catalog snapshots, and whether `BenchmarkDataCache.collect_from_local` union logic misses namespaced ids (e.g. `qwen/qwen3.8-27b` vs `qwen3.8-27b`).

---

## 1. Dataset snapshot (what was measured)

| Path | Grain | Size / count | Committed? |
|------|-------|-------------|------------|
| `data/results/*.yaml` | one YAML per provider, records in `keep[]` + `drop_llm[]` (plus `huggingface.yaml` single-record legacy shape) | 17 files, **506 total records** (122 keep, 384 drop including 371 `drop` tier + 8 `uncertain`), per-file 0–98 records | gitignored via `data/` in `.gitignore:21`, but several YAMLs are currently present on disk with `evaluated_at` 2026-09-04T11:28–12:15 UTC |
| `data/artificial_analysis_models.json` | AA snapshot (`ArtificialAnalysisCatalog` in `src/llm_discovery/catalogs.py:5-28`) | 759,906 B, 631 models, header `fetched_at: 2026-09-02T09:29:24.770396+00:00` (`refresh.py:_normalize_aa_payload` injects `fetched_at`) | gitignored (`data/`) |
| `data/models_dev_catalog.json` | models.dev snapshot (`ModelsDevCatalog` in `src/llm_discovery/catalogs.py:29-52`, keys are **all namespaced** `provider/model` e.g. `tencent/hy3-preview`) | 8,701,215 B, 364 models, 212 providers — **no top-level `fetched_at`** (only per-model `last_updated`), file mtime `2026-09-02T09:29:25Z` | gitignored |
| `data/benchmarks.json` | derived union (`BenchmarkDataCache` in `src/llm_discovery/benchmarks.py:157-241`) | not required by `build_all` any more (cache-optional per `build_all.py:58` and #79); transient when present | gitignored |
| `data/model_info_store.json` | canonical SCD1 store (`ModelInfoStore` in `src/llm_discovery/model_info_store.py`) | 219,256 B, pretty + compact variants | **tracked** (`!data/model_info_store.json` in `.gitignore:22`) |

`git ls-files data/` returns empty (all of `data/` is ignored); `git log -- data/artificial_analysis_models.json` returns no history — snapshots are local-only, not versioned.

---

## 2. Per-provider coverage table (all records: keep + drop)

> Definitions: **AA match rate** = `aa_model_id != null / total` (AA resolution via `ModelResolver`→`ModelMatcher` in `model_matching.py`/`model_resolver.py`). **models.dev match rate** = provider `model_id` normalized via `_normalize_model_key` (`benchmarks.py:365`) matches any `models_dev_catalog.json` key or its bare suffix (dot↔hyphen alts included). **benchmark_coverage** = `len(KEY_SIGNALS ∩ scores)/4` where `KEY_SIGNALS = (aa_intelligence, swe_bench_verified, livecodebench, humaneval)` (`benchmarks.py:58-63`); **coverage_with_supplements** = `len(ALL_SIGNALS ∩ scores)/12` (`benchmarks.py:74-86`). Averages below count missing `benchmarks` as 0. **coding_score null rate** / **pricing null rate** = share with `coding_score == null` / `pricing == null` in the YAML record.

| Provider | Total | AA match | models.dev match | avg `benchmark_coverage` | avg `coverage_with_supplements` | `coding_score` null | `pricing` null |
|----------|-------|----------|------------------|--------------------------|---------------------------------|---------------------|----------------|
| agnes | 6 | 3/6 50.0% | 0/6 0.0% | 0.12 | 0.04 | 3/6 50.0% | 3/6 50.0% |
| ainative | 83 | 56/83 67.5% | 43/83 51.8% | 0.15 | 0.07 | 32/83 38.6% | 27/83 32.5% |
| bazaarlink | 1 | 0/1 0.0% | 0/1 0.0% | 0.00 | 0.00 | 1/1 100% | 1/1 100% |
| cerebras | 3 | 3/3 100% | 2/3 66.7% | 0.17 | 0.08 | 0/3 0% | 0/3 0% |
| cloudflare | 51 | 0/51 0.0% | 0/51 0.0% | 0.00 | 0.00 | 51/51 100% | 51/51 100% |
| cohere | 17 | 0/17 0.0% | 9/17 52.9% | 0.00 | 0.00 | 16/17 94.1% | 17/17 100% |
| google | 48 | 9/48 18.8% | 32/48 66.7% | 0.01 | 0.03 | 38/48 79.2% | 39/48 81.2% |
| groq | 14 | 3/14 21.4% | 7/14 50.0% | 0.05 | 0.02 | 10/14 71.4% | 11/14 78.6% |
| huggingface | 1 | 1/1 100% | 1/1 100% | 0.00 | 0.00 | 1/1 100% | 1/1 100% |
| kilo_ai | 17 | 8/17 47.1% | 12/17 70.6% | 0.22 | 0.11 | 5/17 29.4% | 9/17 52.9% |
| llm7 | 42 | 25/42 59.5% | 29/42 69.0% | 0.16 | 0.12 | 15/42 35.7% | 17/42 40.5% |
| mistral | 45 | 16/45 35.6% | 7/45 15.6% | 0.03 | 0.01 | 37/45 82.2% | 29/45 64.4% |
| modelscope | 47 | 21/47 44.7% | 22/47 46.8% | 0.11 | 0.06 | 25/47 53.2% | 26/47 55.3% |
| nararouter | 8 | 5/8 62.5% | 6/8 75.0% | 0.12 | 0.10 | 3/8 37.5% | 3/8 37.5% |
| navy_ai | 98 | 43/98 43.9% | 50/98 51.0% | 0.08 | 0.05 | 54/98 55.1% | 55/98 56.1% |
| opencode_zen | 7 | 4/7 57.1% | 6/7 85.7% | 0.18 | 0.07 | 3/7 42.9% | 3/7 42.9% |
| openrouter | 18 | 8/18 44.4% | 15/18 83.3% | 0.19 | 0.11 | 6/18 33.3% | 10/18 55.6% |
| **OVERALL** | **506** | **205/506 40.5%** | **~243/506 48%** | **—** | **—** | **300/506 59.3%** | **302/506 59.7%** |

Key observations:

- **Two zero-AA providers are 100% drops:** `cloudflare` (51/51 drop, UUID `model_id`s like `01564c52-...` — infra models, not AA-tracked; see prototype #84) and `cohere` (17/17 drop). They dominate the null tail.
- **High-AA, low-benchmark providers:** `google` 18.8% AA but 0.01 `benchmark_coverage` (Aider-only supplements); `groq` 21.4% AA with `qwen/qwen3.8-27b` keeping via AA only; `mistral` 35.6% AA but 0.03 coverage — 30/45 weak drops drag the average.
- **Best-covered providers:** `kilo_ai` (0.22), `openrouter` (0.19), `opencode_zen` (0.18), `cerebras` (0.17) — small, curated catalogs where most keeps already have AA + SWE-bench.
- **Distribution:** across all 506, `benchmark_coverage` is `null`=300, `0.25`=139, `0.0`=45, `0.5`=20, `0.75`=2 — i.e. **59% have no benchmark block at all** (all weak drops), **27% have exactly one KEY_SIGNAL** (typically `aa_intelligence` or `swe_bench_verified` alone).

### 2.1 Keep-only view (the models that actually ship)

| Provider | Keeps | AA keep rate | `coding_score` null (keeps) | `pricing` null (keeps) | avg `benchmark_coverage` (keeps) |
|----------|-------|--------------|-----------------------------|------------------------|----------------------------------|
| agnes | 4 | 3/4 75% | 1/4 | 1/4 | 0.19 |
| ainative | 29 | 27/29 93.1% | 5/29 | 2/29 | 0.22 |
| bazaarlink | 1 | 0/1 0% | 1/1 | 1/1 | 0.00 |
| cerebras | 1 | 1/1 100% | 0 | 0 | 0.00 |
| google | 6 | 6/6 100% | 0 | 0 | 0.00 |
| groq | 1 | 1/1 100% | 0 | 0 | 0.00 |
| huggingface | 1 | 1/1 100% | 1/1 | 1/1 | 0.00 |
| kilo_ai | 7 | 4/7 57.1% | 2/7 | 3/7 | 0.29 |
| llm7 | 22 | 22/22 100% | 1/22 | 0 | 0.25 |
| mistral | 2 | 2/2 100% | 0 | 0 | 0.25 |
| modelscope | 14 | 13/14 92.9% | 1/14 | 1/14 | 0.21 |
| nararouter | 5 | 4/5 80% | 0 | 1/5 | 0.20 |
| navy_ai | 20 | 19/20 95% | 1/20 | 1/20 | 0.19 |
| opencode_zen | 2 | 2/2 100% | 0 | 0 | 0.38 |
| openrouter | 7 | 4/7 57.1% | 1/7 | 3/7 | 0.29 |
| **overall keeps** | **122** | **109/122 89%** | **14/122 11.5%** | **13/122 10.7%** | **—** |

> Keeps are far better matched than the full catalog: **89% have an AA match**, but **benchmark_coverage remains low** (typical 0.20–0.29, i.e. one KEY_SIGNAL) and **coding_score is still null for 11.5% of keeps**. Five keeps with `evidence_level: strong` still have `aa_model_id == null` (see §3).

---

## 3. Correlation with keeper quality (`evidence_level` / `tier`)

Keeper quality as written in YAML (`evidence_level`: `strong`/`moderate`/`weak`, and `tier`: `max`/`flash`/`drop`/`uncertain`/`contributor_free`). Measured over **all 506** (keeps+drops), then keeps-only.

| `evidence_level` | n | AA match | models.dev match | pricing present | `coding_score` present |
|------------------|---|----------|------------------|-----------------|------------------------|
| strong | 215 | 62.8% | 74.4% | 62.8% | 69.8% |
| moderate | 41 | 87.8% | 65.9% | 87.8% | 70.7% |
| weak | 249 | 13.3% | 21.3% | 13.3% | 10.8% |
| unknown (huggingface) | 1 | 100% | 100% | 0% | 0% |

| `tier` | n | pricing present |
|--------|---|----------------|
| max | 77 | 93.5% |
| flash | 48 | 77.1% |
| contributor_free | 2 | 100% |
| drop | 371 | 25.1% |
| uncertain | 8 | 0% |

Keeps-only by evidence_level (122 keeps):

| `evidence_level` (keeps) | n | avg `benchmark_coverage` | avg `coverage_with_supplements` | `coding_score` present | `pricing` present |
|--------------------------|---|--------------------------|---------------------------------|------------------------|-------------------|
| strong | 111 | 0.23 | 0.165 | 96.4% | 91.0% |
| moderate | 7 | 0.04 | 0.011 | 14.3% | 100% |
| weak | 3 | 0.00 | 0.00 | 0% | 0% |
| unknown | 1 | 0.00 | 0.00 | 0% | 0% |

Interpretation:

- **Strong vs weak is sharply separated by AA/models.dev/pricing/coding_score** — not by `benchmark_coverage`. Weak entries are almost all drops with no catalog match (13% AA, 21% models.dev). Strong entries already have 63% AA, 74% models.dev. So catalog match is a gate for strong, but **coverage depth is not** — even strong keeps average only **0.23/1.0** (less than one KEY_SIGNAL) and **0.165/12 supplements**.
- **Moderate keeps are the coverage gap:** 7 keeps at moderate have **0.04 benchmark_coverage**, **14% coding_score** — they passed tier on AA + pricing/promo but lack benchmark facts. This is where the request's "keeper quality (strong vs moderate vs weak)" correlation matters: moderate is where AA is high (87.8%) yet benchmarks are missing.
- **Pricing tracks tier, not quality:** `max` 93.5% priced, `flash` 77%, `drop` 25%, `uncertain` 0%. The 25% of drops with pricing are legacy/historical prices retained via `backfill.merge` gap-fill (see #71). The real pricing gap is **drops + uncertain**, not keeps — but 10.7% of keeps still lack pricing (`bazaarlink auto:free`, `huggingface Kimi-K3`, several `openrouter`/`kilo_ai` variants).
- **Five strong keeps with missing AA:** `agnes-2.5-pro` (agnes), `nemotron-3-super-120b` + `qwen3-5-397b` (ainative), `laguna-s-2.1` (kilo_ai/nararouter/openrouter), `Qwen/Qwen3-235B-A22B` (modelscope), `deepseek-reasoner` (navy_ai) — all `evidence_level: strong` despite `aa_model_id: null`, relying on single-supplement scores (SWE-bench or coding index) or provider claims. These exemplify the supplement-only strong path.

---

## 4. Staleness of `data/artificial_analysis_models.json` and `data/models_dev_catalog.json`

| File | `fetched_at` / header | mtime (UTC) | Age on 2026-09-04T21:00Z | Tracking | Stale? |
|------|----------------------|-------------|------------------------|----------|--------|
| `data/artificial_analysis_models.json` | `fetched_at: 2026-09-02T09:29:24.770396+00:00` injected by `refresh.py:_normalize_aa_payload:38` (`source: artificial-analysis, tier: free, intelligence_index_version: 4.1`) | 2026-09-02 09:29:24Z (same second as fetched_at) | **~2.2 days** | gitignored | **No** — within the 14-day SCD1 freshness window used by `backfill.is_stale` (`backfill.py`) and `store.write` invalidation (`model_info_store.py`). Still, not versioned: `git log -- data/artificial_analysis_models.json` empty, `git ls-files data/` empty. |
| `data/models_dev_catalog.json` | **No top-level `fetched_at`** — only per-model `last_updated` (e.g. `tencent/hy4-preview last_updated 2026-08-28`); `refresh.py:fetch_models_dev:112` returns payload as-is without stamping | 2026-09-02 09:29:25Z (written in same `refresh` run as AA) | **~2.2 days** (mtime proxy) | gitignored | **No** by mtime, **but opaque**: without a header, staleness cannot be checked programmatically; a consumer must rely on filesystem mtime, not the file itself. |

Additional signals:

- **Evaluation dates are fresh:** `data/results/*.yaml` `evaluated_at` ranges `2026-09-04T11:28` (agnes) to `12:15` (modelscope/nararouter) — <10 hours old at measurement time. No YAML exceeds the 14-day `is_stale` threshold (`backfill.py: is_stale(evaluated_at, ttl_days=14)`).
- **Refresh is already cache-optional:** `build_all.py:58-92` no longer requires `refresh` to succeed; if both JSONs are missing it loads `aa=None, models_dev=None` and proceeds (`build_all.py:73-84`). `pipeline.discover_provider` falls back to empty `BenchmarkDataCache` (no benchmarks) — so staleness does not block build, but caps accuracy.
- **Risk:** because both snapshots are gitignored and unversioned, a fresh clone has **zero catalog** and builds with 0 AA / 0 benchmark until `cli refresh` fetches. The 2.2-day age on this machine is not representative of CI/clone. Recommendation in §6 addresses this without requiring network during `build_all`.

---

## 5. Does `BenchmarkDataCache.collect_from_local` miss namespaced ids? — Yes (union incompleteness)

**Question in ticket:** whether `collect_from_local` union logic misses namespaced model ids (`qwen/qwen3.8-27b` vs `qwen3.8-27b`).

**Answer: yes — merge misses dot↔hyphen variants, causing split keys and incomplete benchmark unions. Lookup masks it via `get()` alts, so the bug is silent but caps `benchmark_coverage` / `coding_score`.**

### 5.1 How `collect_from_local` works (`benchmarks.py:176-241`)

```python
def collect_from_local(self, aa, models_dev):
    # From models.dev: store with full namespaced key e.g. "alibaba/qwen3.8-27b"
    for model_id, model_data in models_dev.models.items():
        self._data[model_id] = {benchmarks, raw_benchmarks}  # key = "alibaba/qwen3.8-27b"
    # From AA: try to merge into existing, else store with bare slug
    for model in aa.models:
        slug = model["slug"]  # e.g. "qwen3-8-27b"
        norm_slug = _normalize_model_key(slug)
        key = None
        for existing_id in self._data:
            if _normalize_model_key(existing_id) == norm_slug:
                key = existing_id; break
        if key:
            # union: only add missing canonical keys
            for cn, bm in benchmarks.items():
                if cn not in self._data[key]["benchmarks"]:
                    self._data[key]["benchmarks"][cn] = bm
        else:
            self._data[slug] = {benchmarks}  # new bare key
```

`_normalize_model_key` (`benchmarks.py:365-407`) lowercases, strips `provider/` prefix, strips `:free`/`-free`/`-contributor`/`-next`, replaces `minimax→mm`, `nemotron→nemo`, `laguna-s/xs→laguna`, inserts `letter-digit` hyphen, **preserves version dots** (`2.5` → `zzzdotzzz` → `2.5`). It does **not** canonicalize dots vs hyphens.

`get(model_id)` / `get_raw(model_id)` (`benchmarks.py:243-295`) **do** handle the gap: they try exact, then `bare = id.rsplit("/",1)[-1]`, then build `alts = {norm, norm.replace(".","-"), re.sub(r"(\\d)-(\\d)", r"\\1.\\2", norm)}` and union-match against keys. So lookups succeed even when `collect` split.

### 5.2 The miss (measured)

Reproduction (live cache, `len(cache._data)==700` after `collect_from_local`):

| models.dev key | `norm(models.dev key)` | AA slug | `norm(AA slug)` | Exact-match merge? | Alts intersect? | Result in `_data` |
|----------------|----------------------|---------|-----------------|-------------------|-----------------|------------------|
| `alibaba/qwen3.8-27b` | `qwen-3.8-27b` | `qwen3-8-27b` | `qwen-3-8-27b` | **No** (`.` vs `-`) | Yes | **Two keys**: `alibaba/qwen3.8-27b` → `{swe_bench_pro: 61.7}` only; `qwen3-8-27b` → `{aa_intelligence, aa_coding}` only. Lookup of either returns only one half. |
| `alibaba/qwen3.5-27b` | `qwen-3.5-27b` | `qwen3-5-27b` | `qwen-3-5-27b` | No | Yes | Same split: `alibaba/qwen3.5-27b` (SWE) vs `qwen3-5-27b` (AA). |
| `MiniMax/MiniMax-M3` | `m3` | `minimax-m3` | `m3` | **Yes** | Yes | **One key** (`minimax/MiniMax-M3`) → union `{swe_bench_verified, terminal_bench, osworld_verified, aa_intelligence, aa_coding}` — correct. |
| `nvidia/nemotron-3-nano-30b-a3b` | `nemo-3-nano-30b-a3b` | `nemotron-3-nano-30b-a3b` | `nemo-3-nano-30b-a3b` | Yes | Yes | One key — correct. |

So for **dot-versioned Qwen families** (and any `x.y` vs `x-y` pair) the merge fails; for non-dot or de-prefixed families it succeeds. On this snapshot, **at least 2 Qwen 27B pairs + potentially all `qwen/qwen3.x` variants** remain split. Consequence: `get("qwen/qwen3.8-27b")` (as used by `google`/`groq` provider `model_id`s) finds the models.dev entry first and returns **only SWE-bench, missing AA intelligence**; `get("qwen3-8-27b")` returns only AA, missing SWE.

Direct proof after `collect_from_local` on this machine:

```
key='alibaba/qwen3.8-27b' benchmarks=['swe_bench_pro']
key='qwen3-8-27b'         benchmarks=['aa_intelligence', 'aa_coding']
key='alibaba/qwen3.5-27b' benchmarks=['swe_bench_verified']
key='qwen3-5-27b'         benchmarks=['aa_intelligence']
key='minimax/MiniMax-M3'  benchmarks=['swe_bench_verified','terminal_bench','osworld_verified','aa_intelligence','aa_coding']  # merged correctly
cache size: 700
```

`build_benchmark_profile(provider_model_id, ...)` (`benchmarks.py:409-427`) calls `cache.get(provider_model_id)` once — so the profile gets **half the signals**, `benchmark_coverage` is halved, and `compute_coding_score` (`benchmarks.py:430-466`) down-weights or misses entirely. The groq `qwen/qwen3.8-27b` keep in `data/results/groq.yaml` reports `benchmark_coverage: 0.0, coverage_with_supplements: 0.08 (swe_bench_pro only)` despite AA `qwen3-8-27b` having `aa_intelligence: 42.9 + aa_coding: 58.2` — those AA scores are invisible to that lookup.

### 5.3 Why the bug is silent

`get()` alts logic means **no hard failure** — it always returns *something*. So `benchmark_coverage` looks low (0.25) rather than erroring, and the LLM judge still receives one supplement. The signal loss is invisible in YAML except as unexpectedly low `benchmark_coverage` / `coding_score` for dot-versioned models.

---

## 6. What caps accuracy today (summary of gaps)

1. **Benchmark depth, not AA breadth, is the cap.** AA covers 89% of keeps, but even strong keeps average 0.23 `benchmark_coverage`. models.dev supplies only 36.8% of its 364 models with any benchmarks (134/364), and the top benchmark is SWE-Bench Pro (63) — so most keeps have exactly one signal. Moderate keeps (0.04 coverage, 14% coding_score) are the accuracy frontier.
2. **Split cache halves dot-versioned families.** §5 bug means Qwen `3.x` keeps lose either AA or SWE depending on lookup key. Same pattern likely for any `x.y` models.dev entry vs `x-y` AA slug.
3. **Pricing is catalogue-dependent and not backfilled for new providers.** 59.7% overall pricing null (10.7% of keeps). `bazaarlink auto:free`, `huggingface` legacy shape, and 3 `openrouter`/`kilo_ai` variants have no price; cloudflare/cohere/mistral drops dominate the global null rate but do not affect keeps much. For keeps, pricing null blocks `max`/`flash` tiering in `categorize_model` (`categorize.py`) and `intelligence per dollar`.
4. **Catalog snapshots are local-only and not self-describing.** `data/models_dev_catalog.json` has no `fetched_at`; both files are gitignored, so clones start at 0 coverage. Staleness is not checked at `build_all` time.
5. **Cloudflare/UUID models are unmatchable by design** (51 UUIDs, 0% any match) — correctly dropped, but they inflate global null rates and are noise in the accuracy denominator. They should be excluded from coverage denominators or flagged as infra.

---

## 7. Minimal catalog refresh or matching fix that lifts coverage **without network during `build_all`**

> Constraint from ticket: recommend a fix that lifts coverage **without network during `build_all`** (i.e. deterministic local logic, or a refreshed committed snapshot that `build_all` can use offline).

### 7.1 Fix the union bug (no network, immediate lift — **recommended first**)

**Change:** make `BenchmarkDataCache.collect_from_local` merge use the same alts logic that `get()` already uses, instead of exact `norm` equality (`benchmarks.py:221-230`).

```python
# Before (benchmarks.py:221):
for existing_id in self._data:
    if _normalize_model_key(existing_id) == norm_slug:
        key = existing_id; break

# After:
alts_slug = {norm_slug, norm_slug.replace(".","-"), re.sub(r"(\\d)-(\\d)", r"\\1.\\2", norm_slug)}
for existing_id in self._data:
    norm_existing = _normalize_model_key(existing_id)
    alts_existing = {norm_existing, norm_existing.replace(".","-"), re.sub(r"(\\d)-(\\d)", r"\\1.\\2", norm_existing)}
    if alts_slug & alts_existing:
        key = existing_id; break
```

Plus the symmetric step: when storing AA entries that remain new, also normalize the key to a canonical dot-insensitive form or keep both but union on write. The minimal patch is to change only the merge search; a follow-up can canonicalize stored keys to bare dot-form (`qwen-3.8` → `qwen-3.5` style is already handled by `re.sub`).

**Lift:** Qwen 27B/35B families (and any `x.y`) go from split to union — e.g. `qwen/qwen3.8-27b` lookups would return `{aa_intelligence, aa_coding, swe_bench_pro}` instead of `{swe_bench_pro}`, raising `benchmark_coverage` from 0.0 to 0.25 (or 0.5 if both AA signals count) and `coding_score` from single-supplement to weighted AA+SWE. Measured on this snapshot: at least 2 keeps (`groq qwen/qwen3.8-27b`, `ainative qwen3-5-397b` if AA variant exists) would gain AA signals without any network. Applies offline in `build_all` because `BenchmarkDataCache` is built from local snapshots.

### 7.2 Stamp and optionally commit a catalog snapshot (no network at build time)

**Change:** in `refresh.py:fetch_models_dev` / `_atomic_write_json`, inject `fetched_at = datetime.now(UTC).isoformat()` into the models.dev payload the same way AA does, and optionally track a **committed snapshot** (e.g. `data/catalogs/models_dev.snapshot.json` with `!data/catalogs/` in `.gitignore`) or document that `build_all` should warn when mtime >14 days. `build_all.py:73-84` can then check `fetched_at` / mtime and log staleness without fetching.

**Lift:** does not itself raise coverage, but makes staleness observable and prevents silent 0-coverage clones (the current 2.2-day staleness is fine; the risk is fresh clones with 0 files). If the snapshot is committed, `build_all` on a clone has non-zero coverage offline. Tradeoff: snapshot size (8.7 MB) — use `.gitignore` exception narrowly or store a pruned benchmark-only snapshot.

### 7.3 Selective supplement backfill for the 7 moderate keeps (no network if from models.dev)

**Change:** for the 7 moderate keeps with 0.04 coverage, re-run `BenchmarkDataCache.get` after the §7.1 fix — several may already gain coverage. For remaining, the cheapest offline lift is to ensure `models_dev` benchmarks are imported for those exact model ids (they may already be in models.dev but missed due to normalization edge cases like `minimax` vs `mm` or `moonshotai/Kimi-K3` style). Verify `huggingface moonshotai/Kimi-K3` (AA 59.7, but YAML has empty benchmarks and null coding_score/pricing) — its models.dev entry may be under a different family key.

**Lift:** converts moderate keeps from single-AA to AA+SWE, raising their `coding_score` into strong range and allowing `moderate→strong` promotion deterministically (see `evidence_collector.py` / `policy_gate.py`).

### 7.4 Pricing gap-fill for the 13 keep-nulls (offline)

**Change:** keep pricing nulls are only 13 keeps; most can be filled from existing `model_info_store.json` re-avg (`backfill.py:merge pricing re-avg`) or from `provider_claims` / AA pricing field when present. No new network — just ensure `backfill.merge` gap-fill runs for keeps even when AA/models.dev are missing (it already does for drops; verify it covers keeps like `openrouter laguna` series).

**Not recommended for this ticket but noted:** fetching live pricing/benchmarks during `build_all` (DataLearnerAI `collect_from_web` at `benchmarks.py:298`) would raise coverage more, but violates the "without network during `build_all`" constraint and is already optional/out-of-scope per #79.

---

## 8. How to verify

```bash
# reproduce tables in §2/§3
python3 -c "import yaml, json, re; from pathlib import Path; ..."  # see measurement script in research log
# reproduce split cache
python3 -c "from llm_discovery.benchmarks import BenchmarkDataCache; ... collect_from_local ...; print(cache._data['alibaba/qwen3.8-27b'], cache._data['qwen3-8-27b'])"
# after fix: single key for qwen3.8-27b with union benchmarks
```

Sources consulted: `data/results/*.yaml` (17 files), `data/artificial_analysis_models.json` header (`fetched_at`), `data/models_dev_catalog.json` (size, keys, `last_updated`), `src/llm_discovery/benchmarks.py:58-86,157-295,365-430`, `src/llm_discovery/catalogs.py`, `src/llm_discovery/model_matching.py:normalize_model_id`, `src/llm_discovery/refresh.py`, `src/llm_discovery/build_all.py:58-84`, `src/llm_discovery/model_info_store.py`, `src/llm_discovery/results.py`, `.gitignore:21-22`.

---

*Research by subagent for #82 — measurement + code audit, no build. Does not close ticket.*
