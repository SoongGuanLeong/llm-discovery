# Research: Quantify vision drop impact on coding-capable candidates (issue #54)

Part of #53 — Wayfinder: Vision-capable coding model keep/drop policy

## Question

Which models currently hit the deterministic `vision` drop (`model_id` substring or `models_dev` description) and what coding evidence + pricing they had? Quantify false-drop cases like `nararouter/qwen3.8-27b` (coding_score 61.7) vs true vision-only (Qwen-Image-Edit, InternVL, ERNIE-VL). Pull from `data/results/*.yaml` and `src/llm_discovery/evidence_collector.py` + `pipeline.py` `is_specialized` gate. Identify scope: only `vision` vs other specialized patterns (tts/embedding/safety stay compulsory).

Deliver table: `model_id | flag_source | coding_score/aa_coding/SWE | pricing blended | keep-if-conditional?`

## Methodology

* **Deterministic gate**: `pipeline.evaluate_model` checks `packet.is_specialized()` → `deterministic_drop_record`. `EvidencePacket.is_specialized()` is `any(flag.startswith("specialized_model") for flag in deterministic_flags)` (`evidence_packet.py:88`).
* **Flag sources** in `EvidenceCollector.collect` (`evidence_collector.py:55-92`):
  * `model_id.lower()` substring match against `specialized_patterns` — includes `vision`, `audio`, `voice`, `tts`, `embedding`, `safety`, etc.
  * `models_dev.get_model()` description/name match — checks `vision`, `audio`, `voice`, `embedding`, `safety`, etc. in `description`/`name`. Handles `-free`, bare slug, and normalized fallbacks via `normalize_model_id`.
* **Scan**: enumerated all entries under `keep` + `drop_llm` + `error` in `data/results/*.yaml` (14 providers, 2026-09-03 snapshot), reconstructed `deterministic_flags` via live `EvidenceCollector.collect` with actual `BenchmarkDataCache` + `ModelResolver` against `data/artificial_analysis_models.json` (631 models) and `data/models_dev_catalog.json` (364 models). Cross-checked stored YAML evidence (`specialized_model:vision`) for ground-truth deterministic hits.
* **AA/pricing** via `ModelResolver.resolve_model` (alias-aware normalization) — not raw YAML `pricing` (which is null when AA miss pre-alias). Pricing = `price_1m_blended_3_to_1` ($/1M tokens, 3:1 input:output). Coding evidence = `coding_score` (weighted AA+SWE+LiveCodeBench per `benchmarks.py`), `aa_coding` (AA Coding Index), `SWE` (Verified/Pro).

## Snapshot: deterministic `vision` drops currently

Only 6 unique models (7 provider rows, one duplicate across providers) currently hit the deterministic `vision` flag in the evaluated results. All flag via `models_dev` description, not `model_id` substring.

No `model_id` in the results contains the literal substring `vision`; the `specialized_patterns` id check never fires for these rows. The flag is description-driven:

| provider id | models_dev key | description snippet |
|---|---|---|
| `Qwen/Qwen3.5-27B` | `alibaba/qwen3.5-27b` | "Qwen vision-language model for visual reasoning..." |
| `Qwen/Qwen3.5-122B-A10B` | `alibaba/qwen3.5-122b-a10b` | "Qwen vision-language model ..." |
| `Qwen/Qwen3.8-27B` / `nararouter/qwen3.8-27b` | `alibaba/qwen3.8-27b` | "Dense 27B vision-language model for coding, agent tasks, and image and video understanding" |
| `Qwen/Qwen3.8-Flash-Next` | `alibaba/qwen3.8-flash-next` | "...with vision encoder for coding, agent tasks, and image and video understanding" |
| `Qwen/Qwen3.5-35B-A3B` | `alibaba/qwen3.5-35b-a3b` | "Qwen vision-language model..." |
| `Qwen/Qwen3-VL-235B-A22B-Instruct` | `alibaba/qwen3-vl-235b-a22b-instruct` | "Qwen vision-language instruct model..." |

Other vision-capable models in `modelscope.yaml` (e.g., `MusePublic/Qwen-Image-Edit`, `OpenGVLab/InternVL3_5-241B-A28B`, `PaddlePaddle/ERNIE-4.5-VL-28B-A3B-PT`, `Qwen/Qwen3-VL-8B-Instruct`) have **no** `models_dev` match (catalog has `alibaba/qwen3-vl-235b-*`, `alibaba/qwen3-vl-plus`, `alibaba/qwen-vl-max` but not these exact variants) and their `model_id` does not contain `vision`/`audio`/`image`/`vl` → they bypass the deterministic gate and fall to the LLM judge. They appear as `drop_llm` `weak` with no AA/benchmark — correctly staying dropped.

