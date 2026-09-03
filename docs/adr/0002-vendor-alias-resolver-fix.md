# ADR 0002: Resolver/prompt fix for versioned vendor aliases (glm/mimo/qwen/muse)

## Status
Accepted — issue #50 (part of #46 Wayfinder)

## Context
NaraRouter discovery showed 5 free ids dropping or erroring despite known families:
- mimo-v2.5-free → unresolved (AA has mimo-v2-5-0424 dated)
- minimax-m3-free → AA hit but benchmark cache miss (SWE 80.5 never surfaced)
- muse-spark-1.2/1.3-contributor-free → unresolved (AA has muse-spark-1-2, no contributor)
- qwen3.8-flash-free → unresolved (AA has qwen3-8-flash-next with -next)
- glm-5.3-free → resolved via variant but benchmark miss + invalid JSON hedging

Root causes (issue #48 diagnosis):
1. `_normalize_model_key` keeps `-free` (only strips `:free`), so `BenchmarkDataCache.get` always misses for `-free` aliases; `normalize_model_id` correctly strips `-free` but alias_map checked pre-strip so mimo alias never fires.
2. Muse `-contributor` and Qwen `-next` suffixes have no alias; variant generator only swaps dot/hyphen, not suffixes.
3. `EvidenceCollector.collect` uses `models_dev.get_model(model_id_lower)` exact, so `minimax/MiniMax-M3` and `meta/muse-spark-1.2` never surface for `-free` ids; benchmarks and provider claims stay empty.
4. Empty evidence + disabled web search → contradictory AA/no-benchmark payloads and nondeterministic invalid JSON errors for glm/muse-1.3.

Candidates considered (issue #50):
- extend `normalize_model_id` / `_generate_match_variants`
- add vendor alias map
- enrich evidence packet with family aliases
- adjust `llm.py` SYSTEM_PROMPT / `_build_prompt` to include AA candidates
- fix benchmark-cache lookup key
- accept catalog gap and rely on web search triangulation

## Decision
Minimal correct fix = **normalization-only + alias map + cache/evidence lookup fix**. No prompt enrichment, no catalog backfill.

### 1. Benchmark cache key (`src/llm_discovery/benchmarks.py::_normalize_model_key`)
- Strip `[:/_-]free` uniformly (was only `:free`) before transforms.
- Strip vendor suffixes `-contributor`, `-next` after free strip, so `muse-spark-1.2-contributor-free` → `muse-spark-1.2` and `qwen3.8-flash-free` → `qwen-3.8-flash` match stored keys via dot/hyphen alts.
- Preserves date suffix (issue #42) for cache distinctness; resolver handles dated alias separately.

### 2. Resolver (`src/llm_discovery/model_matching.py`)
- Compute `stripped_slug` / `stripped_base` (free-stripped) and compare them in `alias_map` loop, so `mimo-v2.5-free` hits `mimo-v2.5 -> mimo-v2-5-0424` without needing a separate `-free` entry.
- Add vendor alias entries:
  - `muse-spark-1.2-contributor` / `muse-spark-1.2` / `muse-spark-1-3-contributor` etc → `muse-spark-1-2`
  - `qwen-3.8-flash` / `qwen3.8-flash` / `qwen-3-8-flash` etc → `qwen3-8-flash-next`
- Extend `_generate_match_variants` to emit suffix-strip variants (`-contributor`, `-next`) plus their dot/hyphen forms, so normalized fallback covers `muse-spark-1.2-contributor` → `muse-spark-1.2` → `muse-spark-1-2` without requiring an exact alias for every dot/hyphen combo.

### 3. Evidence packet (`src/llm_discovery/evidence_collector.py`)
- Try `models_dev.get_model` on free-stripped id and bare slug, then normalized fallback via `normalize_model_id` equality (handles `minimax-m3-free` → `minimax/MiniMax-M3`, `alibaba/qwen3.8-flash`). For suffix-stripped forms, also try without `-contributor` / `-next`.
- This surfaces `minimax/MiniMax-M3` SWE 80.5 + Terminal 66 benchmarks and `meta/muse-spark-1.2` coding claim for `-free` ids, so evidence packet no longer empty when AA hit exists. Benchmark cache fix ensures `cache.get` hits for minimax/muse/qwen after free strip.

Scope explicitly **not** changed:
- `llm.py` SYSTEM_PROMPT / `_build_prompt` unchanged; prompt already requires AA candidates when provided and handles parent-model inference. Adding AA candidates to prompt is follow-up only if alias gap persists.
- No catalog backfill; AA gap for muse-1.3 (no 1-3) intentionally maps to 1-2 via alias (latest spark) rather than inventing a catalog entry. Web search triangulation remains fallback for true no-AA models.

Risk to other providers: low. Changes are additive and scoped to stripping known suffixes; existing exact/normalized/variant/similarity paths unchanged. New alias keys are vendor-specific and lower-cased before comparison. Benchmark prefix stripping already handled `minimax-` etc; adding free/contributor/next is consistent with matcher free handling.

## Consequences
- `mimo-v2.5-free` now alias → `mimo-v2-5-0424` (38) with provider claim.
- `minimax-m3-free` now normalized_slug + benchmark hit (SWE 80.5 visible) → keeps if coding true.
- `muse-spark-1.2/1.3-contributor-free` now alias → `muse-spark-1-2` (56.8) with/without claim; 1.2 gets strong evidence, 1.3 inherits 1-2 score as parent.
- `qwen3.8-flash-free` now alias → `qwen3-8-flash-next` (55.8).
- `glm-5.3-free` already variant; now benchmark cache hit for AA 59.5 as well.
- All 108 existing tests still pass (`PYTHONPATH=.:src pytest`).
- Invalid JSON hedging reduced: contradictory AA-no-benchmark payloads eliminated for minimax/muse/qwen; remaining empty-benchmark cases (mimo) now have provider claim, so LLM no longer hedges.

## References
- Issue #50 Decide resolver/prompt fix for versioned vendor aliases
- Issue #48 Research: Diagnose LLM evaluator miss (root causes + recommendations)
- Issue #46 Wayfinder: Fix NaraRouter free-category filter + LLM model-name recognition
- Data: `data/artificial_analysis_models.json` 631 models, `data/models_dev_catalog.json`
