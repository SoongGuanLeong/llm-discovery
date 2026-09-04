# Research #81 — Audit result inaccuracy sources across data/results

**Issue:** [#81 Research: Audit result inaccuracy sources across data/results](https://github.com/SoongGuanLeong/llm-discovery/issues/81) — child of Wayfinder map [#80 Wayfinder: Accurate results and intelligent incremental build](https://github.com/SoongGuanLeong/llm-discovery/issues/80)  
**Status:** research — primary-source audit, no build  
**Date:** 2026-09-04  
**Sources:** `data/results/*.yaml` (17 files), `src/llm_discovery/results.py`, `policy_gate.py`, `evidence_collector.py`, `benchmarks.py`, `model_info_store.py`, `categorize.py`, plus cross-check `docs/research/issue-35*, issue-36*, issue-71*`  
**Method:** Load all 17 YAMLs via `yaml.safe_load`, enumerate keep/drop_llm/error records, flag evidence_level, coding_score null, pricing null, benchmark scores empty, hallucinated evidence strings, vendor alias patterns, and file-shape anomalies. Map each mode to code site with file:line.

---

## 1. Snapshot — totals and per-provider counts

### Totals (17 files, 2026-09-04)

| | keep | drop_llm | error |
|---|---:|---:|---:|
| **All providers** | **122** | **384** | **51** |
| Evidence: weak keep | 3 | — | — |
| Evidence: moderate keep | 7 | — | — |
| Evidence: strong keep | 112 | — | — |

`huggingface.yaml` is single-record (1 keep, no lists); all other 16 are batch shape `{provider, evaluated_at, keep, drop_llm, error}` per `results.py:112-203` (`ProviderBatchWriter`). `cloudflare.yaml` has **0 keeps, 51 drops, 14 errors** — 100% drop shape but entirely UUID-keyed.

### Per-provider defect counts (keeps only, except UUID column)

| provider | keep | weak_keep | mod_keep | cs_null | no_pricing (strong) | no_scores (keeps) | halluc | vendor -free | vendor step | router keep | uuid (drop+err) | shape |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| agnes | 4 | 0 | 1 | 1 | 1 (agnes-2.5-pro) | 1 | 0 | 0 | 0 | 0 | 0 | batch |
| ainative | 29 | 0 | 4 | 5 | 2 (nemotron-3-super, qwen3-5-397b) | 5 | 2 | 0 | 0 | 0 | 0 | batch |
| bazaarlink | 1 | 0 | 0 | 1 | 1 (auto:free) | 1 | 0 | 1 | 0 | 1 | 0 | batch |
| cerebras | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | batch |
| cloudflare | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **65** (51+14) | batch, 0 keep |
| cohere | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | batch, 0 keep |
| google | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | batch |
| groq | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | batch |
| **huggingface** | **1** | 0 | 0 | — | — | — | 0 | 0 | 0 | 0 | 0 | **single-record** |
| kilo_ai | 7 | **2** | 0 | 2 | 1 (poolside/laguna) | 2 | 0 | 5 | 1 | **2** | 0 | batch |
| llm7 | 22 | 0 | 0 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | batch |
| mistral | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | batch |
| modelscope | 14 | 0 | 1 | 1 | 1 (Qwen-235B) | 1 | 0 | 0 | 2 | 0 | 0 | batch |
| nararouter | 5 | 0 | 0 | 0 | 1 (laguna-s-2.1) | 0 | 0 | 3 | 0 | 0 | 0 | batch |
| navy_ai | 20 | 0 | 1 | 1 | 1 (deepseek-reasoner) | 1 | 1 | 0 | 0 | 0 | 0 | batch |
| opencode_zen | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | batch |
| openrouter | 7 | **1** | 0 | 1 | 2 (poolside x2) | 1 | 0 | 6 | 0 | **1** | 0 | batch |

**Aggregated flags (keeps):** cs_null **13/122 (10.7%)**, no_scores **12/122 (9.8%)**, no_pricing on strong keeps **10/122 (8.2%)**, weak+moderate keeps **10/122 (8.2%)** of which 3 are router keeps (intentional), hallucinated evidence **4 strings across 3 models**, -free suffix **17 keeps (13.9%)**, step alias **3 keeps**, UUID model_ids **65/65 cloudflare records (100%)**.

---

## 2. Failure mode taxonomy (5 modes, with code sites)

### Mode A — Weak/moderate keeps that should be drops (suspect keeps)

**Definition:** `decision: keep` with `evidence_level: weak` (forced weak) or `evidence_level: moderate` where `coding_score: null`, `benchmarks.scores: {}`, or evidence cites only provider claims / no URL triangulation. Per policy, these should be `drop` (or at least not in per-provider keep list) until triangulated.

**Inventory (7 suspect + 3 router weak keeps):**

| provider | model_id | el | aa | cs | scores? | pricing? | why suspect | evidence excerpt |
|---|---|---|---|---|---|---|---|---|
| agnes | agnes-2.5-flash | moderate | 49.1 | null | empty | yes $0.15 | AA 49.1 alone, no coding benchmarks; second evidence line admits "No specific coding benchmarks found; AA score alone supports moderate" | `AA Intelligence score 49.1 … with provider documentation mentioning coding improvements (source: https://wiki.agnes-ai.com/...)` |
| ainative | claude-opus-4.5 | moderate | 35.6 | 35.6 | {aa} | yes $10 | cs=35.6 is threshold-promoted (`coding_score>=35` → coding=true) but no SWE/Terminal; evidence says "No web evidence found … third-party re-host without verified coding performance" + deterministic override | `No web evidence found for coding capabilities; third-party re-host` |
| ainative | deepseek-3-2 | moderate | 25.1 | null | empty | yes | AA 25.1 barely above MIN_SCORE 24, no benchmarks, hallucinated BenchLM line | `BenchLM ranks DeepSeek V3.2 #84 in coding category with overall score 54.8/100` |
| ainative | deepseek-3.2 | moderate | 25.1 | null | empty | yes | Same AA as above, evidence "DeepSeek models are known for coding capabilities" — generic claim, no URL, no benchmark | `DeepSeek models are known for coding capabilities with moderate general intelligence` |
| ainative | zai-glm-4-7 | moderate | 34.5 | null | empty | yes | AA 34.5 alone, no benchmarks; evidence is provider docs without SWE/Terminal | `Provider documentation confirms enhanced multi-language coding … (source: https://docs.z.ai/...)` |
| modelscope | deepseek-ai/DeepSeek-V4-Pro-0813 | moderate | 53.2 | null | empty | yes | AA 53.2 but cs null / empty scores — benchmark profile failed to attach (dot-hyphen or AA-only). Flagged moderate via AA only. | `AA score 53.2 exceeds minimum threshold … DeepSeek V4 Pro is a coding-capable model per AA index` |
| navy_ai | grok-4-fast-non-reasoning | moderate | 27.9 | null | empty | yes | AA 27.9 alone, no benchmarks; hallucinated sibling evidence cites tokenmix.ai / callsphere.ai | `Grok 4 base model demonstrates strong coding benchmarks including SWE-bench Verified ~67-78% (source: tokenmix.ai, callsphere.ai)` |
| kilo_ai | kilo-auto/free | weak | null | null | empty | no | Router keep — intentional per `policy_gate.py:_is_router_model()` (`kilo-auto/free` hard-coded). Not a bug but pollutes accuracy gate if counted as coding model. | `Router model: always keep (routing meta-model)` |
| kilo_ai | openrouter/free | weak | null | null | empty | no | Same router class | same |
| openrouter | openrouter/free | weak | null | null | empty | no | Same router class | same |

**Code sites:**

- `policy_gate.py:21-36` `_is_router_model()` — returns True for `kilo-auto/free`, `openrouter/free`, any id containing `router` or `auto+free`. Router override at `policy_gate.py:260-268` forces `tier=flash, decision=keep, coding=True` regardless of AA/benchmarks. Routers are legit keeps but mis-classified if audit treats them as coding models.
- `policy_gate.py:151-164` `_deterministic_evidence_level()` — promotes moderate when `aa>=24` alone or any supplement >=30, even with empty benchmark coverage. This is why deepseek-3.2 (AA 25.1, no benchmarks) is moderate rather than weak. Pre-fix (`issue-35`) LLM was sole decider; post-fix hybrid promotion is now aggressive on AA-only.
- `policy_gate.py:166-181` triangulation guard — demotes moderate→weak only when `aa is None + no scores + no URL`. Does NOT demote AA-only moderate (aa present) even if no URL, so zai-glm-4-7 passes guard.
- `evidence_collector.py:82-92` specialized-pattern detection — would have sent vision/audio models to weak but not triggered for these (no specialized substring).
- `benchmarks.py:131-139` `benchmark_coverage` / `coverage_with_supplements` — empty for these records (0.0 coverage), yet evidence_level is moderate via AA-only path.

**Impact:** 7/122 keeps (5.7%) are inflated moderate keeps with cs_null + no_scores. They inflate provider keep counts (ainative is worst: 4/29 keeps = 13.8% suspect). If `build_all` enables a 14-day TTL reuse, these stale AA-only keeps would be blindly reused.

### Mode B — Hallucinated evidence strings (LLM invention)

**Definition:** Evidence lines citing fabricated sources, non-existent leaderboards, or unverifiable aggregate ranks that do not correspond to any entry in `data/artificial_analysis_models.json`, `data/models_dev_catalog.json`, or `data/benchmarks.json`.

**Inventory (4 strings, 3 models):**

| provider | model_id | evidence_level | hallucinated line | why hallucinated |
|---|---|---|---|---|
| ainative | deepseek-3-2 | moderate | `BenchLM ranks DeepSeek V3.2 #84 in coding category with overall score 54.8/100` | No `BenchLM` source in `BENCHMARK_NAME_MAP` (`benchmarks.py:26-70`), no entry in `benchmarks.json`; invented ranking |
| ainative | nemotron-3-super-120b | strong | `SWE-bench Verified score of 60.47% confirms strong coding capability (source: digitalapplied.com)` | `digitalapplied.com` not in any benchmark source column; real NVIDIA source is `build.nvidia.com` / HF card. Score 60.47 not in `raw_benchmarks` list for this provider check vs catalog |
| navy_ai | grok-4-fast-non-reasoning | moderate | `Grok 4 base model demonstrates strong coding benchmarks including SWE-bench Verified ~67-78% (source: tokenmix.ai, callsphere.ai)` | Two fake leaderboard domains; both lines contain approximate ranges (`~67-78%`) not a single score — LLM hedging with invented aggregate |

**Also near-miss (not counted as halluc but worth flagging):** `modelscope Qwen3-235B-A22B` cites `Aider Polyglot: 59.6%` with no AA match — real benchmark but provider is not Qwen/Ali canonical; evidence is plausible but unverified re-host claim.

**Code sites:**

- `src/llm_discovery/llm.py:SYSTEM_PROMPT` (per issue-35) — no source allowlist; LLM may invent any URL. Pre-#39, no triangulation guard.
- `evidence_collector.py:147-192` — builds `evidence_packet` from deterministic catalogs but packet is fed to LLM judge; LLM can ignore packet and synthesize.
- `policy_gate.py:166-181` triangulation guard (issue #39) — partially mitigates by demoting claim-only moderate without URL, but hallucinated URL *with* domain passes guard (has http).
- `results.py:ProviderBatchWriter._to_record()` at `results.py:136-153` — `clean_evidence()` (`evidence_utils.py`) strips empty strings but does not validate URL domains or score ranges.

**Impact:** 3/122 keeps (2.5%) carry at least one hallucinated line; rates highest in ainative (2/29 = 6.9%). Undermines audit trust — a consumer cannot verify the cited source. Hallucinated scores also flow into `model_info_store.json` if cached (moderate is cacheable per `model_info_store.py:CACHEABLE_LEVELS`).

### Mode C — Missing pricing / missing benchmarks (null coverage)

**Definition:** `pricing: null` (no AA pricing), `coding_score: null` (no BenchmarkProfile scores), `benchmarks.scores: {}` (empty), `benchmark_coverage: 0.0`.

**Inventory:**

| sub-mode | keeps affected | % of keeps | worst providers | notes |
|---|---|---|---|---|
| cs null | 13 | 10.7% | ainative 5, kilo_ai 2, bzr 1, agnes 1, modelscope 1, navy_ai 1, openrouter 1, llm7 1 | All have no BenchmarkProfile scores; some have AA only path |
| no_scores (empty scores dict) | 12 | 9.8% | ainative 5, kilo_ai 2, others 1 each | Same set as cs null minus 1 (rounding) |
| no_pricing on strong keep | 10 | 8.2% | kilo_ai 1, modelscope 1, nararouter 1, navy_ai 1, openrouter 2, ainative 2, bzr 1, agnes 1 | Strong evidence keeps without price — tier influenced message missing (`policy_gate.py:231-250` skips pricing line) |
| benchmark_coverage 0.0 among keeps | 4 explicit 0.0 + 12 empty | ~13% | cerebras, llm7 gemini-3.8-flash-high, nav  | Reporting coverage 0.0 but strong evidence still granted via terminal_bench supplement path |

**Strong keeps with missing pricing (10):**

- `agnes agnes-2.5-pro` strong, cs 49.1, aa null, pricing null — phantom AA 49.1 without pricing lookup
- `ainative nemotron-3-super-120b` strong, cs null, pricing null — halluc source, no AA pricing
- `ainative qwen3-5-397b` strong, cs null, pricing null — no AA pricing
- `bazaarlink auto:free` strong, cs null, pricing null — router
- `kilo_ai poolside/laguna-s-2.1:free` strong, cs 56.98, pricing null — no AA pricing, benchmark-only strong
- `modelscope Qwen/Qwen3-235B-A22B` strong, cs 59.6, pricing null — Aider-only strong, no AA pricing
- `nararouter laguna-s-2.1` strong, cs 56.98, pricing null — same laguna, no pricing
- `navy_ai deepseek-reasoner` strong, cs 74.2, pricing null — Aider-only strong, no AA pricing
- `openrouter poolside/laguna-s-2.1:free` + `laguna-xs-2.1:free` strong, cs 56.98, pricing null

**Code sites:**

- `policy_gate.py:96` `pricing = aa_model.get("pricing") if aa_model else None` — pricing only when AA resolution exists. Third-party re-hosts (navy_ai, ainative) often have no AA match → pricing null even when strong benchmarks exist. Fallback to `data/nararouter_pricing_raw.json` or models.dev pricing never attempted.
- `benchmarks.py:176-241` `BenchmarkDataCache.collect_from_local` — merges AA + models.dev but lookup key mismatches cause empty profile: dot vs hyphen (`gemini-3.8-flash-high` vs `gemini-3-7-flash`), version suffix (`deepseek-4…-0813`), alias miss (`minimax-m2.7` vs `minimax-m2-7`). Fix in dirty diff was zzzdotzzz hack.
- `benchmarks.py:26-70` `BENCHMARK_NAME_MAP` — 20 raw names mapped; supplements like BigCodeBench, EvalPlus, LiveCodeBench heavily underpopulated (0 bigcodebench rows, 4 livecodebench rows per issue-36).
- `model_info_store.py:aggregate_pricing()` — cross-provider pricing aggregation exists but not called during per-provider YAML write; each YAML holds single-provider price or null.

**Impact:** 10.7% cs_null means coding assessment is AA-only or LLM-only, not triangulated. 8.2% strong keeps without pricing means tier assignment (`categorize_model` at `policy_gate.py:225` uses `pricing_blended`) is price-blind for those models. At `model_info_store.py:should_cache()` gate, these still cache (strong/moderate) and pollute `model_info_store.json`.

### Mode D — Vendor-alias instability (-free, stepfun, dot/hyphen, -contributor, -next, provider prefix)

**Definition:** Same logical model appears under different surface ids; normalization inconsistencies cause duplicate records, missed benchmark joins, or split pricing.

**Inventory:**

| alias class | affected keeps | example ids | normalization rule | bug |
|---|---|---|---|---|
| `-free` / `:free` suffix | 17 keeps | `minimax/m3:free`, `deepseek-v4-flash-free`, `minimax-m3-free`, `muse-spark-1.2-contributor-free` | `results.py:_normalize_model_id()` + `model_info_store.py:normalize_store_key()` strip trailing `[:/_-]free` (case-insensitive) before provider split — one occurrence | Correct but not applied at BenchmarkDataCache lookup; cache key retains suffix, miss |
| stepfun → step | 3 keeps | `step-ai/Step-3.5-Flash`, `step/step-3.7-flash:free` | `results.py:16-20` + `model_info_store.py:38-44` map `stepfun-` → `step-` | Only at YAML write + store key; `evidence_collector.py:106-129` normalized fallback does handle via `model_matching.normalize_model_id` but `benchmarks.py:BenchmarkDataCache` lookup uses raw id — miss |
| dot vs hyphen (`3.8` vs `3-8`) | ≥4 keeps | `qwen3.8-27b`, `deepseek-3.2` vs `deepseek-3-2`, `gemini-3.8-flash-high` | `model_matching.normalize_model_id` replaces `.` between digits via zzzdotzzz | BenchmarkDataCache alternates include `norm, norm.replace(".","-"), re.sub(r"(\\d)-(\\d)", …)` — covers 1-digit cases but not multi-digit suffix variants |
| `-contributor` / `-next` | 3 keeps | `muse-spark-1.2-contributor-free`, `qwen3.8-Flash-Next` | NOT stripped at store key (`model_info_store.py:66-73` keeps suffix to avoid collision); alias map in `model_matching.py` handles | Correctly kept distinct at store key, but evidence collector re-strips for lookup — inconsistent |
| provider prefix (`qwen/qwen3…` vs `Qwen/…`) | pervasive | `qwen/qwen3.8-27b`, `Qwen/Qwen3-235B-A22B` | `normalize_store_key()` takes `rsplit("/",1)[-1]` + lowercases | Consumer sees duplicated minimax-m3 across kilo_ai, openrouter, nararouter — same store key but separate YAML rows; intended but obscures dedup |
| duplicate logical model across providers | 9 logical models appear 2+ times | minimax-m3 (kilo_ai, openrouter), laguna-s-2.1 (kilo_ai, openrouter, nararouter, opencode_zen), deepseek-v4-flash (9 providers) | Should collapse via `normalize_store_key` in store | Current YAMLs correctly duplicate (per-provider grain), store dedup is separate layer — no bug yet, but 14-day file-level TTL would treat each copy independently |

**Code sites:**

- `results.py:15-20` `_normalize_model_id()` — stepfun only; does NOT handle `-free`, dots, or contributor.
- `results.py:107` `SingleModelWriter` + `results.py:184-195` `ProviderBatchWriter` — call `_normalize_model_id` + `clean_evidence` but no provider-prefix strip; model_id stored verbatim (`openrouter/minimax/…:free`) — shape preserved for YAML consumers.
- `model_info_store.py:47-125` `normalize_store_key()` — canonical dedup: lowercases, strips `[:/_-]free`, stepfun→step, provider prefix strip. Single source of truth for store key.
- `model_info_store.py:128-149` `normalized_key_with_matcher()` — opt-in dot-insensitive + alias-aware variant via `model_matching.normalize_model_id`.
- `model_matching.py:normalize_model_id()` (per ADR 0002) — handles dot↔hyphen, `-contributor` → `-spark-1-2`, `-next` → canonical, minimax prefix folding. Not used in YAML write path.
- `benchmarks.py:BenchmarkDataCache.get()` (approx line 250-310) — key lookup tries direct hit then normalized alternates; zzzdotzzz dirty fix lives here. Still misses some suffix combos.
- `evidence_collector.py:95-129` — verbose fallback chain (strip free, bare stripped, `model_matching.normalize_model_id` candidates, loop over `models_dev.models`) — fragile but covers most alias misses at evidence time.

**Impact:** Alias instability is the #1 cause of Mode C (cs null). When benchmark lookup misses due to suffix/dot mismatch, coding_score stays null and evidence falls back to AA-only → moderate keep inflation (Mode A). Fixing alias at cache lookup layer fixes both C and A together.

### Mode E — File-shape anomalies (structure, not content)

#### E1. huggingface.yaml single-record vs batch

**Observed:** `data/results/huggingface.yaml` (11 lines) shape `{provider, model_id, decision, tier, aa_model_id, aa_score, confidence, evidence}` — no `evaluated_at`, no `keep/drop_llm/error` lists. All other 16 files follow batch shape `{provider, evaluated_at, keep:[], drop_llm:[], error:[]}` per `results.py:112-120` `PROVIDER_SCHEMA_KEYS`.

**Code site:** `results.py:62-109` `SingleModelWriter.write()` writes single-record shape (one model → one file); `results.py:123-203` `ProviderBatchWriter.write()` writes batch shape. `huggingface.yaml` was produced by legacy single-model path (`save_yaml_result` → `SingleModelWriter`) at a time when HuggingFace provider had one model (Kimi-K3). Current pipeline (`pipeline.py:build_provider`) calls `ProviderBatchWriter` for all providers; the file was never migrated.

**Consequences:**

- `build_all.py` / `backfill.py` per-provider reuse logic that iterates `yaml[keep]` will crash or skip huggingface (no keep list).
- `model_info_store` hydration that reads `data/results/*.yaml` via batch assumption misses huggingface keeps.
- Evidence fields differ: huggingface stores `evidence` as plain strings but lacks `evidence_level`, `coding_score`, `pricing`, `benchmarks`, `coding_assessment` — not auditable.
- Re-running discovery for huggingface will overwrite file with batch shape if `ProviderBatchWriter` used — shape migration is not idempotent without check.

#### E2. cloudflare.yaml UUID model_ids

**Observed:** All 65 records (51 drop_llm + 14 error) have UUID model_id `^[0-9a-f]{8}-[0-9a-f]{4}-…-…$` (e.g., `01564c52-8717-47dc-8efd-907a2ca18301`). keep is `[]`. No human-readable name. Evidence correctly identifies type ("Deepgram Aura is a text-to-speech model", "BGE is an embedding model", "bge-reranker-base is a reranker") — meaning the LLM judge received the UUID but inferred type via auxiliary API metadata not stored in YAML.

**Code site:** Cloudflare provider discovery at `src/llm_discovery/discovery.py` (or provider-specific fetcher) lists models via Cloudflare API `GET /models` which returns `{id: uuid, name?, description?}`; `pipeline.py:evaluate_model()` passes `model["id"]` (the UUID) as `model_id` to `PolicyGate.apply()` and `EvidenceCollector.collect()`. `EvidenceCollector` fallback to `models_dev.get_model(model_id_lower)` obviously misses for UUID, so deterministic packet is empty → all cloudflare records are weak (`evidence_level weak` or `none`) with `coding_score null`, `benchmarks {}`.

**Consequences:**

- Records are unactionable: a consumer cannot map UUID `01564c52-…` to a model card or pricing page; `model_info_store.normalize_store_key("01564c52-…")` returns the UUID itself — no dedup, no human key.
- 65 rows inflate drop counts without value; error rate 21.5% (14/65) from LLM invalid JSON — higher than any other provider (next highest is cohere 15%).
- Benchmark and AA joins are impossible for UUIDs — any future benchmark for a Cloudflare-hosted model (e.g., Qwen) cannot be matched.
- File size 30,519 B (largest) but 0 keeps — bloat with no signal.

#### E3. Other shape quirks (minor)

- `bazaarlink.yaml` 1 keep (`auto:free`) with `cs null, no pricing, no scores` — router-like auto discovery strategy, evidence `Provider bazaarlink uses auto discovery strategy` is tautological, not coding evidence. Shape is batch but keep is weak-strong tautology.
- `cohere.yaml` 0 keep / 17 drop / 3 error — all cohere models dropped (including 5 strong drop with cs null but deterministic flag `specialized_model:embedding` etc. would have kept if coding). Not anomalous per se but worth noting: cohere keep rate 0% vs ainative 34% — suggests cohere filtered as non-coding (embedding heavy).
- `huggingface.yaml` missing `evaluated_at` — staleness cannot be assessed; 14-day TTL cannot be applied.

---

## 3. Cross-cutting root causes (code seams)

| Root cause | Code seam | Failure modes hit |
|---|---|---|
| **BenchmarkDataCache key mismatch** (suffix/dot/alias miss → null coding_score) | `benchmarks.py:BenchmarkDataCache.collect_from_local / get()` + `model_matching.normalize_model_id` not used at write path | C (cs null), A (moderate inflation) |
| **LLM may invent evidence with any URL** (no allowlist) | `llm.py:SYSTEM_PROMPT` + `evidence_collector.py -> LLM judge` + `policy_gate.py:triangulation guard` (partial) | B (hallucination) |
| **Pricing only from AA resolution** (no fallback) | `policy_gate.py:96` + `catalogs.py:ModelsDevCatalog` pricing not consulted | C (no_pricing strong keeps) |
| **Router override forced keep** (weak but keep) | `policy_gate.py:21-36 + 260-268` | A (weak keeps counted as keeps) |
| **Deterministic evidence_level promotion too permissive on AA-only** | `policy_gate.py:151-164` `_deterministic_evidence_level` | A (moderate inflation) |
| **Legacy SingleModelWriter still on disk** | `results.py:62-109` vs `123-203` shape split | E1 (huggingface shape) |
| **Provider fetcher uses UUID as model_id** | `discovery.py:Cloudflare fetch` + `pipeline.py:evaluate_model` | E2 (cloudflare UUID) |
| **Specialized-model flag suppresses coding override** | `evidence_collector.py:82-92` + `benchmarks.py:has_critical_weakness()` | Strong drops that arguably should be keeps (e.g., `groq/qwen3.6-27b` dropped as vision despite cs 77.2) |

**Strong drops that arguably should be keeps (specialized-flag overreach, 6 cases):**

| provider | model_id | cs | el | why dropped | suspect? |
|---|---|---|---|---|---|
| groq | qwen/qwen3.6-27b | 77.2 | strong | `specialized_model:vision` | qwen3.6-27b is known coding model, flag likely triggered on "vision" substring elsewhere; should be keep |
| navy_ai | qwen3.6-27b | 77.2 | strong | same | same |
| kilo_ai | thinkingmachines/inkling:free + small | 42.3/41.2 | strong | `specialized_model:audio` (pattern "audio" in model_id) | inkling is audio-to-text + coding? Evidence says audio, so drop may be correct — but cs strong suggests re-review |
| llm7 | Inkling, Inkling-Small | 42.3/41.2 | strong | same audio pattern | same — borderline |

---

## 4. Prioritized fix order (P0 = blocks trust, P1 = accuracy lift, P2 = hygiene)

### P0 — must fix before 14-day TTL or store trust

| Rank | Fix | Mode | Code site | Effort | Why P0 |
|---|---|---|---|---|---|
| **P0-1** | **Cloudflare UUID → human name** | E2 | `discovery.py:Cloudflare fetch` (store `model["name"]` or `display_name` alongside UUID, use name as model_id; keep UUID as `source_id`), `pipeline.py` (pass canonical name to PolicyGate), `normalize_store_key` | M | 65 rows unactionable; blocks any benchmark/AA join and store dedup; fixes cloudflare keep rate (currently 0%) |
| **P0-2** | **Unify BenchmarkDataCache alias normalization** | C, A, D | `benchmarks.py:BenchmarkDataCache.get() / collect_from_local()` — call `model_matching.normalize_model_id` + `normalize_store_key` on both insert and lookup keys; remove zzzdotzzz hack; add `-free/-contributor/-next` stripping at lookup | S-M | Single fix lifts 13 cs_null (10.7%) and 7 suspect moderate keeps; highest leverage |
| **P0-3** | **Evidence URL allowlist + hallucination lint** | B | `llm.py:SYSTEM_PROMPT` (add "only cite sources from evidence_packet provider_claims or raw_benchmarks sources; no other domains"), `evidence_utils.clean_evidence` or new `lint_evidence()` in `results.py:ProviderBatchWriter._to_record()` (reject BenchLM, unknown domains, ~ ranges) | S | Prevents hallucinated moderate keeps from caching (CACHEABLE_LEVELS); 3 models affected but trust-critical |
| **P0-4** | **Migrate huggingface.yaml to batch shape** | E1 | `results.py:ProviderBatchWriter.write()` migration script (read single-record, project to keep list with `evidence_level strong, benchmarks {scores}` if possible, write batch) + `pipeline.py:build_all` ensure all providers use ProviderBatchWriter | S | Blocks per-file reuse (no evaluated_at) and hydration; 1 file but breaks invariants |

### P1 — accuracy lift (keep/drop correctness)

| Rank | Fix | Mode | Code site | Effort | Why P1 |
|---|---|---|---|---|---|
| **P1-5** | **Demote AA-only moderate keeps to weak/drop** | A | `policy_gate.py:_deterministic_evidence_level()` — require `boundary: aa>=24 alone → weak` unless at least one `scores >=30` or URL-verified provider claim; or `policy_gate.py:166-181` triangulation guard extend to AA-only moderate without URL | S | Fixes 5/7 suspect moderate keeps (deepseek-3-2, deepseek-3.2, zai-glm-4-7, grok-4-fast, etc.) |
| **P1-6** | **Pricing fallback when AA missing** | C | `policy_gate.py:96` — if `pricing is None` and `models_dev.get_model(model_id)` has pricing, use that; also consult `data/nararouter_pricing_raw.json` for nararouter/bazaarlink | S | Fixes 10 strong keeps without pricing (8.2%); tier becomes price-aware |
| **P1-7** | **Router keeps exclude from coding accuracy gate** | A | `policy_gate.py:260-268` — keep routers but mark `is_router=True` in record so `model_info_store.should_cache()` + YAML consumers can filter; or write to separate `routers:` list not `keep:` | S | Removes 3 weak keeps from coding keep counts; aligns with issue-80 router spec |
| **P1-8** | **Specialized-model flag precision** | Cross | `evidence_collector.py:82-92` + `benchmarks.py:has_critical_weakness()` — make vision/audio flag conditional on description check (models_dev description contains vision/audio) not just model_id substring, to avoid qwen3.6-27b vision false positive | M | Fixes 3 strong drops that may be coding keeps |

### P2 — hygiene and future-proofing (do after P0/P1)

| Rank | Fix | Mode | Code site | Effort |
|---|---|---|---|---|
| **P2-9** | **Store-level dedup and evidence gating on write** | D, A | `model_info_store.py:should_cache()` (`CACHEABLE_LEVELS`) already gates weak; ensure `ProviderBatchWriter` or `pipeline.py` calls `should_cache` before inserting keep — currently weak keeps (routers) are still written to YAML as keep | S |
| **P2-10** | **Benchmark coverage expansion (BigCodeBench, EvalPlus, LiveCodeBench)** | C | `benchmarks.py:BENCHMARK_NAME_MAP` + `SIGNAL_WEIGHTS` + new collector for BigCodeBench (170 models) / EvalPlus (125) — see issue-36 gap analysis | M |
| **P2-11** | **YAML shape lint in CI** | E | New `scripts/lint_results.py` — assert each file has `provider, evaluated_at, keep, drop_llm, error`; UUID regex fail; evidence URL domain allowlist; cs_null rate <5% gate | S |
| **P2-12** | **Pricing aggregation per ProviderBatchWriter** | C | `model_info_store.py:aggregate_pricing()` — call at batch write time to fill per_provider_overrides for duplicated models (laguna, minimax) | S |

**Recommended ticket slicing for Wayfinder:**

- #84 (Cloudflare identity) ← P0-1
- New task: alias normalization ← P0-2 (blocks P1-5)
- #87 (YAML accuracy gate) ← P0-3, P0-4, P1-5, P1-7, P2-11
- #82 (catalog coverage) ← P2-10
- #85/#86 (incremental build invalidation) blocked until P0 done — otherwise TTL reuses dirty rows.

---

## 5. Evidence and reproducibility

- **Files read:** `data/results/*.yaml` (17), `src/llm_discovery/results.py:1-210`, `policy_gate.py:1-350`, `evidence_collector.py:1-200`, `benchmarks.py:1-320`, `model_info_store.py:1-300, should_cache at line ~260`.
- **Counts derived via:** `python3 -c "import yaml, pathlib, glob; …"` (see per-provider table generation in this audit's working trace; re-run with `python3 <<'PY' import yaml, pathlib, glob … PY`).
- **Hallucination check:** `any(p in evidence for p in ["BenchLM","digitalapplied.com","tokenmix.ai","callsphere.ai"])`.
- **Alias check:** `normalize_store_key()` strip test on `-free`, `stepfun→step`, provider prefix split.

---

*Next step: do not close #81 — charting phase. Consumer should read this file then proceed to #82 (benchmark/catalog coverage gaps) and #83 (grilling: accurate-enough gate).*
