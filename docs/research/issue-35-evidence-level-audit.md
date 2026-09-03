# Audit evidence_level assignment for weak entries in llm7.yaml (issue #35)

Part of #34 — Map: Raise evidence levels and fix version-dot normalization.

## Question
Why do 14 drop_llm + 2 keep entries stay at `evidence_level: weak` (and Inkling/mistral-Nemo at moderate)? Trace LLM judge SYSTEM_PROMPT guidance, EvidencePacket polarity/has_strong_evidence, EvidenceCollector, PolicyGate, BenchmarkProfile coverage.

## Snapshot
- `data/results/llm7.yaml`: 23 keep, 23 drop_llm (14 weak, 1 moderate dark-beast), 0 error
- Weak keep? No — weak are all drop_llm. Moderate keep: Inkling (42.3), Inkling-Small (41.2), gpt-oss (no AA), mistral-Nemo (no AA)

## Assignment path (current)
1. **LLM judge** `src/llm_discovery/llm.py:SYSTEM_PROMPT` — Returns `evidence_level` as free-form JSON field. No threshold guidance in prompt beyond generic "strong/moderate/weak/none". 2 web searches max. LLM invents level subjectively → hedging.
2. **EvidencePacket** `src/llm_discovery/evidence_packet.py:has_strong_evidence()` — `len(positive)>=2 OR (len(positive)>=1 and aa>=45)`. Positive defined via `classify_benchmark_score` thresholds (SWE>=40, Terminal>=50, etc). Packet carries `pricing` but `has_strong_evidence` not used for evidence_level anywhere.
3. **EvidenceCollector** `src/llm_discovery/evidence_collector.py` — Walks `BenchmarkDataCache` via `_normalize_model_key`, collects benchmarks, classifies polarity/category, collects `provider_claims` from models_dev descriptions containing coding keywords. AA match + pricing copied from resolution. For weak entries, benchmark lookup fails due to key mismatch (dot vs hyphen, version alias) → empty packet.
4. **PolicyGate** `src/llm_discovery/policy_gate.py` — Stores `llm_result.evidence_level` verbatim. Does deterministic **coding** override (coding_score>=35, SWE>=50, Terminal>=50) but no evidence_level override. Tier derived via `categorize_model` (AA + coding_score + flagship + pricing). Evidence_level flows unchecked.
5. **BenchmarkProfile** `src/llm_discovery/benchmarks.py` — `benchmark_coverage = KEY_SIGNALS (aa_intelligence, swe_bench_verified, livecodebench, humaneval) /4`; `coverage_with_supplements = ALL_SIGNALS/12`. Used for `compute_coding_score` weighted sum and reporting. Not used for evidence_level. Weak entries show 0.0/0.0 or 0.0/0.08 (aider only).

Root cause: evidence_level is LLM-only, no hybrid deterministic promotion. So high AA (glm 59.5) stays weak while Inkling 42.3 is moderate — inconsistent hedging.

## Table: weak/moderate -> missing signal -> fix lever

| model_id | evidence_level | AA | coding_score | benchmarks (coverage) | provider_claims | Missing signal | Bottleneck | Fix lever |
|---|---|---|---|---|---|---|---:|---|
| L3-8B-Lunaris-v1-Turbo | weak | None | None | {} 0/0 | none | AA + coding + claims | threshold (no signal) | keep weak — specialized, no fix |
| XiaomiMiMo/MiMo-V2.5 | weak | 38 (mimo-v2-5-0424) | None | {} 0/0 | none | SWE 57.2/GPQA 86.6 absent from packet | missing catalog data (key normalize: mimo-v2.5 vs mimo-v2-5-0424) | alias + benchmark key normalize (mimo-v2.5 → mimo-v2-5-0424) + evidence_level hybrid (AA>=24 + benchmark → moderate/strong) |
| chroma-v.46-flash | weak | None | None | {} 0/0 | none | AA, coding | missing catalog (vision model) | keep weak; add specialized detection if vision |
| claude-haiku-4-5 | weak | 24.1 (claude-4-5-haiku) | None | {} 0/0 | none | version-dot typo 4-5 vs 4.5 blocks AA originally + benchmarks empty | LLM hedging + normalization | systematic dash-dot correction + alias map + hybrid (AA>=24 → at least moderate) |
| codestral-latest | weak | None | 11.1 (aider 11.1) | aider 11.1 0/0.08 | none | AA, strong coding (score 11 low, polarity negative) | threshold (low score) | keep weak |
| dark-beast-krea2 | moderate | None | None | {} | none | AA + benchmarks | missing catalog | keep moderate/weak — specialized video |
| gemini-3.1-flash-lite | weak | None (preview alias 25.6 exists) | None | {} | none | AA alias missing | missing catalog (3.1 vs preview suffix) | alias / suffix strip + hybrid |
| gemini-3.5-flash-low | weak | None | None | {} | none | AA variant suffix | missing catalog / hedging | alias strip "-low" variant |
| gemini-3.8-flash-high | weak | 56 (via 3-7 alias) | None | {} | none | benchmarks empty despite high AA | LLM hedging (AA 56 alone should be moderate/strong) + catalog | hybrid promotion AA>=50 → moderate/strong + benchmark key alias |
| gemini-omni-flash | weak | None | None | {} | none | AA | missing catalog | keep weak |
| glm-5.3 | weak | 59.5 | None | {} 0/0 | none (ZAI) | benchmarks empty (key glm-5.3 dot vs glm-5-3 hyphen) | normalization + LLM hedging | dot→hyphen key fix + hybrid (AA>=50 → strong or at least moderate) |
| glm-5.3-flash | weak | 57.5 | None | {} | none | same | same | same |
| mistral-Small-24B-Instruct-2501 | weak | None | None | {} | none | AA | missing catalog / instruct not coding | keep weak |
| seed-2.0-mini | weak | None | None | {} | none | AA, benchmarks | missing catalog | keep weak |
| seedance-2.0-fast | weak | None | None | {} | none | AA, video specialized | threshold | keep weak (specialized) |
| Inkling | moderate | 42.3 | 42.3 | aa only 0.25/0.08 | coding claim | SWE/Terminal | threshold (AA alone → moderate, needs SWE for strong) | hybrid would keep moderate (correct) |
| Inkling-Small | moderate | 41.2 | 41.2 | aa only | coding claim | same | same | same |
| gpt-oss | moderate | None | None | {} | coding claim? | AA | missing catalog but provider claim | hybrid: provider_claim + AA low → moderate |
| mistral-Nemo-Instruct-2407 | moderate | None | None | {} | none | AA | missing catalog | keep moderate (borderline) |

