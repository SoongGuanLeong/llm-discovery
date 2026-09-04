# ADR 0003: Vision-capable coding model keep/drop exception

## Status
Accepted — Issue #55 (Grilling) part of #53 Wayfinder

## Context
Deterministic `specialized_model:vision` flag (via `models_dev` description) dropped all vision-language models before LLM judge. This caused false drops for coding-capable vision models with cheap pricing, exemplified by `nararouter/qwen3.8-27b` (`coding_score 61.7`, `SWE Pro 61.7`, `aa_coding 68.1`, pricing $1.13) which should be kept for code tasks. Pure vision-only models (Qwen-Image-Edit, InternVL, Qwen3-VL-235B `aa 14.4`) correctly stay dropped.

Research #54 quantified: 7 rows (6 unique) hit vision flag; 4 unique (5 rows) coding-capable + cheap should be recovered; 1 true VL-only + 1 borderline + 6+ LLM pure-vision stay dropped. Other specialized patterns (`embedding`, `tts`, `audio`, `voice`, `safety`, `rerank`) remain compulsory drops.

Existing `PolicyGate` deterministic override already uses `coding_score >=35`, `SWE >=50`, `Terminal >=50`. Need explicit exception rule aligning with it plus AA coding/intel and pricing.

## Decision

**Scope:** Only `specialized_model:vision` gets conditional exception. If any non-vision flag present, compulsory drop unchanged.

**Condition to bypass deterministic drop:** `vision-only` AND `coding-capable` AND `cheap_or_free`:

- `vision-only`: `packet.deterministic_flags` non-empty and every flag == `specialized_model:vision`
- `coding-capable` (OR):
  - `aa_coding (artificial_analysis_coding_index) >=45` OR
  - `aa_intel (artificial_analysis_intelligence_index) >=55` OR
  - `coding_score >=35` OR
  - `swe_bench_verified >=50` OR `swe_bench_pro >=50` OR `terminal_bench >=50` OR `terminal_bench_2_1 >=50`
  - Single signal sufficient; `aa_coding`/`aa_intel` checked from `resolution.aa_model.evaluations`; bench scores from `BenchmarkDataCache` via `build_benchmark_profile`
- `cheap_or_free`:
  - `pricing.price_1m_blended_3_to_1 <= 1.2` (blended 3:1 from live `ModelResolver` AA catalog, post-alias) OR
  - free if model_id contains `free` substring (`:free`, `-free`, `_free`, `/free`) OR AA pricing `blended==0` OR `input==0 && output==0`
  - Null pricing without free proof is **not cheap** — stays dropped (grill Q3 C). Proven cheap required.

Threshold `1.2` chosen to keep research keep set ($0.23–$1.13) while tighter than original $2.0. `$1.10` and `$1.13` (Qwen3.5-122B, Qwen3.8-27B) pass `<=1.2` but would fail `<1.0`.

**Implementation seam:** `pipeline.evaluate_model` conditional bypass (Option B). `EvidenceCollector` still records truthful `vision` flag; pipeline skips `deterministic_drop_record` when exception holds and falls through to `Judge → PolicyGate` normal path. No auto-keep; judge + deterministic coding override decide final. Preserves audit trail.

## Consequences
- Recovers 4 unique vision-language coding models (Qwen3.5-27B, Qwen3.5-122B, Qwen3.8-27B x2 providers, Qwen3.8-Flash-Next) while keeping 1 true VL-only + 1 borderline + LLM pure-vision dropped — no regression.
- `embedding`/`tts`/`safety`/`audio`/`voice` unchanged.
- Pricing resolved via live `ModelResolver` (alias-aware), not stale YAML `pricing: null`.

## References
- #53 Wayfinder, #54 Research (docs/research/issue-54-vision-drop-impact.md), #55 Grilling
- `src/llm_discovery/pipeline.py` `VISION_CHEAP_THRESHOLD=1.2`, `_is_vision_only`, `_is_coding_capable`, `_is_vision_free_model`, `_is_cheap_or_free`
