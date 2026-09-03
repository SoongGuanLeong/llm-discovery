# Decide evidence promotion thresholds (issue #37)

Part of #34 — Map: Raise evidence levels and fix version-dot normalization.

## Question

What rule should promote evidence_level? Current LLM prompt says confidence and evidence_level are LLM-judged, but deterministic signals exist (AA Intelligence threshold, SWE-Bench Verified >= 40, Terminal-Bench >= 50, provider_claims). Options:
- Keep LLM as sole judge and just improve its evidence via better packet (no code change to level)?
- Add deterministic post-processing (e.g., if AA match + coding_score positive -> force at least moderate; if 2+ positive coding benchmarks -> strong regardless of LLM)?
- Change SYSTEM_PROMPT example to anchor levels (strong = AA + 1 coding benchmark, moderate = AA or 1 benchmark, weak = claim only)?

Decide threshold table and whether PolicyGate should override evidence_level like it overrides coding/tier. Consider two moderate keeps (Inkling, Inkling-Small) that have AA 42 but no raw_benchmarks yet moderate not strong — what would flip them?

---

## Decision

Hybrid promotion (max) via PolicyGate + SYSTEM_PROMPT anchoring. Not LLM-only, not deterministic-only.

LLM remains primary judge, but PolicyGate computes deterministic level from AA + coding_score + coverage and promotes via final = max(llm_level, deterministic_level) (ordering none < weak < moderate < strong). Never demotes LLM strong; only promotes weak/moderate when signals justify.

This mirrors existing deterministic coding override (coding_score >= 35, SWE >= 50) — same seam, same rationale.

### Why not LLM-only?

- Audit shows inconsistent hedging: glm-5.3 (AA 59.5) stayed weak while Inkling (AA 42.3) got moderate for same signal class (AA-only packet).
- LLM has 2 web searches max and no threshold guidance beyond free-form strong/moderate/weak/none — hedging inevitable.
- has_strong_evidence() and coverage signals already exist but were dead code for evidence_level.

### Why not deterministic-only?

- LLM web search still needed for no-AA / claim-only models (gpt-oss, mistral-Nemo) where provider_claims matter.
- LLM captures qualitative nuance (provider-native exception, agentic system) not in benchmarks.
- Max preserves LLM strong when deterministic packet empty — no false demotion.

## Threshold table (deterministic side)

Aligns with categorize_model thresholds min 24 / max 45 and audit recommendation.

| Deterministic level | Condition (any) | Example |
|---|---|---|
| **strong** | coding_score >= 45 | Weighted AA+SWE+LiveCodeBench exceeds max threshold |
|  | aa >= 55 | Frontier AA alone |
|  | aa >= 45 && benchmark_coverage >= 0.25 | AA in max band + at least 1 KEY_SIGNAL |
|  | aa >= 50 && coverage_with_supplements >= 0.08 | High AA + at least 1 supplement |
|  | Any coding benchmark >= 50 (swe_bench_verified, terminal_bench, terminal_bench_2_1, swe_bench_pro) | Direct benchmark threshold |
| **moderate** | aa >= 24 | AA in flash band (min_score) |
|  | coding_score >= 20 | Meaningful coding signal but below strong |
|  | Any benchmark score >= 30 | Positive supplement even if AA missing |
| **weak** | else | No signal meets moderate |

provider_claims: not used as standalone deterministic signal for evidence_level (claim-only models stay weak unless AA or benchmark lifts them). Claim influences LLM moderate via web search.

## PolicyGate behavior

orig = llm_result.evidence_level
det = _deterministic_evidence_level(aa_score, coding_score, profile)
final = _max_evidence_level(orig, det)  # never demote

Implement in src/llm_discovery/policy_gate.py — already landed in commit 15f0992 (issue #35). Issue #37 confirms thresholds and adds SYSTEM_PROMPT anchoring.

## SYSTEM_PROMPT anchoring (change)

Add explicit evidence_level guidance to src/llm_discovery/llm.py:SYSTEM_PROMPT so LLM hedging aligns with deterministic thresholds even before promotion:

Evidence level guidance (deterministic thresholds shown for calibration):
- strong: AA Intelligence >= 55 alone, or AA >= 45 plus at least one coding benchmark (SWE-bench Verified >= 40, Terminal-Bench >= 50), or coding_score >= 45, or 2+ positive coding benchmarks
- moderate: AA >= 24 alone, or any single coding benchmark >= 30, or provider claim with supporting AA/coding signal
- weak: claim only, no AA, no benchmark >= 30
- none: no evidence at all

## Inkling case analysis

| Model | AA | coding_score | benchmarks | det level | Why not strong | What flips to strong |
|---|---|---|---|---|---|---|
| Inkling | 42.3 | 42.3 (AA-only) | {aa_intelligence} 0.25/0.08 | moderate via aa>=24 | aa 42.3 < 45, <55; coding_score <45; no SWE/Terminal >=50 | Needs coding_score >=45 OR aa>=45 + bc>=0.25 with real coding benchmark, OR SWE/Terminal >=50, OR aa>=55 |
| Inkling-Small | 41.2 | 41.2 | same | moderate | same | same |

Both correctly stay moderate. Hybrid keeps moderate — not over-promoted to strong on AA alone. Gap analysis (issue #36) shows BigCodeBench/EvalPlus ingestion could provide missing signal to flip them.

glm-5.3 contrast: AA 59.5 -> strong via aa>=55 alone (fixed hedging). claude-haiku-4-5: AA 24.1 -> moderate via aa>=24 (was weak due to LLM hedging + alias miss, now fixed).

## Rollout

- Forward-only via PolicyGate; backfill existing YAML via one-off discover_provider re-run for llm7 (idempotent overwrite).
- Tests: unit for _deterministic_evidence_level thresholds, integration: Inkling stays moderate, glm-5.3 promotes weak->strong, claude-haiku-4-5 weak->moderate.