### Full table (deterministic vision hits only)

`coding_score` from live `BenchmarkDataCache` + `PolicyGate` weighted score; `swe_*` from `benchmarks.json`/`raw_benchmarks`; `aa_coding` from AA catalog; `pricing` = AA blended 3:1 (live resolver, not YAML which was null for these ids pre-alias). Cheap = `<$2/1M` blended per #53 Notes (or null + strong score still qualifies).

| model_id (provider) | flag_source | coding_score | aa_intel / aa_coding | SWE | pricing blended | keep-if-conditional? | rationale |
|---|---|---|---|---|---|---|---|
| `Qwen/Qwen3.5-27B` (modelscope) | `models_dev:vision` (`alibaba/qwen3.5-27b`) | 72.4 | 34.6 / — | SWE Verified 72.4 | $0.825 | **YES — keep** | SWE 72.4 ≥50, cheap <2, AA 34.6 above flash min 24 |
| `Qwen/Qwen3.5-122B-A10B` (modelscope) | `models_dev:vision` (`alibaba/qwen3.5-122b-a10b`) | 72.0 | 32.8 / 45.7 | SWE Verified 72 | $1.10 | **YES — keep** | SWE 72 ≥50, aa_coding 45.7 ≥45, cheap |
| `Qwen/Qwen3.8-27B` (modelscope) + `qwen3.8-27b` (nararouter) — same underlying model, 2 provider rows | `models_dev:vision` (`alibaba/qwen3.8-27b`) | 61.7 | 52 / 68.1 | SWE Pro 61.7 | $1.125 | **YES — keep** | Issue exemplar: coding_score 61.7 ≥35, aa_coding 68.1 ≥45, SWE Pro 61.7 ≥40, pricing $1.13 cheap (<2). Nararouter row was stored with `pricing: null` because YAML predates alias fix, but live AA is cheap. Null-pricing + strong score would also qualify per #53. |
| `Qwen/Qwen3.8-Flash-Next` (modelscope) | `models_dev:vision` (`alibaba/qwen3.8-flash-next`) | 55.8* | 55.8 / 73.1 | — | $0.23 | **YES — keep** | AA 55.8 alone ≥55 frontier, aa_coding 73.1 ≥45, ultra-cheap $0.23. *coding_score == aa_intel (no SWE yet but AA coding confirms). |
| `Qwen/Qwen3.5-35B-A3B` (modelscope) | `models_dev:vision` (`alibaba/qwen3.5-35b-a3b`) | 29.9 | 29.9 / — (AA gpqa 0.845, scicode 0.377, lcr 0.68) | — | $0.688 | **NO — stay dropped (borderline, not pure vision-only)** | Multimodal vision-language (same family as `qwen3.6-35b-a3b` with SWE 73.4 / aa_coding 41.9), but this 3.5 variant has only AA 29.9, no SWE/Terminal, coding_score 29.9 <35. Not pure vision-only like `Qwen-Image-Edit`; family is code-capable but this snapshot lacks strong evidence. Stays dropped under current threshold; would flip to keep only if supplemented with SWE/AA coding ≥45. |
| `Qwen/Qwen3-VL-235B-A22B-Instruct` (modelscope) | `models_dev:vision` (`alibaba/qwen3-vl-235b-a22b-instruct`) | 14.4 | 14.4 / — | — | $0.70 | **NO — stay dropped** | AA 14.4 <<24, no coding benchmark. VL-only, not coding-capable. Only true VL-only among deterministic set. |

Notes:
* Duplicate: `nararouter/qwen3.8-27b` and `modelscope/Qwen/Qwen3.8-27B` are the same underlying `alibaba/qwen3.8-27b` model discovered via two providers. Counted as 1 unique model, 2 rows.
* YAML on-disk at audit time stored these 7 rows as `drop_llm` with `evidence: ["specialized_model:vision"]` and `confidence: 1.0` — wired through the deterministic path but serialized under `drop_llm` (the `deterministic_drop_record` helper currently writes `decision: drop` with `source: llm` in some snapshots vs `deterministic`; see `pipeline.py:deterministic_drop_record`). Functionally they were deterministic drops before the LLM.

### False-drop vs true vision-only quantification

