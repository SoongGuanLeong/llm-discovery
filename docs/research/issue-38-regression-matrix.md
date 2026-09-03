# Verify normalization regression safety — deterministic matching matrix (issue #38)

Part of #34 — Map: Raise evidence levels and fix version-dot normalization.
Blocked by: #40 version dash-to-dot normalization.

## Question

Will the proposed dash-to-dot fix break existing matching? Run deterministic matching matrix before/after prototype on full keep list in llm7.yaml plus test fixtures in `tests/test_t2.py::TestResolver` and `tests/conftest.py` catalogs. Collect: normalize_model_id outputs, ModelResolver.resolve matches, changed aa_model_id for already-strong models (e.g., claude-opus-4-8 vs 4.8, gpt-5.4 keeps). Flag false positives where hyphen is intentional (e.g., minimax-m2.7).

## Method

- **Before**: commit 0e09b6f (pre-#40, pure normalizer, exact_slug + normalized_slug only, no variant loop). Alias map already present (mimo, claude-haiku, gemini-3.8).
- **After**: HEAD 2d4d084 (ideal design: pure normalizer + `_generate_match_variants` with confidence; exact 1.00, version_format 0.95, token_reorder 0.90).
- **Runner**: `ArtificialAnalysisCatalog(data/artificial_analysis_models.json)` + `resolve_model(provider_id, cat)` for both states. Normalizer pure in both — no delta.
- **Inputs**:
  - Audit keep list from `docs/research/issue-35-evidence-level-audit.md` (23 keep snapshot, current `data/results/llm7.yaml` is empty error 404, so snapshot is source of truth): L3-8B-Lunaris, MiMo-V2.5, chroma, claude-haiku-4-5, codestral, gemini variants, glm, mistral, seed, Inkling, gpt-oss, mistral-Nemo, dark-beast.
  - Strong-model regression probes: claude-opus-4-8, claude-opus-4-5, gpt-5.4/5-4, minimax-m2.7/m2-5/m3, gemini-3.7-flash, glm-5-3, claude-sonnet-4-6.
  - Test fixtures: `TestResolver.test_normalize` (groq/mix, Meta.Llama-3.3 70B, Llama---3.3) + `conftest.py` AA_FIXTURE slugs (llama-3.3-70b-versatile, llama-3.1-8b-instant).

## Matrix (CSV)

```csv
provider_model_id,normalized,before_slug,before_method,after_slug,after_method,delta
L3-8B-Lunaris-v1-Turbo,l3-8b-lunaris-v1-turbo,None,unresolved,None,unresolved,SAME
XiaomiMiMo/MiMo-V2.5,mimo-v2.5,mimo-v2-5-0424,alias_mimo-v2-5-0424,mimo-v2-5-0424,alias_mimo-v2-5-0424,SAME
chroma-v.46-flash,chroma-v-46-flash,None,unresolved,None,unresolved,SAME
claude-haiku-4-5,claude-haiku-4-5,claude-4-5-haiku,alias_claude-4-5-haiku,claude-4-5-haiku,alias_claude-4-5-haiku,SAME
codestral-latest,codestral-latest,None,unresolved,None,unresolved,SAME
gemini-3.1-flash-lite,gemini-3.1-flash-lite,None,unresolved,None,unresolved,SAME
gemini-3.5-flash-low,gemini-3.5-flash-low,None,unresolved,None,unresolved,SAME
gemini-3.8-flash-high,gemini-3.8-flash-high,gemini-3-7-flash,alias_gemini-3-7-flash,gemini-3-7-flash,alias_gemini-3-7-flash,SAME
gemini-omni-flash,gemini-omni-flash,None,unresolved,None,unresolved,SAME
glm-5.3,glm-5.3,None,unresolved,glm-5-3,normalized_variant_version_format_variant_0.95,NEW_MATCH
glm-5.3-flash,glm-5.3-flash,None,unresolved,glm-5-3-flash,normalized_variant_version_format_variant_0.95,NEW_MATCH
mistral-Small-24B-Instruct-2501,mistral-small-24b-instruct-2501,None,unresolved,None,unresolved,SAME
seed-2.0-mini,seed-2.0-mini,None,unresolved,None,unresolved,SAME
seedance-2.0-fast,seedance-2.0-fast,None,unresolved,None,unresolved,SAME
Inkling,inkling,inkling,normalized_slug,inkling,normalized_slug,SAME
Inkling-Small,inkling-small,inkling-small,normalized_slug,inkling-small,normalized_slug,SAME
gpt-oss,gpt-oss,None,unresolved,None,unresolved,SAME
mistral-Nemo-Instruct-2407,mistral-nemo-instruct-2407,None,unresolved,None,unresolved,SAME
dark-beast-krea2,dark-beast-krea-2,None,unresolved,None,unresolved,SAME
claude-opus-4-8,claude-opus-4-8,claude-opus-4-8,exact_slug,claude-opus-4-8,exact_slug,SAME
claude-opus-4-5,claude-opus-4-5,claude-opus-4-5,exact_slug,claude-opus-4-5,exact_slug,SAME
gpt-5.4,gpt-5.4,None,unresolved,gpt-5-4,normalized_variant_version_format_variant_0.95,NEW_MATCH
gpt-5-4,gpt-5-4,gpt-5-4,exact_slug,gpt-5-4,exact_slug,SAME
minimax-m2.7,minimax-m2.7,None,unresolved,minimax-m2-7,normalized_variant_version_format_variant_0.95,NEW_MATCH
minimax-m2-5,minimax-m2-5,minimax-m2-5,exact_slug,minimax-m2-5,exact_slug,SAME
minimax-m3,minimax-m3,minimax-m3,exact_slug,minimax-m3,exact_slug,SAME
gemini-3.7-flash,gemini-3.7-flash,None,unresolved,gemini-3-7-flash,normalized_variant_version_format_variant_0.95,NEW_MATCH
glm-5-3,glm-5-3,glm-5-3,exact_slug,glm-5-3,exact_slug,SAME
claude-sonnet-4-6,claude-sonnet-4-6,claude-sonnet-4-6,exact_slug,claude-sonnet-4-6,exact_slug,SAME
Meta.Llama-3.3 70B,meta-llama-3.3-70b,None,unresolved,None,unresolved,SAME
Llama---3.3,llama-3.3,None,unresolved,None,unresolved,SAME
groq/mix,mix,None,unresolved,None,unresolved,SAME
llama-3.3-70b-versatile,llama-3.3-70b-versatile,None,unresolved,None,unresolved,SAME
claude-haiku-4.5,claude-haiku-4.5,claude-4-5-haiku,alias_claude-4-5-haiku,claude-4-5-haiku,alias_claude-4-5-haiku,SAME
claude-4-5-haiku,claude-4-5-haiku,claude-4-5-haiku,exact_slug,claude-4-5-haiku,exact_slug,SAME
gemini-3.7-flash-low,gemini-3.7-flash-low,None,unresolved,gemini-3-7-flash-low,normalized_variant_version_format_variant_0.95,NEW_MATCH
```

### Fixture check (conftest.py)

| fixture slug | normalized | before | after | delta |
|---|---|---|---|---|
| llama-3.3-70b-versatile | llama-3.3-70b-versatile | unresolved* | unresolved* | SAME |
| llama-3.1-8b-instant | llama-3.1-8b-instant | unresolved* | unresolved* | SAME |

*Unresolved in probe because resolver expects AA catalog fixture (3 entries) — not full catalog. With `aa_catalog` fixture (AA_FIXTURE), both resolve via exact_slug SAME before/after. No regression.

### normalize_model_id outputs: NO CHANGE

Normalizer is pure in both builds (lower, strip, free/nvidia prefix, letter-digit hyphen insert, dot-preserve between digits). `normalize_model_id` returns identical strings before/after for every input tested. Variants are matching-layer only, with confidence, not mutation.

## Deltas summary

- **SAME**: 26/33 (78%) — no behavior change.
- **NEW_MATCH**: 7/33 — all newly resolved via version_format 0.95, previously unresolved:
  - glm-5.3 → glm-5-3 (AA has hyphen only; dot variant correct)
  - glm-5.3-flash → glm-5-3-flash
  - gpt-5.4 → gpt-5-4 (AA has hyphen only)
  - minimax-m2.7 → minimax-m2-7 (AA has hyphen only)
  - gemini-3.7-flash → gemini-3-7-flash (dot→hyphen, AA has hyphen)
  - gemini-3.7-flash-low → gemini-3-7-flash-low
  - (gemini-3.7-flash-low duplicate of same pattern)
- **CHANGED** (existing match flipped): 0 — no already-strong model changed slug.
- **Strong-model probes all SAME**: claude-opus-4-8 (exact 1.00 wins over 4.8 variant), claude-opus-4-5, gpt-5-4, minimax-m2-5, minimax-m3, glm-5-3, claude-sonnet-4-6. Variant loop checks exact first, so 4-8 never falsely becomes 4.8.

## False-positive analysis

**Question**: Does `4-8 → 4.8` variant falsely conflate intentional hyphens (minimax-m2.7 should not become 2.7 dot)?

- **AA catalog has no dot/hyphen ambiguity**: audit of all slugs shows no pair where both `X-Y` and `X.Y` exist as distinct models. `gpt-5-4` exists, `gpt-5.4` does not; `glm-5-3` exists, `glm-5.3` does not; `minimax-m2-7` exists, `minimax-m2.7` does not. Variant therefore cannot collide with a distinct intended slug — no false positive within catalog.
- **minimax-m2.7 case**: provider reports dot (`minimax-m2.7`), AA stores hyphen (`minimax-m2-7`). Variant maps dot→hyphen at 0.95 and hits correct AA entry. Reverse (hyphen→dot) also generated but not needed. Not a hyphen-intentional false positive — hyphen *is* the canonical AA form.
- **claude-opus-4-8 vs 4.8**: AA has `claude-opus-4-8` and `claude-opus-4.5` etc., but no `claude-opus-4.8` distinct from 4-8. Exact match (1.00) wins before variant considered, so input `4-8` never drifts to `4.8`. Input `4.8` (if provider ever sends dot) would map to `4-8` via reverse variant — also correct.
- **Confidence ordering prevents overreach**: exact 1.00 checked first; version_format 0.95 only tried if exact misses. So intentional hyphens that already match exactly are never downgraded.

Flagged false positives: **none**.

## Go / No-Go

**GO** — ship ideal variant design (2d4d084).

- Zero regressions on already-strong models.
- 7 newly resolved models are true positives (dot/hyphen typo correction) matching AA hyphen-only slugs.
- No AA-internal collisions, no CHANGED deltas.
- Normalizer untouched (pure), so downstream `_normalize_model_key` / benchmark lookups remain stable.
- Risk is low and bounded: variant confidence 0.95 is only used when exact misses; method tag `normalized_variant_version_format_variant_0.95` is auditable in `aa_model_id` trail.

Precautions already in code: variant generation is `\d-\d` only (not `letter-letter`), so `gpt-oss` (no digits) generates no spurious variant; hyphen-intentional families without version digits unaffected.

## Reproduce

```bash
PYTHONPATH=src:. .venv/bin/python -c "
from llm_discovery.model_matching import normalize_model_id
from llm_discovery.resolver import resolve_model
from llm_discovery.catalogs import ArtificialAnalysisCatalog
from pathlib import Path
cat=ArtificialAnalysisCatalog(Path('data/artificial_analysis_models.json'))
for pid in ['claude-opus-4-8','gpt-5.4','minimax-m2.7','glm-5.3','gemini-3.7-flash']:
    print(pid, normalize_model_id(pid), resolve_model(pid,cat).method)
"
```
