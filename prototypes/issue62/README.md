# Issue #62 validation — navy premium-flag regression

Date: 2026-09-04
Seams: discovery._normalize_models, pipeline._is_free_model/_split_by_free_rule/_has_free_name, discover_provider wiring

## Results
- Permanent tests: tests/test_navy_premium_free_rule.py 28 tests, all pass (5 normalize + 4 generic + 6 navy + 9 split + 4 wiring)
- Full suite: 161 tests (133 prior + 28 new). 160 pass, 1 pre-existing live-drift failure in test_issue51_nararouter_prototype::test_allowlist_equals_free_plan (live NaraRouter now returns longcat-2.0-free extra, snapshot 9 vs live 10). Not navy regression.
- Prototype matrix: scripts/issue61_navy_premium_prototype.py 19 cases + 5 split scenarios all OK (see prototypes/issue61/before_after.json)
- Generic markers unchanged, navy premium False => free, true/missing/None/"false"/0 => not free, marker wins, str fallback marker-only, non-navy premium ignored, cloudflare generic, NaraRouter isolated.

## Artifacts
- prototypes/issue61/before_after.json, filter_before_after.txt (prototype still green)
- prototypes/issue62/regression_tests.txt (28 pytest -v)
- tests/test_navy_premium_free_rule.py (permanent regression)
