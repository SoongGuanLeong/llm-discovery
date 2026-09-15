# ADR 0008: Freeze evidence-level thresholds until Phase 1-4 readiness gate passes

## Status
Accepted — Issue #216 (parent #212 Wayfinder #206), 2026-09-15. No code change: this ADR documents and freezes the existing thresholds; any future tuning requires the four-artifact readiness gate and the proposal template.

## Context

~28% of discovered models (183/659) are evidence_level=weak and 55% are weak+none. Research #209 showed the search-budget lever (3→5) is tail-only (+5–10 moderate at most, hallucination risk unless the triangulation guard is hardened — done in #214), and #211 established that threshold tuning is premature until all evidence-recovery phases are measured. The deterministic thresholds live in a single seam — PolicyGate._deterministic_evidence_level — and are the only place a "weak is too high" fix could be made cheaply. Freezing them now stops the metric-gaming path (lowering AA/coding/bench floors to shrink the weak percentage) while legitimate recovery (alias, verified claims, search budget, router fix) is being measured.

## Decision

### 1. Frozen values (as of 2026-09-15, no code change)

Catalog/tier thresholds:

- MIN_SCORE = 24.0, MAX_SCORE = 45.0 (src/llm_discovery/benchmarks.py:106-107)

Deterministic evidence-level promotion (PolicyGate._deterministic_evidence_level, src/llm_discovery/policy_gate.py:401-458):

| Target | Condition | Rationale |
|--------|-----------|-----------|
| strong | coding_score >= 45 | high-coding boundary |
| strong | AA >= 55 | frontier AA alone, above MAX 45 |
| strong | AA >= 45 AND benchmark_coverage >= 0.25 | AA near-max plus one KEY_SIGNAL |
| strong | AA >= 50 AND coverage_with_supplements >= 0.08 | frontier-ish plus any supplement |
| strong | SWE/Terminal (incl. 2.1, SWE-bench Pro) >= 50 | direct agentic-coding proof |
| moderate | AA >= 24 | flash-band floor, aligns MIN_SCORE |
| moderate | coding_score >= 20 | meaningful signal below strong 45 |
| moderate | any supplement score >= 30 | avoids low-signal (aider 11.1) |
| moderate | verified provider claim (#213: allowlisted first-party URL + owner match + specific phrasing) | claim-only, never strong |
| weak | else | no deterministic signal |

Promotion semantics: hybrid max-only via _max_evidence_level (policy_gate.py:460-466); never demotes LLM strong; triangulation guard (policy_gate.py:284-305) demotes claim-only moderate→weak when no allowlisted URL (hardened to domain allowlist in #214).

### 2. No threshold edits until the four-artifact gate passes

Per #211, any tuning proposal is blocked until all four measurement artifacts exist and are linked:

- P1 alias: before/after histogram + collision audit (14-model claim, 12 strong + 2 moderate expected)
- P2 verified claims: allowlist + observed 2-4 moderate, marketing-only verified weak
- P3 search budget: A/B on the 20-model weak sample (this ticket, #216) with latency p50/p95, promotion, hallucination, guard demotion
- P4 router: ~5 router ids flipped weak→strong, no substring collision

### 3. Proposal must use the template

Any future threshold change ships with the copy-paste template at .scratch/research/216-threshold-proposal-template.md (or its body pasted into a new issue): distribution both, exact models changing, false-positive histogram (38 low-signal band), rationale per bound, rollback.

### 4. Rollback and monitoring

- Thresholds are constants in one seam (policy_gate.py). Revert = revert the commit that changed them; no store migration (slim v2 store does not persist evidence_level).
- Triangulation guard rate is the regression canary: demotion count must not spike (monitor the "TRIANGULATION GUARD" log lines per build).

## Considered Options

- Lower moderate floors now (AA>=15 / coding>=15 / bench>=20): rejected — would flip ~38 low-signal drops (aa 7-11 band, .scratch/wayfinder-weak-evidence/02-threshold-tuning-moderate.md) as false Keepers; no measurement behind it yet.
- Auto-tune after each build: rejected — no stable denominator, and weak records are re-evaluated every build by design (ADR 0006: weak never cached).
- Freeze only the strong thresholds: rejected — the moderate floors are where the hallucination/promotion risk concentrates; freeze all.

## Consequences

- weak percentage stays ~28% until P1-P4 land and a template-compliant proposal is accepted.
- #216's A/B result (adopts 5 or keeps 3) is a config decision (SEARCH_MAX_RESULTS env), not a threshold change — compliant with this freeze.
- Reviewers can reject threshold proposals mechanically: missing artifact = missing gate.

## References

- Issue #212 (spec), #215/#216 (tickets 3/4), research .scratch/research/211-threshold-readiness.md
- ADR 0006 (Keeper=strong only, coverage floor 0.25), ADR 0007 (Evidence Delta disabled)
- src/llm_discovery/policy_gate.py:401-458, src/llm_discovery/benchmarks.py:106-107
