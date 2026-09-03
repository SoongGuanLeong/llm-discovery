# Gap analysis: supplemental benchmarks for weak models (issue #36)

Part of #34 — Map: Raise evidence levels and fix version-dot normalization. Blocked by #35 (closed).

## Question

Which supplemental benchmark sources from `docs/benchmark-research.md` (BigCodeBench 170 models, EvalPlus 125, LiveCodeBench, CRUXEval 43, EvoEval 51) actually cover the weak models in `data/results/llm7.yaml` and can lift `evidence_level` from weak to moderate/strong without new LLM search? Check current `BenchmarkDataCache.collect_from_local` merging (`benchmarks.py` `BENCHMARK_NAME_MAP`, AA vs `models.dev`) and the per-model `benchmarks {}` emptiness for weak entries. Estimate coverage gain if BigCodeBench + EvalPlus were ingested, and note ingestion cost (scraping, schema mapping, weight in `SIGNAL_WEIGHTS`). Recommend minimal viable supplement set that would flip the most weak cases (e.g., codestral-latest aider_polyglot 11.1, glm-5.3 no benchmarks, gemini variants).

## Current snapshot

- `data/results/llm7.yaml` at audit time: 23 keep, 23 drop_llm (14 weak, 1 moderate dark-beast). Current file on disk is empty (`provider: llm7` error 404) — analysis uses the audit snapshot from `docs/research/issue-35-evidence-level-audit.md` as source of truth.
- 14 weak `drop_llm` + 4 moderate (Inkling, Inkling-Small, gpt-oss, mistral-Nemo) — see table below.
- `data/benchmarks.json`: 699 entries (merged). `data/artificial_analysis_models.json`: 631 models. `data/models_dev_catalog.json`: 364 models.

## Current `BenchmarkDataCache.collect_from_local` merging

### Flow (`src/llm_discovery/benchmarks.py`)

1. **From `models_dev`** (364 models): iterates `models_dev.models[model_id].benchmarks[]`, maps raw `bm.name` via `BENCHMARK_NAME_MAP` to canonical (`swe_bench_verified`, `terminal_bench`, `aider_polyglot`, `gpqa_diamond`, etc.), writes `self._data[model_id] = {benchmarks, raw_benchmarks}`. Only entries where `canonical` exists are kept; unmapped names are preserved only in `raw_benchmarks` but do not contribute to `BenchmarkProfile.scores` or `compute_coding_score`.

2. **From `AA` catalog** (631 models): iterates `aa.models[].evaluations`, maps raw eval name via same `BENCHMARK_NAME_MAP` (only `artificial_analysis_intelligence_index` / `coding_index` / `coding_agent_index` map today). Uses `_normalize_model_key` to dedup against existing `self._data` keys — if normalized slug matches, merges benchmarks without overwriting; otherwise inserts new entry keyed by `slug`.

3. **Lookup** (`get` / `get_raw`): direct key hit, then normalized provider slug with alternates `{norm, norm.replace(".","-"), re.sub(r"(\\d)-(\\d)", r"\\1.\\2", norm)}` intersected against key alternates — handles systematic version-dot typo (glm-5.3 vs glm-5-3, mimo-v2.5 vs mimo-v2-5-0424) introduced in #35.

### `BENCHMARK_NAME_MAP` coverage today

20 mapped raw names -> ~10 canonicals used in scoring:

- AA: `aa_intelligence`, `aa_coding`, `aa_agentic`
- SWE family: `swe_bench_verified`, `swe_bench_pro`, `swe_bench_marathon`
- LiveCodeBench: `livecodebench`, `livecodebench_pro`
- HumanEval: `humaneval` (0 entries in cache — no source populates it today)
- Terminal: `terminal_bench`, `terminal_bench_2_1`, `terminal_bench_hard`
- Other: `aider_polyglot`, `gpqa_diamond`, `gpqa`, `mmlu_pro`, `mmlu`, `osworld`, `deepswe`, `scicode`, etc.

