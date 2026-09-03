# Design version dash-to-dot normalization (4-5 -> 4.5) - Prototype (issue #40)

Part of #34 - Map: Raise evidence levels and fix version-dot normalization.

## Question

How should `src/llm_discovery/model_matching.py:normalize_model_id` be extended to correct version typos like `claude-haiku-4-5` -> `claude-haiku-4.5` while preserving intentional hyphens (e.g., claude-opus-4-8 is not 4.8)?

Prototype three options:

- **Option A**: regex digit-dash-digit -> digit.dot.digit only when family is known anthropic versioned family
- **Option B**: generate alternative alias list (try both hyphen and dot) and score candidates
- **Option C**: explicit alias table for known drifts

Prototype must show: before/after normalize outputs for llm7 samples (claude-haiku-4-5, claude-opus-4-8, gemini-3.7-flash, gpt-5.4), AA slug match result, and regression check against existing test_normalize assertions.

## Current behavior (before)

`normalize_model_id` preserves dots only when between digits (2.5 stays 2.5, stray dots become hyphens). No dash->dot correction.

| input | before normalize | AA expected | match before? |
|---|---|---|---|
| claude-haiku-4-5 | claude-haiku-4-5 | claude-4-5-haiku (AA hyphen, via alias) | alias hit, but normalize itself wrong |
| claude-opus-4-8 | claude-opus-4-8 | claude-opus-4-8 | exact, OK |
| gemini-3.7-flash | gemini-3.7-flash | gemini-3-7-flash (AA hyphen) | via alt dot->hyphen, OK |
| gpt-5.4 | gpt-5.4 | gpt-5-4 | via alt, OK |

## Prototype extension (after) - Option A implemented

### Code snippet (branch: master, src/llm_discovery/model_matching.py: normalize_model_id)

```python
    value = value.strip("-.")
    # Option A: systematic dash -> dot for known anthropic versioned families
    # Only correct when dotted version is known-good, preserving intentional hyphens like 4-8 -> not 4.8
    _ANTHROPIC_PREFIXES = ("claude-haiku-", "claude-sonnet-", "claude-opus-", "claude-")
    _KNOWN_DOT_VERSIONS = {"3.5", "3.7", "4.0", "4.1", "4.5", "4.6", "5.0", "5.1", "3.6"}
    if any(value.startswith(p) for p in _ANTHROPIC_PREFIXES):
        m = re.search(r"(\d)-(\d)(?=\b|-|$)", value)
        if m:
            dotted = f"{m.group(1)}.{m.group(2)}"
            if dotted in _KNOWN_DOT_VERSIONS:
                value = value.replace(f"{m.group(1)}-{m.group(2)}", dotted, 1)
    return value
```

Also re-uses existing Option C alias map and Option B alt generation in ModelMatcher.match:

```python
alias_map = {
    "mimo-v2.5": "mimo-v2-5-0424",
    "mimo-v2-5": "mimo-v2-5-0424",
    "claude-haiku-4-5": "claude-4-5-haiku",
    "claude-haiku-4.5": "claude-4-5-haiku",
    "gemini-3.8-flash-high": "gemini-3-7-flash",
}
# In match():
alts = {normalized}
alts.add(normalized.replace(".", "-"))
alts.add(re.sub(r"(\d)-(\d)", r"\1.\2", normalized))
alts.add(re.sub(r"(\d)\.(\d)", r"\1-\2", normalized))
```

### Before / After normalize outputs (PYTHONPATH=src:. python3)

| input | before (old) | after (new) | expected | pass |
|---|---|---|---|---|
| claude-haiku-4-5 | claude-haiku-4-5 | claude-haiku-4.5 | claude-haiku-4.5 | PASS |
| claude-opus-4-8 | claude-opus-4-8 | claude-opus-4-8 | claude-opus-4-8 | PASS preserved |
| gemini-3.7-flash | gemini-3.7-flash | gemini-3.7-flash | gemini-3.7-flash | PASS |
| gpt-5.4 | gpt-5.4 | gpt-5.4 | gpt-5.4 | PASS |

Additional edge checks:

| input | after | note |
|---|---|---|
| claude-haiku-4.5 | claude-haiku-4.5 | already dot, unchanged |
| gemini-3-7-flash | gemini-3-7-flash | gemini not anthropic -> stays hyphen, matcher alt handles |
| anthropic/claude-haiku-4-5 | claude-haiku-4.5 | provider prefix stripped then dot-corrected |

### AA slug match result (data/artificial_analysis_models.json)

| provider id | normalized (after) | AA match slug | method | correct? |
|---|---|---|---|---|
| claude-haiku-4-5 | claude-haiku-4.5 | claude-4-5-haiku | alias_claude-4-5-haiku | PASS |
| claude-opus-4-8 | claude-opus-4-8 | claude-opus-4-8 | exact_slug | PASS (not 4.8) |
| gemini-3.7-flash | gemini-3.7-flash | gemini-3-7-flash | normalized_slug_alt | PASS |
| gpt-5.4 | gpt-5.4 | gpt-5-4 | normalized_slug_alt | PASS |

### Regression check against existing test_normalize assertions (tests/test_t2.py:508)

```python
    def test_normalize(self):
        assert normalize_model_id("groq/mix") == "mix"
        assert normalize_model_id("Meta.Llama-3.3 70B") == "meta-llama-3.3-70b"
        assert normalize_model_id("Llama---3.3") == "llama-3.3"
```

Result after patch: all 3 PASS (verified via PYTHONPATH=src:. .venv/bin/pytest tests/test_t2.py -k test_normalize). Full suite: 108 passed.

## Comparison of options

| Option | mechanism | pros | cons | prototype result |
|---|---|---|---|---|
| A | regex digit-dash-digit only for known anthropic family + whitelist | systematic, minimal, preserves intentional hyphens (4-8 not in whitelist) | needs whitelist maintenance | implemented - PASS |
| B | generate alias list (try both hyphen and dot) and score candidates | no whitelist, handles any version | blind - would map 4-8 to 4.8 incorrectly | already in matcher alts - complements A |
| C | explicit alias table for known drifts | precise, auditable | manual, brittle | already in alias_map - complements A for reorder cases |

## HITL Decision

**Recommendation: Hybrid A + C + B-fallback (current implementation) = A in normalize + C alias_map + B alts in match.**

- A gives canonical normalize output (claude-haiku-4-5 -> 4.5) while preserving 4-8 via whitelist.
- C handles non-systematic drifts (mimo-v2.5 ordering, claude-haiku reorder).
- B remains as fallback alt generation for non-anthropic families.

## Branch / Artifact

- Code: src/llm_discovery/model_matching.py:normalize_model_id (Option A)
- Alias: src/llm_discovery/model_matching.py:ModelMatcher.alias_map (Option C)
- Alt generation: src/llm_discovery/model_matching.py:ModelMatcher.match alts (Option B)
- Tests: PYTHONPATH=src:. .venv/bin/pytest tests/ -q (108 passed)
- This doc: docs/research/issue-40-version-dash-dot-prototype.md
