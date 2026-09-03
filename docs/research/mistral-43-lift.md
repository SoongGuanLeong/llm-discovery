# Issue #43 — Mistral regeneration and lift measurement

**Date:** 2026-09-03  
**Parent:** #41 Wayfinder: Mistral evidence completeness  
**Depends on:** #42 resolver fix (commit 68a3cb4)  
**Regenerated:** `data/results/mistral.yaml` at 2026-09-03T08:22:24 (48 models)

## Summary

Regenerated Mistral report after resolver fix. Resolver now correctly maps `*-latest` and dated variants and strips slash prefixes in benchmark cache. Live discovery confirms keep count stabilizes at 2 strong models.

## Regeneration

- Command: `MISTRAL_API_KEY=... AGNES_AI_API_KEY=... DISABLE_WEB_SEARCH=1 PYTHONPATH=src:. .venv/bin/python -u scripts/discover.py mistral --all`
- Secrets: loaded via Infisical (`LLM_SHARED_PROJECT_ID=7686072c-85c7-4b7e-96e5-5bad8086cf44` contains MISTRAL_API_KEY, AGNES_AI_API_KEY)
- Output: `data/results/mistral.yaml` (gitignored, local)
  - 2026-09-03T08:22:24 — keep 2, drop 41, error 5, total 48
  - Keep: `mistral-medium-2604` (AA 30.4 / SWE 77.6, tier max), `mistral-medium-latest` (AA 30.4 / SWE 77.6, tier max)
  - Drop: 41 (including `codestral-2508` now drop vs previous keep uncertain; `codestral-latest` 11.1 aider, `magistral-*/pixtral/ministral` etc.)
  - Error: 5 transient LLM JSON failures (`labs-leanstral-1-5`, `mistral-code-latest`, `mistral-medium`, `mistral-ocr-3-0`, `mistral-ocr-4-0`) — retries exhausted, unrelated to resolver, not counted as keep/drop. Previous run (2026-09-03T08:15:48) had 4 errors, same keep 2.
- Previous file (2026-09-03T07:31:18, post-fix but pre-live): keep 3 (`codestral-2508` uncertain + 2 medium), drop 45, error 0. Live Judge now correctly drops `codestral-2508` (no AA, no coding_score) → net keep -1 vs that snapshot, but resolver gains remain.

## Quantified lift vs baseline (pre-#42)

Resolver simulation (old logic: no `*-latest` alias, dated stripped to generic, no slash direct lookup):

| Provider ID | Baseline resolution | Score | Fixed resolution | Score | Lift |
|---|---|---|---|---|---|
| `mistral-medium-latest` | `mistral-medium` | 3.2 | `mistral-medium-3-5` | 30.4 | +27.2 |
| `mistral-large-latest` | `mistral-large` | 4.1 | `mistral-large-3` | 15.9 | +11.8 |
| `mistral-small-latest` | `mistral-small` | 4.3 | `mistral-small-3-1` | 14.9 | +10.6 |
| `mistral-medium-2604` | `mistral-medium` | 3.2 | `mistral-medium-3-5` | 30.4 | +27.2 |
| `mistral-medium-2505` | `mistral-medium` | 3.2 | `mistral-medium-3` | 12.5 | +9.3 |
| `mistral-large-2512` | `mistral-large` | 4.1 | `mistral-large-3` | 15.9 | +11.8 |
| `mistral/mistral-medium-latest` | miss (no bare) | — | `mistral-medium-3-5` | 30.4 | from miss to hit |
| `mistral/mistral-medium-2604` | miss/generic | 3.2 | `mistral-medium-3-5` | 30.4 | +27.2 |

**Catalog coverage:**
- AA: 19 mistral slugs self-resolve 19/19 (exact_slug) after fix; baseline would have failed for 6 alias/dated cases.
- Benchmarks: 29 mistral keys (10 slash-prefixed `mistral/*` + 19 bare) now 29/29 cache hits via bare-slug direct lookup + preserved date handling. Baseline: slash-prefixed keys missed (19/29) and dated keys collapsed to generic, yielding wrong coding_score or miss.
- Coding_score: `mistral-medium-latest` and `mistral-medium-2604` now correctly get SWE-Bench Verified 77.6 (was miss or generic 3.2). `mistral-large-2512` and `mistral-small-2603` now get terminal_bench/scicode scores where previously miss.

**Keep/drop impact:**
- Baseline (pre-fix) keep ≈1 (`codestral-2508` uncertain) — medium variants would drop due to AA <24 and missing benchmarks.
- Fixed (post-#42, snapshot 07:31): keep 3 (2 medium max + 1 codestral uncertain) → +2 vs baseline.
- Fixed live (08:22): keep 2 (2 medium max, codestral now correctly dropped by Judge) → +1 vs baseline with codestral keep, +2 vs baseline if codestral excluded. Resolver lift stable; Judge refinement explains codestral change.

## vs llm7.yaml

`data/results/llm7.yaml` at 2026-09-03T08:16:47:
- keep 0, drop 0, error 1 (`llm7` — HTTP 404, discovery stage)
- Mistral keep 2 vs llm7 keep 0 → **+2 keep**
- Mistral has complete evaluation (48 models, 29 benchmark hits, 19 AA hits) vs llm7 discovery failure.

## Verification

- `tmp_quantify.py` (PYTHONPATH=src): 19/19 AA self-resolve, 29/29 benchmark hits, alias/dated tests OK.
- Tests: `PYTHONPATH=.:src .venv/bin/pytest` — 108 passed (including test_t2).
- No new sources yet (per #43, research after measurement).

## Next steps per #41

1. Resolver fix done (68a3cb4) → 2. Regenerate done (this report) → 3. Evaluate → 4. Add sources if needed → 5. Web-search policy
Next agent: evaluate whether 2 keeps (both medium-3.5) satisfies completeness or if supplemental benchmarks (mistralai/* HuggingFace) needed; consider openrouter aa_coding promotion policy.
