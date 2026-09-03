# Design version dash-to-dot normalization (4-5 -> 4.5) - Prototype (issue #40)

Part of #34 - Map: Raise evidence levels and fix version-dot normalization.

## Question

How should src/llm_discovery/model_matching.py:normalize_model_id be extended to correct version typos like claude-haiku-4-5 -> claude-haiku-4.5 while preserving intentional hyphens (e.g., claude-opus-4-8 is not 4.8)?

> Refactor note (ideal design): Don't make normalizer guess globally. Separate normalization from matching. Keep original ID untouched, parse versions explicitly, generate matching variants with confidence.

## Tactical fix (reverted)

Previous commit abcaa03 mutated normalize_model_id with whitelist _KNOWN_DOT_VERSIONS. Problem: growing exception list.

## Ideal design (implemented now)

Provider ID -> Parse model identity -> Generate safe variants -> Candidate AA -> Similarity scoring -> Best match + confidence

1. Keep original untouched: normalize_model_id is now pure (only lower, strip, preserve dots between digits, no dash->dot guessing).

2. Parse versions explicitly: 4.5 <-> 4-5, 3.7 <-> 3-7 detected as version-like in variant generator, not global replace.

3. Generate matching variants with confidence:

- exact normalized (1.00): claude-haiku-4-5
- version-format variant (0.95): claude-haiku-4-5 <-> claude-haiku-4.5, gemini-3.7 <-> gemini-3-7
- token reorder (0.90): claude-haiku-4-5 <-> claude-4-5-haiku
- date suffix removed (0.85)
- fuzzy similarity (0.70) via SimilarityScorer

Variants checked in confidence order; first AA hit wins.

### Before / After

| input | normalize (pure) | variants | AA match | method |
|---|---|---|---|---|
| claude-haiku-4-5 | claude-haiku-4-5 | 4-5, 4.5, 4-5-haiku, 4.5-haiku | claude-4-5-haiku | alias / variant 0.90 |
| claude-opus-4-8 | claude-opus-4-8 | 4-8, 4.8, 4-8-opus | claude-opus-4-8 | exact 1.00 (dot variant not used) |
| gemini-3.7-flash | gemini-3.7-flash | 3.7, 3-7 | gemini-3-7-flash | variant 0.95 |
| gpt-5.4 | gpt-5.4 | 5.4, 5-4 | gpt-5-4 | variant 0.95 |

Key: opus-4-8 keeps original, exact 1.00 wins, no false 4.8.

### Implementation

- Code: src/llm_discovery/model_matching.py:normalize_model_id (pure) + _generate_match_variants + ModelMatcher.match variant loop
- Tests: 108 passed
- Doc: this file

## Decision

Refactored to ideal design now per request. Tactical whitelist removed. No over-engineering beyond existing ModelNormalizer/SimilarityScorer.
