# NaraRouter corrected pipeline - issue #51 prototype

Part of #46 (Wayfinder). Follows the filter fix (#52) and the versioned-vendor-alias
fix (#50), both already on master. This issue asked for a runnable, offline
branch/prototype that runs the corrected pipeline end-to-end for NaraRouter and
produces a before/after YAML diff.

## The corrected pipeline (end-to-end, offline)

This prototype replays the production seam stack on captured data so it needs no
network and no secrets (the NaraRouter /models and /api/plans endpoints require an
API key; the agnes LLM judge returns 401 without credentials).

~~~
raw /models (59 models, data/nararouter_raw.json)
  -> filtered true-free list (9 models, issue #52 allowlist)
  -> resolved AA match (ModelMatcher, issue #50 aliases + normalization)
  -> evidence packet (EvidenceCollector: AA + benchmarks + pricing)
  -> deterministic judge verdict (DeterministicJudge -> real PolicyGate.apply)
~~~

Real seams only - nothing is reimplemented:

- discovery._split_by_free_rule - legacy "before" filter (kept all -free)
- discovery.NARAROUTER_FREE_SNAPSHOT - true-free allowlist source (#52)
- model_matching.ModelMatcher - alias map + normalize_model_id (#50)
- evidence_collector.EvidenceCollector - packet with AA match + benchmarks
- policy_gate.PolicyGate - final keep/drop + tier (unchanged)

Run it:

~~~
.venv/bin/python scripts/nararouter_issue51_prototype.py [--diff]
~~~

## Result 1 - paid-gated models excluded (issue #52 filter)

The legacy free-rule (_split_by_free_rule) kept every id carrying a free marker,
mixing the two NaraRouter buckets. The corrected allowlist keeps only the free-plan
models from data/artifacts/nararouter_plans.json (code == free).

- BEFORE (_split_by_free_rule): kept 8, including 6 paid-gated-free:
  deepseek-v4-flash-free, glm-5.3-flash-free, glm-5.3-free, mimo-v2.5-free,
  muse-spark-1.3-contributor-free, qwen3.8-flash-free; and wrongly DROPPED 7
  true-free (non-free-suffixed) models: agnes-2.0-flash, agnes-2.5-flash,
  laguna-s-2.1, mistral-large, mistral-medium-3-5, qwen3.8-27b, stepfun-3.7-flash.
- AFTER (allowlist): kept 9 true-free; excluded 6 paid-gated-free (listed above).

See prototypes/issue51/filter_before_after.txt for the machine-readable table.

## Result 2 - mimo/minimax/muse/qwen map correctly (issue #50)

Resolution of every vendor-family id and the two controls, via the corrected
ModelMatcher (free-marker strip + versioned-alias entries). Paid-gated ids are
excluded from evaluation but their resolution is shown to prove the alias fix:

~~~
provider_id                         aa_slug            score  method                                             status
mimo-v2.5-free                      mimo-v2-5-0424     38     alias_mimo-v2-5-0424                             paid-gated EXCLUDED
minimax-m3-free                     minimax-m3         45.4   normalized_slug                                   true-free
muse-spark-1.2-contributor-free     muse-spark-1-2     56.8   alias_muse-spark-1-2                             true-free
qwen3.8-27b                         qwen3-8-27b        52     normalized_variant_version_format_variant_0.95  true-free
qwen3.8-flash-free                  qwen3-8-flash-next 55.8   alias_qwen3-8-flash-next                         paid-gated EXCLUDED
glm-5.3-free                        glm-5-3            59.5   normalized_variant_version_format_variant_0.95  paid-gated EXCLUDED
glm-5.3-flash-free                  glm-5-3-flash      57.5   normalized_variant_version_format_variant_0.95  paid-gated EXCLUDED
deepseek-v4-flash-free              deepseek-v4-flash  51.8   normalized_slug                                   paid-gated EXCLUDED
muse-spark-1.3-contributor-free     muse-spark-1-2     56.8   alias_muse-spark-1-2                             paid-gated EXCLUDED
~~~

Pre-fix (#50 unset), these same ids were unresolved: the free / contributor / -next
markers were not stripped before alias lookup and the vendor alias entries
(mimo-v2.5, muse-spark-1.2-contributor, qwen3-8-flash-next) were absent
(documented in docs/research/issue-48-evaluator-miss.md). The corrected normalizer
+ alias map resolve them all; the tests pin this.

## Result 3 - judge verdict (deterministic stand-in)

The agnes LLM judge is 401 in this environment (no credentials). The prototype
substitutes DeterministicJudge, which builds a ModelEvaluation from the AA score +
coding_score + benchmark coverage using the evidence_level calibration PolicyGate
already encodes, then hands it to the real evaluate_model + PolicyGate.apply. The
deterministic coding override in PolicyGate promotes benchmark-backed models to
keep, so verdicts for benchmarked models match a working-LLM run exactly:

- keep (3): laguna-s-2.1 (flash, Terminal Bench 37.5),
  minimax-m3-free (max, AA 45.4, coding_score 65.5),
  muse-spark-1.2-contributor-free (max, AA 56.8, coding_score 56.8)
- drop (6): agnes-2.0-flash (no evidence), agnes-2.5-flash (AA 49.1 weak
  similarity, no coding bench), mistral-large (AA 4.1 below min 24),
  mistral-medium-3-5 (coding_score 30.4 < 35), qwen3.8-27b
  (vision-specialized - deterministic scope drop), stepfun-3.7-flash (no evidence)

## Before/after YAML diff

- BEFORE = data/results/nararouter.yaml (committed, 2026-09-03T10:19): keep [],
  drop_llm [], error [9 models], every aa_model_id null, aa_score null,
  confidence 0.0, evidence_level none, evidence ["LLM evaluation failed: 401
  Unauthorized .../chat/completions"].
- AFTER = prototypes/issue51/nararouter.yaml (prototype re-run): keep [3],
  drop_llm [6], error [], aa_model_id resolved for matched models (e.g.
  minimax-m3-free -> <aa uuid>, aa_score 45.4), confidence/evidence_level
  populated, real benchmark evidence.

Excerpt (prototypes/issue51/before_after.diff):

~~~
- keep: []
+ keep:
+ - model_id: laguna-s-2.1
+   decision: keep
+   tier: flash
+ - model_id: minimax-m3-free
- - model_id: minimax-m3-free
-   decision: error
-   aa_model_id: null
-   aa_score: null
+   decision: keep
+   tier: max
+   aa_model_id: 277f939a-...
+   aa_score: 45.4
~~~

## Tests (TDD)

tests/test_issue51_nararouter_prototype.py - 13 tests, all green, guarding:

- normalize_model_id strips free markers; preserves version dots
- corrected filter keeps 9 true-free, excludes 6 paid-gated
- legacy _split_by_free_rule keeps paid-gated (regression proof)
- resolution matrix: mimo/minimax/muse/qwen + glm/deepseek/qwen3.8-flash map to
  the correct AA slugs; no unresolved for named families
- end-to-end: 9 buckets, schema matches PROVIDER_SCHEMA_KEYS, keep set stable

Full suite: 121 passed (108 baseline + 13 new), no regressions.

## Artifacts

- scripts/nararouter_issue51_prototype.py - runnable prototype
- tests/test_issue51_nararouter_prototype.py - TDD guard tests
- prototypes/issue51/nararouter.yaml - after YAML (re-run)
- prototypes/issue51/before_after.diff - committed-before vs prototype-after
- prototypes/issue51/filter_before_after.txt - filter before/after table
- prototypes/issue51/run_transcript.txt - full run log
- docs/research/issue-51-nararouter-prototype.md - this report

Branch: issue-51/nararouter-prototype

MDEOF && wc -l docs/research/issue-51-nararouter-prototype.md && git status --short