**Missing for supplements:** no entries for `BigCodeBench` / `BigCodeBench-Hard` / `HumanEval+` / `MBPP+` / `CRUXEval` / `EvoEval` / `LiveCodeBench` variants beyond the 2 mapped. Raw names from those leaderboards would fall through to `raw_benchmarks` only and never enter `ALL_SIGNAL_WEIGHTS`.

### `SIGNAL_WEIGHTS` / `ALL_SIGNALS` today

```python
KEY_SIGNALS = ("aa_intelligence", "swe_bench_verified", "livecodebench", "humaneval")
SIGNAL_WEIGHTS = {aa_intelligence:0.30, swe_bench_verified:0.35, livecodebench:0.20, humaneval:0.20}
SUPPLEMENT_WEIGHTS = {terminal_bench:0.25, terminal_bench_hard:0.25, aider_polyglot:0.25,
                      gpqa_diamond:0.15, swe_bench_pro:0.20, terminal_bench_2_1:0.25,
                      deepswe:0.20, osworld_verified:0.15}  # 8 supplements
ALL = 12 signals -> coverage denominator 12
```

- `benchmark_coverage = KEY_SIGNALS hit /4`, `coverage_with_supplements = ALL hit /12` — reported in `BenchmarkProfile.to_dict()` but before #35 only used for display, now used for hybrid promotion.
- `humaneval` weight 0.20 is dead weight — zero rows in `benchmarks.json` populate it.

### Actual cache distribution (699 entries)

| canonical | rows | source |
|---|---:|---|
| `aa_intelligence` | 617 | AA (89% of models) |
| `aa_coding` | 265 | AA + openrouter |
| `swe_bench_verified` | 40 | models.dev + HF (6%) |
| `terminal_bench` | 36 | models.dev |
| `aider_polyglot` | 31 | aider leaderboard |
| `scicode` | 30 | openrouter |
| `terminal_bench_hard` | 22 | ... |
| `livecodebench` | 4 | HF/medium (0.6%) |
| `humaneval` | 0 | none |
| `bigcodebench` | 0 | not ingested |
| `evalplus` (humaneval+/mbpp+) | 0 | not ingested |

Coding benchmark coverage <10% of models. `humaneval` + `livecodebench` nearly empty despite high model count in external leaderboards.

### AA vs models.dev responsibility split

- AA owns `aa_intelligence` (and `aa_coding`/`aa_agentic`) for 617 models — quality index, not coding task score.
- models.dev owns the coding signals (`swe_bench_verified`, `terminal_bench`, `aider_polyglot`, `gpqa_diamond`, etc.) but only for ~60 models that have explicit leaderboard entries.
- Gap: frontier models released after catalog snapshot (glm-5.3 family, gemini-3.8) appear in AA quickly but have no coding benchmarks yet in either catalog — their `benchmarks.json` entry is AA-only (`aa_intelligence` + `aa_coding`), `raw_benchmarks: []` for coding.

## Per-model `benchmarks {}` emptiness for weak entries

From audit + current `benchmarks.json` / `models_dev` / AA re-check (2026-09-03):