* **False drops (vision-flagged but coding-capable, should be kept if conditional): 4 unique models (5 provider rows) — 67% of deterministic vision set.**
  * `Qwen3.5-27B`, `Qwen3.5-122B-A10B`, `Qwen3.8-27B` (×2 providers), `Qwen3.8-Flash-Next` — all have `coding_score ≥55` or `SWE ≥61` or `aa_coding ≥45` and pricing `$0.23–$1.13` (cheap). All exceed the #53 provisional keep condition: `coding evidence strong (AA coding / coding_score / SWE) + cheap pricing`.
* **True/borderline drops (vision-flagged but not coding-capable under current threshold): 2 unique models — 33%.**
  * `Qwen3-VL-235B-A22B-Instruct` — true VL-only (AA 14.4 <<24, no SWE/Terminal, pricing cheap but no coding signal) → correctly stays dropped.
  * `Qwen3.5-35B-A3B` — **not pure vision-only** (multimodal vision-language; family sibling `qwen3.6-35b-a3b` has SWE 73.4 and aa_coding 41.9), but this 3.5 snapshot has only AA 29.9, no SWE/Terminal, coding_score 29.9 <35 → stays dropped under current threshold. Would become keep if supplemented (e.g., SWE ≥50 or aa_coding ≥45). Corrected per review: do not count as "true vision-only" like `Qwen-Image-Edit`/`InternVL`/`ERNIE-VL`.
* **True vision-only controls *outside* deterministic set (LLM-dropped, not flagged): 6+ models in `modelscope.yaml` drop_llm/error, e.g.:**
  * `MusePublic/Qwen-Image-Edit` — image editing model, no AA, no benchmarks, pricing null, LLM `weak` — no deterministic flag (id has `image` not `vision`, no models_dev hit). Correctly stays dropped.
  * `OpenGVLab/InternVL3_5-241B-A28B` — vision model, no AA/benchmark, LLM weak.
  * `PaddlePaddle/ERNIE-4.5-VL-28B-A3B-PT`, `PaddlePaddle/ERNIE-4.5-300B-A47B-PT` (AA 8.9), `OpenGVLab/InternVL` family, `Shanghai_AI_Laboratory/Intern-S2-Preview` — all LLM `weak`/`none`, no coding benchmarks, AA <24 or null.
  * These demonstrate the expected behavior: pure vision models without coding evidence stay `drop` regardless of vision filter.

Impact: flipping the deterministic `vision` gate to conditional (keep if coding-capable + cheap) would **recover 4 unique coding-capable models (5 rows)** while **keeping 1 true VL-only (`Qwen3-VL-235B`) plus 1 borderline multimodal (`Qwen3.5-35B-A3B`) plus all 6+ LLM pure vision-only models dropped** — no regression for pure vision-only (`Qwen-Image-Edit`/`InternVL`/`ERNIE-VL` family).

## Scope: only `vision` vs other specialized patterns

Deterministic flags other than `vision` remain compulsory `drop` per #53 Out-of-scope.

Counts in current `data/results/*.yaml` across all providers:

| pattern | rows hitting | flag source | coding evidence? | pricing | disposition if conditional? |
|---|---|---|---|---|---|
| `embedding` (e.g., `Qwen/Qwen3-Embedding-0.6B/4B/8B`) | 3 (modelscope) | `model_id:embedding` substring (`Qwen3-Embedding`) | none — `coding_score null`, AA null or `qwen3-8b-instruct` AA 4.8 non-coding, Terminal Hard 15.2 weak | null or $0.31 | **Stay dropped** — no coding benchmark, pattern compulsory |
| `tts` / `text-to-speech` / `speech` / `audio` / `voice` | 0 in current results (no provider returned a TTS/voice id in this snapshot) | id or models_dev | — | — | **Stay dropped** if ever appears — audio modality is not coding |
| `safety` / `guard` / `moderation` / `rerank` | 0 in current results | id or models_dev | — | — | **Stay dropped** — safety/reranker never coding |
| `vision` | 7 rows (6 unique) | `models_dev:vision` | 5 rows strong (see table), 2 weak | $0.23–$1.13 cheap | **Conditional** — only this pattern gets the coding+pricing exception |

There is no overlap: the false-drop Qwen vision-language models are **not** also `embedding`/`tts`/`safety` — the `vision` exception does not weaken those gates.