Legend: bottleneck = LLM hedging | missing local catalog data | threshold. Fix lever = code change.

## Cross-cutting issues
- **BenchmarkDataCache key mismatch**: `_normalize_model_key` and `normalize_model_id` disagreed on dot handling (2.5 vs 2-5). Dirty fix preserves dots between digits via zzzdotzzz — correct direction. Need systematic hyphen↔dot interchange + date suffix strip (already in dirty diff).
- **AA alias table missing**: mimo-v2.5 → mimo-v2-5-0424, claude-haiku-4-5 → claude-4-5-haiku, gemini-3.8-flash-high → gemini-3-7-flash, deepseek flash date variants. Dirty diff alias_map covers this — keep but make regex-generic for version typo.
- **has_strong_evidence unused**: Should drive evidence_level promotion. Currently dead code for tier.
- **coverage signals unused for evidence_level**: benchmark_coverage / coverage_with_supplements reported but not consulted.

## Recommendation: evidence_level should be hybrid deterministic override (not LLM-only)

**Keep LLM as primary** but add deterministic promotion in PolicyGate (not replacement):

- LLM produces initial `evidence_level`.
- PolicyGate computes deterministic level from packet/profile:
  - **strong** if `coding_score>=45` OR `has_strong_evidence()==True` OR `(aa>=45 and benchmark_coverage>=0.25)` OR `(aa>=50 and coverage_with_supplements>=0.08)` OR `(aa>=50 and provider_claims)`
  - **moderate** if `aa>=24` OR `coverage_with_supplements>=0.08` OR `coding_score is not None` OR `provider_claims`
  - else weak/none
- Final = max(llm_level, deterministic_level) per ordering none<weak<moderate<strong. Never demote LLM strong; only promote weak/moderate. Add evidence entry explaining promotion.
- Rationale: fixes inconsistent hedging (glm 59.5 weak vs Inkling 42.3 moderate), compensates missing catalog via normalization fixes, respects frontier provider-claim case (glm-5.3 with AA 59.5 and ZAI claim should be strong even without benchmarks). Thresholds align with existing categorize min_score 24 / max 45.

**Normalization**: systematic, not just alias table:
- Regex: hyphen between digit-digit (`4-5` → `4.5`) when version context (letter- digit- digit or digit- digit), plus generic dot↔hyphen interchange for lookup (try both). Fallback alias table for known families (claude-haiku, mimo, deepseek, gemini-3.8) as before.
- Apply same logic to both `normalize_model_id` and `_normalize_model_key` + benchmark alias.

**Rollout**: forward-only via PolicyGate; backfill existing YAML via one-off re-run of `discover_provider` for llm7 — idempotent overwrite in ProviderBatchWriter (existing dirty diff already adds pricing).

## Decisions ready for #34
- Hybrid promotion (max) — not LLM-only, not deterministic-only.
- Systematic version-dot correction (regex + alias table) — already in dirty diff, keep.
- Thresholds: strong requires AA>=45 + any coding signal OR AA>=50 alone with claim/supplement; weak→moderate if AA>=24 alone.
