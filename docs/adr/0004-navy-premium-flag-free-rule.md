# ADR 0004: Navy.ai premium-flag free-model rule (provider-aware)

## Status
Accepted — Issue #60 Grilling (part of #58 Wayfinder), user confirmed 2026-09-04 — all Q1-Q5 recommendations accepted.

## Context
Navy.ai (https://api.navy/v1/models) marks free models via `premium: false` not id marker
(`FREE_MARKERS: :free/-free/_free//free`). Current \u0060_split_by_free_rule\u0060 / \u0060_is_free_model\u0060 only checks id substring, so navy free models dropped as non-free or mixed. Need unified rule preserving generic marker logic + NaraRouter allowlist precedent, while keeping other providers unchanged.

Navy entry already in \u0060config/providers.yaml\u0060 (base_url https://api.navy/v1, secret NAVY_AI_API_KEY). Discovery uses generic \u0060discover_models\u0060 / \u0060/v1/models\u0060; no dedicated strategy yet. Fog: exact navy response shape (id, premium boolean vs string, pricing, object), whether all free have premium:false reliably, any other discriminator.

Candidates: provider-aware vs generic premium check, signature change vs new helper, normalization preserve shape, config strategy vs param, precedence/fallback.

## Decision

### 1. Scope — navy-scoped provider-aware (Q1)
- Only \u0060navy_ai\u0060 does \u0060marker OR premium==false\u0060. Others marker-only.
- Mirrors NaraRouter provider-specific predicate pattern; avoids misclassify if other provider reuses \u0060premium\u0060 differently. Generic \u0060premium==false => free\u0060 can extend later if proven shared semantics.

### 2. Precedence + fallback (Q2)
- \u0060is_free = marker OR (premium is False)\u0060 — identity check, not truthiness.
- Missing key / None / string -> fallback to marker only; missing never auto-free.
- Only \u0060False\u0060 (bool) counts as free; \u00600\u0060 / \u0060"false"\u0060 handled only if live navy sample shows it — don't guess. Verify via sample capture before broadening.
- Precedence: marker wins regardless; premium false also wins even without marker. Both OR, no AND.

### 3. Normalization (Q3)
- Extend \u0060discovery._normalize_models\u0060 to preserve \u0060premium\u0060 when present: \u0060{"id", "name", "object", "premium"?}\u0060.
- Keep explicit key, not \u0060_raw\u0060 passthrough. Minimal, survives generic \u0060discover_models\u0060.
- If premium absent, omit key (not None) to distinguish missing vs explicit false.

### 4. Function seam (Q4)
- Extend signatures:
  - \u0060_is_free_model(model: dict|str, provider_name: str|None = None) -> bool\u0060 — tolerant both forms; if dict, checks \u0060model.get("id")\u0060 + \u0060model.get("premium") is False\u0060 when \u0060provider_name=="navy_ai"\u0060; if str, marker only.
  - \u0060_split_by_free_rule(models, provider_name: str = "") -> (keep, dropped)\u0060 — passes provider; default "" => generic marker-only, zero regression for others.
  - Deprecate str-only call path but keep compat.
- No new helper name; single seam.

### 5. Config + generalisation (Q5)
- No new \u0060discovery_strategy\u0060 for now. Navy keeps generic \u0060discover_models\u0060; \u0060_split_by_free_rule\u0060 provider-aware via param suffices.
- \u0060discover_provider\u0060 / \u0060discover_single_provider\u0060 pass \u0060provider_name\u0060 into free rule.
- If navy later needs dedicated endpoint/pagination, promote to \u0060discovery_strategy: navy_ai\u0060 like nararouter. \u0060free_rule: premium_flag\u0060 per-provider toggle deferred (over-generalizes).

## Consequences
- Navy free detection correct: marker OR premium false; paid (premium true/missing + no marker) dropped before LLM cost.
- Other providers unchanged (default param, marker-only).
- NaraRouter allowlist untouched; separate branch in pipeline (discovery_strategy nararouter) stays.
- Implementation tickets: #61 prototype (before/after matrix), #62 validation (regression + wire policy + tests/artifacts).

## References
- #58 Wayfinder: Navy.ai premium-flag free-model rule
- #60 Grilling: Define navy premium-flag rule design and branching (this ADR)
- #61 Prototype: Navy premium-aware free filter in discovery/pipeline
- #62 Task: Validate regression + wire updated navy policy
- src/llm_discovery/discovery.py:_normalize_models, src/llm_discovery/pipeline.py:FREE_MARKERS/_is_free_model/_split_by_free_rule, config/providers.yaml:navy_ai