| model_id (llm7) | audit evidence_level | AA | current `benchmarks.json` entry | `EvidenceCollector` packet would contain | why empty |
|---|---|---|---|---|---|
| L3-8B-Lunaris-v1-Turbo | weak | None | none | `benchmarks: []`, `aa_match: None` | specialized small model, no catalog entry anywhere |
| XiaomiMiMo/MiMo-V2.5 | weak | 38 (mimo-v2-5-0424) | `xiaomi/mimo-v2.5-pro` has SWE 78.9 + GPQA 86.6 but `XiaomiMiMo/MiMo-V2.5` key miss | `aa_match` resolves via alias (38), but `cache.get("mimo-v2.5")` missed before #35 dot-fix; after fix alts resolve to mimo-v2.5-pro entry -> would now find SWE 78.9 | key mismatch dot vs hyphen + missing alias `mimo-v2.5 -> mimo-v2-5-0424`; models_dev has no `XiaomiMiMo/MiMo-V2.5` entry |
| chroma-v.46-flash | weak | None | none | `[]` | vision model, no coding benchmarks ever |
| claude-haiku-4-5 | weak | 24.1 (claude-4-5-haiku) | `anthropic/claude-haiku-4-5` in models_dev has SWE-Pro 39.45 | after #35 alt lookup, `cache.get` now hits SWE-Pro 39.45; before #35 missed | version-dot typo 4-5 vs 4.5 + missing AA alias `claude-haiku-4-5 -> claude-4-5-haiku` (now fixed) |
| codestral-latest | weak | None | `mistral/codestral-latest` -> `aider_polyglot 11.1` | `aider 11.1` (polarity NEGATIVE, coverage_with_supplements 0.08) | AA none, only low aider score; polarity threshold aider>=50 positive, <=15 negative -> negative evidence |
| gemini-3.1-flash-lite | weak | None (preview alias 25.6) | `gemini-3-1-flash-lite-preview` AA 25.6 but `gemini-3.1-flash-lite` key miss in benchmarks | `aa_match` via suffix strip `-preview` now resolves 25.6, but no coding | variant suffix `-lite` + preview alias |
| gemini-3.5-flash-low | weak | None | `gemini-3-7-flash-low` AA 50.9 exists but `gemini-3.5-flash-low` miss | none | variant suffix `-low` not stripped in old normalizer |
| gemini-3.8-flash-high | weak | 56 (via 3-7 alias) | `gemini-3-7-flash` AA 56 merged via alias heuristics; benchmarks AA-only | `aa_intelligence 56` but no coding benchmarks | benchmarks empty despite high AA -> now strong via hybrid `AA>=55 -> strong` (fixed in #35) |
| gemini-omni-flash | weak | None | none | `[]` | no catalog entry |
| glm-5.3 | weak (audit) | 59.5 | `glm-5-3` AA 59.5 + aa_coding 74.8 (AA-only entry) | `aa_intelligence` present -> now strong via hybrid (AA>=55) | audit said `benchmarks {}` but current cache has AA-only entry; dot vs hyphen was the gap, now fixed via alts |
| glm-5.3-flash | weak | 57.5 | `glm-5-3-flash` AA 57.5 + aa_coding 71.5 (AA-only) | same | same |
| mistral-Small-24B-Instruct-2501 | weak | None | none | `[]` | instruct model, no coding benchmarks in catalogs |
| seed-2.0-mini | weak | None | none | `[]` | no catalog entry |
| seedance-2.0-fast | weak | None | none | `[]` (specialized video flag) | video model |
| dark-beast-krea2 | moderate | None | none | `[]` + specialized flag | video |
| Inkling / Inkling-Small | moderate | 42.3 / 41.2 | AA-only `aa_intelligence` | moderate via AA>=24 | no coding benchmarks |
| gpt-oss | moderate | None | none | provider_claim maybe | no AA |
| mistral-Nemo-Instruct-2407 | moderate | None | none | `[]` | no AA |

**Takeaway:** 10 of 14 weak entries have `benchmarks: {}` or AA-only with no coding signal. The bottleneck is (a) missing catalog rows for coding tasks and (b) `BENCHMARK_NAME_MAP` not recognizing supplemental benchmark names. Normalization fixes in #35 close the alias gap for ~3 entries (mimo, claude, glm) but do not create new coding signals where none exist externally.

## Which supplemental sources would actually cover these weak models?

Based on `docs/benchmark-research.md` coverage counts and external leaderboard composition (static HTML + HF datasets), plus manual spot-check of model families:

| Weak model (flip target) | BigCodeBench (170) | EvalPlus 125 (HumanEval+/MBPP+) | LiveCodeBench 28 | CRUXEval 43 | EvoEval 51 | likely to flip evidence_level without new LLM search? |
|---|---|---|---|---|---|---|
| **codestral-latest** (aider 11.1, negative) | **Yes** — Codestral family is in BigCodeBench (complete/instruct) | **Yes** — HumanEval+ / MBPP+ leaderboards list codestral-22b, mamba, latest | Yes (small) | Yes | Yes | **Yes — EvalPlus humaneval+ ~75-85 would dominate**; releveraged as `humaneval` 0.20 weight + supplement alternative. Would move `weak -> strong` (coding_score>=45, bc>=0.25) or at least moderate. Highest-flip candidate. |
| **claude-haiku-4-5** (AA 24.1, now SWE-Pro 39.45) | **Yes** — Anthropic Claude-3/4 Haiku in BigCodeBench | **Yes** — Claude-3.5 Haiku in EvalPlus | Yes | Unlikely (reasoning) | - | **Yes** — BigCodeBench pass@1 ~40-55 would push coverage bc>=0.25 + AA>=24 -> `moderate->strong` via hybrid `AA>=45? no, but coding_score>=45` possible. |
| **XiaomiMiMo/MiMo-V2.5** (now SWE 78.9 via pro) | **Maybe** — BigCodeBench lists ~170 models, Xiaomi/MiMo not yet prominent (Chinese models rising) | **Maybe** — EvalPlus 125 includes qwen, deepseek, but MiMo not confirmed | Maybe (contest) | - | - | Already flips via models.dev SWE 78.9 after #35 fix; supplement adds redundancy but not needed. |
| **gemini-3.1-flash-lite / 3.5-flash-low / 3.8-flash-high / omni-flash** | **Yes** — Gemini-2.0/2.5/3.0 in BigCodeBench; 3.8 too new, but 3.7 alias would match | **Yes** — Gemini-1.5/2.0 in EvalPlus | Gemini in LiveCodeBench | - | - | **Yes for 3.8/3.5 variants** — EvalPlus humaneval+ ~70-80 + BigCodeBench ~45-55 would provide coding_score where none exists today. For 3.8 already strong via AA>=55, supplement turns AA-only strong into benchmark-backed strong (confidence). |
| **glm-5.3 / glm-5.3-flash** (AA 59.5/57.5) | **Unlikely short-term** — GLM-5 family released Aug 2025, BigCodeBench last update covers up to 2025-Q1 models; GLM-4 already there, GLM-5 not yet | **Unlikely** — EvalPlus leaderboard stops at ~2024 models | No | No | No | **No direct flip** — already flips via hybrid `AA>=55 -> strong` deterministically. Supplement not needed for level, but would add confidence if later evaluated. |
| **mistral-Small-24B-Instruct-2501** | **Maybe** — Mistral Small 24B may appear in BigCodeBench | **Yes** — Mistral in EvalPlus | - | - | - | **Maybe moderate** — humaneval ~40-50 would lift weak->moderate (coding_score ~25-35) but not strong. |
| **seed-2.0-mini / seedance-2.0-fast** | No | No | No | No | No | No — video/seed models absent from coding leaderboards. Keep weak (correct, specialized). |
| **L3-8B-Lunaris-v1-Turbo / chroma-v.46-flash** | No | No | No | No | No | No — specialized/vision, no coding evals anywhere. Keep weak. |
| **gpt-oss / mistral-Nemo** (moderate keep) | Yes (gpt-oss) | Yes | - | - | - | Already moderate via provider_claim/AA; supplement would reinforce but not needed for keep. |

**Summary:** BigCodeBench and EvalPlus would directly cover 5-7 of the 14 weak entries where the model family exists on external leaderboards (codestral, claude-haiku, gemini variants, mistral-small, possibly mimo). Frontier ZAI gemini/GLM models already rescued by hybrid AA>=55; the remaining ~6 weak entries (seed, chroma, lunaris, etc.) would correctly stay weak — they are specialized/video and absent from every coding leaderboard, so no supplement would flip them.

## Coverage gain estimate if BigCodeBench + EvalPlus ingested

Assumptions: ingest via static HTML + HF dataset API (no eval cost), normalize keys via `_normalize_model_key` alts, add `BENCHMARK_NAME_MAP` entries, assign weights as below.

| Metric | Before (current cache) | + BigCodeBench (170) | + EvalPlus (125) | Both (unique ~220-250, overlap ~30-40%) |
|---|---|---|---|---|
| Coding benchmark rows (non-AA) | 40 SWE +31 aider +36 terminal +4 livecodebench = ~170 rows / 699 entries (6-8% of models have coding) | +170 models x 2 modes (Full/Hard) = ~170-200 new entries | +125 models x 2 (HE+/MBPP+) = ~200-250 new entries | ~250 unique models with coding signal |
| Models with any coding_score | ~70 of 699 (10%) | ~180 (~26%) | ~160 (~23%) | ~220-250 (~32-36%) |
| `humaneval` population | 0 | 0 (BigCodeBench is separate) | ~125 (now non-zero, weight active) | 125 |
| `bigcodebench` population (new) | 0 | 170 | 0 | 170 |
| Weak entries that would gain `benchmarks {}` | - | codestral, claude-haiku, gemini-3.x, mistral-small (4-5) | same + gpt-oss (5-6) | **5-7 of 14 weak -> non-empty** |
| Weak -> moderate/strong flips **without LLM search** (via hybrid promotion on new coding_score/aa+coverage) | 0 (post-#35: glm + gemini-3.8 already strong via AA>=55, mimo via SWE) | +3 (codestral strong, claude moderate->strong, gemini-lite moderate) | +2-3 (mistral-small moderate, gemini-low moderate) | **+5-7 total** (est. 35-50% of weak pool) |
| `compute_coding_score` denominators | KEY 4, ALL 12 | KEY 5 (add bigcodebench), ALL 14 | KEY 5 (add humaneval+), ALL 14 | KEY 6, ALL 15-16 |
| Avg `coverage_with_supplements` for coding models | 0.08-0.15 | 0.15-0.25 | 0.15-0.25 | 0.20-0.30 |

Conservative: even with both sources, ~7-9 weak entries remain correctly weak (specialized/video/no coding eval). That is expected — not every dropped model should be promoted.

**Confidence:** BigCodeBench 170 and EvalPlus 125 counts are from `docs/benchmark-research.md` (2025). Actual unique overlap estimated 30-40% (both evaluate popular open models like Codestral, Qwen, Llama, Gemma). LiveCodeBench 28 would add marginal gain (+0-1 weak) but its contamination-free angle is orthogonal.

## Ingestion cost

### Scraping

| Source | Complexity (per `docs/benchmark-research.md`) | Primary URL | Fallback / API | ToS | Freshness |
|---|---|---|---|---|---|
| **BigCodeBench** | Low — static HTML table + HF Space Gradio; structured | `https://bigcode-bench.github.io/` + `https://huggingface.co/spaces/bigcode/bigcodebench-leaderboard` | HF dataset `bigcode/bigcodebench` for task metadata | Apache-2.0, open | Monthly/quarterly |
| **EvalPlus** | Low — static GitHub Pages table | `https://evalplus.github.io/leaderboard.html` | HF datasets `evalplus/humanevalplus_release`, `evalplus/mbppplus_release` | Apache-2.0/MIT | Monthly |
| **LiveCodeBench** | Low-Medium — date-filterable static HTML | `https://livecodebench.github.io/leaderboard.html` | HF `livecodebench/` | Academic | Quarterly |
| **CRUXEval** | Low — static | `https://cruxeval.github.io/` | HF `cruxeval/cruxeval` | MIT | Static (43 models) |
| **EvoEval** | Low — static | paper repo | HF | MIT | Static (51) |

All three top sources have no compute cost (scrape, no eval). SWE-bench excluded (high compute: Docker 120GB+, 16GB RAM). Existing `BenchmarkDataCache.collect_from_web` already scaffolds DatalearnerAI scraping with `httpx` + regex table parsing — similar parser reusable.

### Schema mapping (`BENCHMARK_NAME_MAP` + `SIGNAL_WEIGHTS`)

Additions needed for minimal viable set (BigCodeBench + EvalPlus):

```python
# benchmarks.py
BENCHMARK_NAME_MAP |= {
    # BigCodeBench
    "BigCodeBench": "bigcodebench",
    "BigCodeBench-Full": "bigcodebench",
    "BigCodeBench-Hard": "bigcodebench_hard",
    "BigCodeBench Hard": "bigcodebench_hard",
    "BCB Full": "bigcodebench",
    # EvalPlus
    "HumanEval+": "humaneval_plus",
    "HumanEval+ (EvalPlus)": "humaneval_plus",
    "MBPP+": "mbpp_plus",
    "MBPP+ (EvalPlus)": "mbpp_plus",
    "HumanEval": "humaneval",          # already there, now populated
    "MBPP": "mbpp",
    # LiveCodeBench already mapped
    # CRUXEval / EvoEval (phase 2)
    "CRUXEval": "cruxeval",
    "CRUXEval-O": "cruxeval_o",
    "CRUXEval-I": "cruxeval_i",
    "EvoEval": "evoeval",
}
```

Canonical keys to create: `bigcodebench`, `bigcodebench_hard`, `humaneval_plus`, `mbpp_plus` (and optionally `cruxeval`, `evoeval`). Each maps to a leaderboard column (pass@1 or pass@1_instruct). Store as `BenchmarkScore` with `metric: "pass@1"`, `source: leaderboard URL`, `score: float`.

### Weight in `SIGNAL_WEIGHTS` / `SUPPLEMENT_WEIGHTS`

Current `ALL_SIGNAL_WEIGHTS` has 12 entries; `humaneval` 0.20 is unused. Proposal for minimal set (keeps total weight meaningful after normalization):

- **Option A (preferred, minimal change):** keep `KEY_SIGNALS` as trio `aa_intelligence` 0.30 + `swe_bench_verified` 0.35 + `bigcodebench` 0.30 + `humaneval_plus` 0.20 + `livecodebench` 0.20 -> normalize by available (same as today). Add to `SUPPLEMENT_WEIGHTS`: `bigcodebench:0.30`, `humaneval_plus:0.25`, `mbpp_plus:0.20` (or fold `humaneval_plus` into `humaneval` canonical). This makes `compute_coding_score` sensitive to the new signals without renaming existing `humaneval` consumers.
- **Option B:** introduce `bigcodebench` into `KEY_SIGNALS` (makes it primary, coverage denominator 5), keep EvalPlus as supplement. Either works; Option A less churn.
- Normalize weights: `compute_coding_score` already normalizes by `total_weight` of available signals, so adding entries does not dilute existing scores unless both old and new present — then new signal pulls weighted average toward its value (desirable: more evidence).
- Update `benchmark_coverage` denominator if promoting to KEY (e.g., 4->5) and `coverage_with_supplements` to 14-15.

### Code changes (est. 1-2 days)

1. Extend `BENCHMARK_NAME_MAP` + add canonical constants.
2. Extend `ALL_SIGNAL_WEIGHTS` / `SIGNAL_WEIGHTS` or `SUPPLEMENT_WEIGHTS` (threshold discussion: should `bigcodebench>=40` be positive via `classify_benchmark_score`? Add `EvidenceSource.BIGCODEBENCH` threshold).
3. Add `BenchmarkDataCache.collect_from_bigcodebench()` and `collect_from_evalplus()` (or generalize `collect_from_web` with URL map) — reuse HTML regex + HF dataset fallback, write to `data/benchmarks.json` with atomic write.
4. Extend `EvidenceCategory`/`EvidenceSource` enums and `classify_benchmark_score` positive/negative thresholds for new sources.
5. Tests for `_normalize_model_key` alts covering new leaderboard model name formats.
6. No `live` eval cost; CI can refresh weekly via `scripts/refresh_benchmarks.py` (existing catalog refresh pattern).

### Risks

- Leaderboard HTML structure change -> mitigate via HF dataset API fallback (stable).
- Model name mismatches across sources (codestral-latest vs codestral-22b) -> reuse `_normalize_model_key` alts + fuzzy token match already in `collect_from_web` (`>=2 token overlap`).
- Frontier models not yet evaluated on external boards (glm-5.3) -> no gain, but hybrid AA>=55 already covers them.

## Recommendation: minimal viable supplement set

**Phase 1 (now): BigCodeBench + EvalPlus — the core duo.**

- **BigCodeBench first** (170 models, highest coding leaderboard coverage, practical tasks: completion/instruct/library usage, active monthly updates, low scrape cost). Flips codestral-latest (`aider 11.1 -> humaneval+ not needed, BigCodeBench alone ~45-60 -> moderate->strong`), claude-haiku-4-5, and gemini variants where BigCodeBench has scores. Weight: `bigcodebench` 0.30 primary or 0.25 supplement — either lifts `coding_score>=35` and `benchmark_coverage>=0.25` thresholds.
- **EvalPlus second** (125 models, augmented HumanEval+/MBPP+ with 80x more tests, trivial static scraping, complements BigCodeBench by covering the "standard" function-level generation that BigCodeBench does not). Flips codestral definitively (HumanEval+ is the canonical coding benchmark), and covers mistral/gemini families missing from BigCodeBench. Weight: `humaneval_plus` 0.25 supplement (or 0.20 primary via existing `humaneval` slot).
- Together they cover the **most weak cases that should flip**: codestral-latest, claude-haiku-4-5, gemini-3.1-flash-lite / 3.5-flash-low / 3.8-flash-high, mistral-Small-24B-Instruct-2501 (marginal), and reinforce mimo/gpt-oss. Est. 5-7 promotions without any new LLM search.
- Ingestion cost: low (static HTML, no Docker), schema + weight change only, no pricing/ToS risk (Apache/MIT).

**Phase 2 (next quarter, optional):** CRUXEval (43, code reasoning/execution) + EvoEval (51, evolved HumanEval variants) as reasoning supplements. They test execution/understanding, not generation, and would help only if reasoning gaps remain. Lower priority; small coverage, static leaderboards.

**Phase 3 (future):** LiveCodeBench (contamination-free, 28 public but more via PR) if temporal decontamination becomes a policy requirement. High academic value, limited immediate weak-model lift.

**Not recommended now:** SWE-bench Verified is already the primary via models.dev (40 rows); SWE-bench no public model leaderboard and evaluation cost extreme. Open LLM Leaderboard v2 explicitly excludes coding. Spider 2.0 agent-focused.

**Concrete next step:** file follow-up tickets to (1) extend `BENCHMARK_NAME_MAP` + weights, (2) add `collect_from_bigcodebench` / `collect_from_evalplus` with HF fallback, (3) add `EvidenceSource` thresholds, (4) re-run `discover_provider` for llm7 to verify 5-7 flips in `evidence_level` and `coding_score`.

## Evidence

- `src/llm_discovery/benchmarks.py` — `BENCHMARK_NAME_MAP`, `SIGNAL_WEIGHTS`, `SUPPLEMENT_WEIGHTS`, `_normalize_model_key`, `BenchmarkDataCache.collect_from_local` / `get` alts
- `data/benchmarks.json` (699 entries, canonical counts above) and `data/models_dev_catalog.json` (364 models) inspected via `python3 -c` counters
- `data/artificial_analysis_models.json` (631 models, AA Intelligence coverage 617)
- `src/llm_discovery/model_matching.py:normalize_model_id` and `EvidenceCollector.collect` (benchmark walk + provider_claims)
- `src/llm_discovery/policy_gate.py:_deterministic_evidence_level` (hybrid promotion thresholds post-#35)
- `docs/benchmark-research.md` — source table, 170/125/28/43/51 counts, feasibility matrix
- `docs/research/issue-35-evidence-level-audit.md` — weak/moderate table (14+4) used as input snapshot