Implementation scope recommendation: in `EvidenceCollector.collect`, detect `vision`-flagged models separately, then in `pipeline.evaluate_model` (or `EvidencePacket` helper), gate `is_specialized()` as: if `deterministic_flags == ["specialized_model:vision"]` (or contains `vision` as sole/modal pattern) **and** coding-capable + cheap → skip deterministic drop and let `PolicyGate` judge. If flags contain *any* other `specialized_model:*` (embedding/tts/safety/audio/voice) → always deterministic drop, regardless of coding score. This preserves compulsory drops for non-vision specialized models.

## Pricing analysis

* All 7 deterministic-vision models have cheap blended pricing via AA (`$0.23–$1.13`) once resolved via `ModelResolver` alias (YAML legacy stored `null` for 4 of 7 because `BenchmarkDataCache`/`ModelResolver` missed dot→hyphen alias pre-#50). Even the `null`-priced nararouter row qualifies per #53: "null pricing + strong score still qualifies" — so the `null` case does not block keeping `qwen3.8-27b`.
* Cheap threshold per #53: `<$1–2/1M` blended. All false-drop models are `<$1.13`, well under `$2`. The ultra-cheap `Qwen3.8-Flash-Next` ($0.23) and `Qwen3-VL-235B` ($0.70) show pricing alone does not discriminate coding vs non-coding — must be paired with coding evidence.
* True vision-only LLM controls have `null` pricing or low AA-based pricing but **no** coding evidence, so they stay dropped under the conditional rule.

## Threshold sketch for #53 follow-up (informative)

Based on this data, the conditional `vision` keep predicted in #53 could be:

> Keep if (`models_dev` or `model_id` flag is *solely* `vision`) **AND** (`coding_score ≥35` OR `swe_bench_verified ≥50` OR `swe_bench_pro ≥40` OR `aa_coding ≥45` OR `aa ≥50`) **AND** (`pricing blended < $2/1M` OR `pricing null AND (coding_score ≥45 OR aa_coding ≥45)`).

Applied to the table:
* 4 unique models (5 rows) satisfy → keep.
* 1 true VL-only (`Qwen3-VL-235B`) + 1 borderline multimodal (`Qwen3.5-35B-A3B`, not pure vision-only) fail coding side → stay dropped; latter would flip if supplemented.
* All 6+ LLM vision-only fail coding side → stay dropped.
* Embedding/tts/safety rows fail modality side → stay dropped (compulsory).

This keeps the change narrow, avoids regressions (issue #38 matrix showed dot→hyphen alias already correct for `glm-5.3` etc.), and matches the #53 fog note that null pricing needs a higher coding bar.

## Evidence

* `src/llm_discovery/evidence_collector.py:55-106` — `specialized_patterns`, `model_id` lower check, `models_dev` description check, normalized fallback with `-contributor`/`-next` stripping.
* `src/llm_discovery/evidence_packet.py:82-88` — `is_specialized()`.
* `src/llm_discovery/pipeline.py:28-45` — `evaluate_model` deterministic drop via `packet.is_specialized()` and `deterministic_drop_record`.
* `data/results/modelscope.yaml` — 6 deterministic `specialized_model:vision` drops (with `coding_score`/`benchmarks`/`pricing` shown), plus 3 `embedding` deterministic drops, plus 6+ LLM vision-only drops (Qwen-Image-Edit, InternVL, ERNIE-VL).
* `data/results/nararouter.yaml` — 1 deterministic `specialized_model:vision` drop (`qwen3.8-27b` coding_score 61.7, pricing null pre-alias).
* `data/artificial_analysis_models.json` — pricing/AA lookups via `ModelResolver` (e.g., `qwen3-5-27b` $0.825, `qwen3-8-flash-next` $0.23/55.8).
* `data/benchmarks.json` / `BenchmarkDataCache` — SWE Verified 72.x, SWE Pro 61.7, AA Coding 73.1 etc. populated for the 4 keep candidates.
* `data/models_dev_catalog.json` — `alibaba/qwen3.8-27b` etc. vision-language descriptions (source of deterministic flag).

## Out of scope / not changed

* NaraRouter true-free allowlist logic, general tier (flash/max) tuning beyond vision case — per #53.
* `audio`/`voice`/`tts`/`embedding`/`safety` deterministic drops remain compulsory (no conditional).
* `VL`/`image` id substrings not in `specialized_patterns` today — would require adding `image`/`vl` to catch `InternVL`/`Qwen-Image-Edit` deterministically; intentionally not added here because LLM already correctly drops them and adding would broaden scope beyond #54’s `vision` focus. Follow-up could consider `image`/`vl` as `vision` synonyms if comprehensive vision capture is desired.

