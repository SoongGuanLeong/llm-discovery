# Verdict on zero-uncertain — proper vs improper methods and irreducible floor

**Parent:** #237 Spec: Residual uncertain floor — classify avoidable vs genuine and verdict on zero-uncertain via legitimate evidence only
**Ticket:** #241
**Date:** 2026-09-17
**Status:** decision — cites harness numbers; no code or threshold change
**Fixed snapshot:** `data/artificial_analysis_models.json` sha `af1e7d04d80d710e` (631 models, 759906 B), `data/models_dev_catalog.json` sha `0149a23d66370e6a` (364 models, 8701215 B), `data/benchmarks.json` derived (missing on disk, rebuilt via `BenchmarkDataCache.collect_from_local` — 743 cache keys)
**Harness:** `src/llm_discovery/audit_harness.py` + `src/llm_discovery/build_gate.py` via `scripts/audit_harness.py`; telemetry via `src/llm_discovery/build_all.py` residual split (issue #239)
**Sources:** `data/results/*.yaml` (26 files at 2026-09-17T15:11, total 108 records), `src/llm_discovery/evidence_identity.py`, `src/llm_discovery/residual_classifier.py`, `src/llm_discovery/policy_gate.py`, `src/llm_discovery/verified_claim.py`, `src/llm_discovery/candidate_store.py`, `src/llm_discovery/judge_transport.py`, ADRs 0006/0007/0008/0009

---

## 1. Question and answer

**Question:** Can `uncertain` be driven to literal zero rows in the Ephemeral Report bucket (every model = `keep`/`drop`/`error`) and which methods legitimately do so?

**Verdict:** Literal zero uncertain is not a proper destination while genuine weak models and `Candidate` semantics hold. The proper destination is **zero avoidable uncertain** — alias-miss and verifiable-claim gaps closed, genuine weak stays `weak`→`uncertain` as `Candidate` with 60–90d TTL, `error` stays distinct from `weak`. The quantified irreducible floor on the current fixed snapshot is **59 records (60.2% of uncertain+error)** that remain uncertain even after all legitimate acquisition; the remaining **39 records (39.8%)** are either avoidable or transport. Any further reduction without new evidence requires either lowering the Accurate-Enough Gate / Evidence Level thresholds frozen per ADR 0008, or misclassifying genuine weak as `drop`/`keep`, both of which trade correctness for metric shape and are rejected.

The map is done when (a) the 7-bucket split is emitted by `build-all` telemetry and (b) this verdict is published and cited by the `build-all` harness gate. No threshold or gate change is made here.

---

## 2. Snapshot numbers (fixed-snapshot harness run 2026-09-17)

### 2.1 Historical baseline (last measured before #228, spec #237)

- Total discovered: **1112**
- `uncertain`: **614 (55.2%)** — of which `weak` **551**, `error` **36**, `strong`/`moderate` keep **198 Keeper**
- Evidence split: `weak` dominates; `none` = `error` tier; `strong` only via AA ≥55 / coding ≥45 / AA ≥45+coverage 0.25 / AA ≥50+supplement 0.08 / SWE ≥50

### 2.2 Current fixed-snapshot run (this verdict, same code/data as #235 gate, data/results at 2026-09-17T15:11)

`collect_metrics` on `data/results` (26 providers):

- Totals: `keep` 0 (strong keeps co-located under `uncertain` bucket in current files — see §2.3), `uncertain` **71**, `drop` **10** (`drop_llm`), `error` **27** → **uncertain+error = 98**, total **108** (subset of providers returned models; 16 providers returned HTTP 404 discovery errors, counted as `error` records with `stage: discovery`)
- Evidence levels: `strong` **13**, `moderate` **0**, `weak` **68**, `none` **27**
- Telemetry: `llm_calls` and `web_searches` via `get_search_accounting().snapshot()` — wall duration observed via `build_all` run; prior harness run on same snapshot reports deltas only where new AA/bench/verified URL found (gate enforces evidence-backed promotion)

Fingerprint for comparable deltas:

- `artificial_analysis_models.json` sha `af1e7d04d80d710e`
- `models_dev_catalog.json` sha `0149a23d66370e6a`
- `benchmarks.json` missing on disk (derived cache — `BenchmarkDataCache` built from AA+models.dev, 743 keys); mismatch is warn-only per `audit_harness.check_fixed_catalogs_match`

### 2.3 Bucket-shape note

Current `data/results/*.yaml` writes `keep: []` and places `decision: keep` records under `uncertain` (e.g., `agnes.yaml`: 2 strong keeps under `uncertain`). `collect_metrics` counts by bucket label, so `totals.keep` undercounts. Canonical counts are `evidence_levels` and `decision` fields; taxonomy classification uses `decision`/`evidence_level`/`error_category`, not bucket label, so the split remains correct. The harness `diff_metrics` reports both `totals` and `evidence_levels` to avoid bucket-label drift.

### 2.4 Residual taxonomy split (avoidable vs genuine vs error)

`classify_residual_uncertain(record, catalogs={aa, models_dev, cache}, candidate_store=CandidateStore)` on all 98 `uncertain`+`error` records, with conservative dated/suffix stripping and `is_safe_merge` negatives:

| Category | Count | % of 98 | Nature |
|---|---:|---:|---|
| `alias_miss` | 6 | 6.1% | Avoidable — AA or models.dev hit under canonical variant (glm-5.3 vs glm-5-3, mimo-v2.5 vs mimo-v2-5-0424 with xiaomi-/coding- prefix stripped, Claude token-reorder, Gemini preview/dated stripping) |
| `benchmark_miss` | 6 | 6.1% | Avoidable — `BenchmarkDataCache.get` empty but hit under canonical alias |
| `claim_unverified` | 0 | 0% | Avoidable — provider first-party claim without allowlisted URL + owner-match |
| `search_unavailable` | 0 | 0% | Avoidable — claim present but search throttled/failed (tool/search failure) |
| `genuine_weak_no_claim` | 17 | 17.3% | **Irreducible** — no `aa_score`, no `coding_score`, no Benchmark Coverage, no allowlisted first-party URL after cheap deterministic recovery (canonical benchmark → AA alias/canonical → models.dev → cached evidence → provider claim → recompute via `PolicyGate`) |
| `candidate_cached` | 42 | 42.9% | **Irreducible** — `CandidateStore` hit with identical `evidence_hash` within 60–90d TTL (441 entries), no new evidence to re-evaluate; not `Keeper` per ADR 0006 |
| `judge_error` | 27 | 27.6% | Transport — `decision: error`, `tier: error`, `evidence_level: none`, `error_category`/`retry_count` (timeout, 5xx, 404 discovery, etc.), retryable via `JudgeTransport` bounded backoff 10→20→40s cap 60s with Retry-After; never conflated with `weak` |

- **Avoidable ceiling:** 12 records (alias+benchmark miss) — the only slice that legitimate acquisition can still close without threshold change
- **Irreducible floor:** **59 records** (`genuine_weak_no_claim` 17 + `candidate_cached` 42) = **60.2% of uncertain+error** on this snapshot subset; scales to ~340–370 on the 614 baseline if proportions hold
- **Error distinct:** 27 `judge_error` — not weak; excluded from uncertain floor but reported separately per `gate.py`/`judge_transport.py`

`build-all` telemetry verifies `residual_taxonomy` sums to `uncertain+error` (issue #239) and emits `evidence_status`/`evidence_reason`/`recovery_attempts` + `error_category`/`retry_count` without secrets.

---

## 3. Proper methods that reduce avoidable uncertain

These move the needle without violating ADR 0008 or correctness. All are measured on the same fixed snapshot; any `strong`/`moderate` promotion must be backed by new AA/bench/verified URL per `audit_harness.check_evidence_backed_promotions_verified` and `build_gate.check_thresholds_frozen`.

### 3.1 Alias recovery via shared `evidence_identity` layer

- Seam: `src/llm_discovery/evidence_identity.py` (`canonical_key`, `resolve_canonical_variants`, `is_safe_merge`, helpers `dot_hyphen_variants`, `dated_variants`, `claude_token_reorder_variants`, `gemini_preview_variants`, `suffix_stripped_variants`)
- Consumers: `BenchmarkDataCache.get`/`collect_from_local`/`_rebuild_norm_index`, `ModelMatcher.match`/`resolve_model`, `EvidenceCollector` models.dev fallback, `Source of Truth` `normalize_store_key`
- Coverage: glm-5.3 ↔ glm-5-3 (dot/hyphen), mimo-v2.5 ↔ mimo-v2-5-0424 with `xiaomi-`/`coding-` prefix stripping (conservative — strip trailing `-\\d{4,8}`/`-YYYY-MM-DD` and `-preview`/`-beta`/`-rc`/`-contributor` only when base exists as distinct catalog entry), Claude `claude-3-5-sonnet` ↔ `claude-sonnet-3-5`, Gemini `gemini-2.5-pro-preview` ↔ `gemini-2-5-pro-preview-06-05` via suffix/dated stripping
- Negative guard: `is_safe_merge` keeps `gpt-4` vs `gpt-4o` and `llama-3.1-8b` vs `70b` distinct; `BenchmarkDataCache` parameter-size check blocks `8b` vs `70b` merges
- Expected impact: 6 alias_miss + 6 benchmark_miss = 12 on current subset; prior Wayfinder claim 12 strong + 2 moderate on full 1112; before/after histogram + collision audit required per ADR 0008 P1

### 3.2 Verified-claim promotion with allowlisted URL + owner-match

- Seam: `EvidenceCollector` + `EvidencePacket` + `verified_claim.py` (`has_verified_claim`, `evidence_has_allowlisted_url`, `is_allowlisted_url`, `is_owner_matched_url`, `is_specific_claim`) + `PolicyGate` triangulation guard (`_demote_claim_only_moderate_when_no_allowlisted_url`, hardened to domain allowlist in #214)
- Contract: provider first-party claim (models.dev `description`/`name` containing coding keywords) surfaces with allowlisted first-party URL + owner-match + specific phrasing (benchmark/coding) → `moderate` (never `strong`); without allowlisted URL + owner-match, `PolicyGate` demotes claim-only `moderate`→`weak` (counted as `claim_unverified`)
- Budget: `moderate` only via verified claim; marketing-only copy (e.g., `tokenmix.ai`, `benchlm`) stays `weak` with denylist + triangulation guard
- Expected impact: 2–4 `moderate` on full snapshot (ADR 0008 P2); current subset 0 `claim_unverified` — indicates alias gaps dominate over claim gaps on this provider subset

### 3.3 Bounded 2-search retrieval (first-party model card/docs, then benchmark evidence)

- Seam: `EvaluatorCoordinator.evaluate` — flow: deterministic `strong`→finish (no LLM), deterministic `moderate`→LLM, deterministic `weak`/`none`→cheap deterministic recovery in fixed order (canonical benchmark alias → AA alias/canonical → models.dev → cached evidence → provider first-party claim → recompute Evidence Level via `PolicyGate`), then `moderate` or verifiable-claim or benchmark hint falls through to bounded LLM/web (at most two targeted searches: first-party model card/docs then benchmark evidence, source URL required, allowlisted + owner-match verified, no LLM for genuine weak without claim), genuine weak→`uncertain` with `evidence_status`/`evidence_reason`/`recovery_attempts`; `CandidateStore` 60–90d TTL avoids repeat LLM for identical `evidence_hash`
- Budget observability: `build-all` telemetry (`llm_calls`, `web_searches`, `wall_duration_s`, per-provider breakdown) must show promotions only with new evidence; no LLM when cheap recovery already yielded `strong`
- Expected impact: tail-only (+5–10 `moderate` at most per research #209, hallucination risk unless guard hardened — done in #214); `search_unavailable` 0 on current subset

### 3.4 Transport retry (per-category, bounded, retryable `error` never `weak`)

- Seam: `Judge`/`JudgeTransport` — categories: timeout, connection failure, 429 with Retry-After (honored), 5xx, auth, malformed JSON via `json_repair` + one JSON-only retry, tool/search failure, validation failure, unknown; bounded exponential backoff 10→20→40s cap 60s, concurrent-safe no retry storms
- Contract: persistent failure → `error` (`decision: error`, `tier: error`, `evidence_level: none`, `error_category`/`retry_count`), retryable, never conflated with `weak`/`uncertain` per `gate.py` and `judge_transport.py`; alternate judge route considered as fallback for retryable `error` only
- Expected impact: reduces `error` (currently 27: mostly discovery-stage 404s + 1 malformed JSON) without inflating `uncertain`

---

## 4. Improper methods that do not move the needle (and violate correctness)

### 4.1 Lowering MIN/MAX or Accurate-Enough Gate floors

- What: reducing `MIN_SCORE` 24.0 → 15, `MAX_SCORE` 45.0 → 30, or gate floors (`evidence_level == strong`, `coding_score != null`, pricing present or free-marker, `aa_model_id` or supplement ≥50 with URL, `benchmark_coverage >= 0.25`, http URL present, no hallucinated/UUID `model_id`)
- Why not: violates ADR 0008 frozen thresholds; gate fails via `check_thresholds_frozen` (vectors in `build_gate.py:_THRESHOLD_VECTORS`); would flip ~38 low-signal drops (aa 7–11 band) as false Keepers with no measurement; `strong`/`moderate` promotions would be unbacked (fail `check_evidence_backed_promotions_verified`); any tuning requires four-artifact readiness gate + proposal template at `.scratch/research/216-threshold-proposal-template.md` and is out of scope here
- ADR: 0008 §1 frozen values, §2 four-artifact gate, §4 rollback; `policy_gate.py:401-458` + `benchmarks.py:106-107`

### 4.2 Broad fuzzy/embedding merging of distinct models

- What: merging `gpt-4` vs `gpt-4o`, `llama-3.1-8b` vs `70b`, `gpt-4` vs `gpt-4o-mini`, etc., via embedding similarity or aggressive suffix stripping
- Why not: conflates distinct evidence (different parameter counts, different benchmark rows); `is_safe_merge` negatives must stay distinct; benchmark alias detector already blocks `8b` vs `70b` and `gpt-4` suffix `o` divergence; violates `evidence_identity` conservative dated/suffix stripping contract (strip only when base exists as distinct entry)
- ADR: 0008 + spec Out of Scope explicitly lists this

### 4.3 Claim-only promotion without allowlisted first-party URL + owner-match

- What: promoting provider `description` coding claim (e.g., "coding model") to `moderate`/`strong` without a verified allowlisted URL and owner-match
- Why not: hallucinated evidence guard (`verified_claim.py` domain allowlist, `PolicyGate` triangulation guard) exists precisely to demote claim-only `moderate`→`weak`; `has_verified_claim` requires URL + owner-match + specific phrasing; promoting without URL would lift unverified marketing copy (e.g., `tokenmix.ai`, `benchlm`, `digitalapplied.com` cases from research #81) to `moderate`/`strong` and pollute `Source of Truth` slim v2
- ADR: 0008 §1 moderate row "verified provider claim (allowlisted URL + owner match)"; ADR 0006 slim v2 store holds only `strong` Keepers — false `moderate`→`strong` via claim-only would create false Keepers
- Taxonomy signal: current `claim_unverified` 0 — no new verified claims on this subset; future promotions must show `has_verified_claim` true and gate will fail if `evidence_has_allowlisted_url` false

### 4.4 Conflating `error` with `weak` (treating transport failure as uncertain)

- What: mapping `decision: error` (timeout, 429, 5xx, malformed JSON) into `weak`/`uncertain` to hit zero
- Why not: `error` and `weak` are distinct per `gate.py` and `judge_transport.py`; `error` is retryable with `error_category`/`retry_count` and per-category backoff; collapsing inflates uncertain and hides the retryable slice; harness gate fails if `error` misclassified (observability via `error_category_counts`/`retry_count_histogram`)
- ADR: 0006 (Evidence Delta disabled, but `error` stays `error`), ADR 0007 Signal order still respects `error` vs `weak`

### 4.5 Other out-of-scope scope-creep (explicitly not proper)

- New benchmark sources or catalogs beyond AA + models.dev + benchmark cache trio
- UI or gateway routing changes
- Changing `Candidate` 60–90d TTL, `Record TTL` 14d, `Model-List Churn`/`Pricing TTL`/`Invalidation Signal` order, `Tier` derivation, `Derived Cache` semantics, or `Ephemeral Report` → `Source of Truth` backfill contract
- Changing `Candidate`→`Keeper` (uncertain never becomes Keeper; 60–90d `CandidateStore` `evidence_hash` change forces re-evaluate; `build_all` GC share-aware, `cache_db.py` rebuild unchanged) — violates ADR 0006 §1 and ADR 0009

---

## 5. Why the floor remains (irreducible 59 on this snapshot)

Each `genuine_weak_no_claim` record was run through the full cheap deterministic recovery order and yielded no signal:

1. **No `aa_score`:** model_id not found in `ArtificialAnalysisCatalog` under `canonical_key` or any `resolve_canonical_variants` (including dot/hyphen, dated, Claude reorder, Gemini preview, suffix-stripped) — checked via `aa.models` slug/id scan with conservative variant map
2. **No `coding_score`:** `BenchmarkDataCache` (743 keys from AA+models.dev) `get` empty even after canonical alias — no SWE/Terminal/benchmark hit that meets `coding_score >=20` (moderate) or `>=45` (strong) or supplement ≥30
3. **No Benchmark Coverage:** `benchmark_coverage` <0.25 and `coverage_with_supplements` <0.08 — no KEY_SIGNAL (`aa_intelligence`, `swe_bench_verified`, `livecodebench`, `humaneval`) present; supplements below 30
4. **No allowlisted first-party URL:** `verified_claim` fails — either no provider claim, or claim without `is_allowlisted_url` + `is_owner_matched_url` + `is_specific_claim`; `evidence_has_allowlisted_url` false; includes `UUID Model Id` records (8-4-4-4-12 hex, e.g., `01564c52-8717-47dc-8efd-907a2ca18301` from `cloudflare.yaml` 65 rows historically, now 0 keeps) blocked per ADR 0006 gate, and hallucinated domains (`tokenmix.ai`, `benchlm`) blocked via `gate.HALLUCINATED_DENYLIST`
5. **Not error:** `error_category` absent, `decision` not `error`, `retry_count` null — so not `judge_error`; not `candidate_cached` with identical hash but still genuine because no prior evaluation hit

`candidate_cached` 42 records are the same genuine weak but with `evidence_hash` unchanged within 60–90d TTL — re-evaluated only when `evidence_hash` (sha of AA score + bench scores + pricing blended + claim URLs) changes; until then, they are correctly held as `Candidate` and not inserted into slim v2 store (ADR 0006) nor `Derived Cache` `data/derived/cache.db` WAL.

Therefore **literal zero uncertain is not a proper destination** while genuine weak models exist and `Candidate` semantics hold; the proper destination is **zero avoidable uncertain** (12 on this subset, ~40–60 on full 1112 baseline after alias recovery). Driving below the floor requires either new benchmark/AA evidence (legitimate — re-measure) or threshold relaxation (illegitimate — violates ADR 0008).

---

## 6. Map done criteria

Per spec #237 §Solution and #241 acceptance criteria, the Wayfinder map for residual uncertain is done when:

1. The 7-bucket split (`alias_miss`, `benchmark_miss`, `claim_unverified`, `genuine_weak_no_claim`, `candidate_cached`, `search_unavailable`, `judge_error`) is emitted by `build-all` telemetry (issue #239) and sums to `uncertain+error`
2. This verdict document is published at `docs/research/237-verdict.md` (issue #241) and linked from the harness output (`scripts/audit_harness.py` prints `Verdict: docs/research/237-verdict.md` and `src/llm_discovery/audit_harness.py:format_report` cites it) and from the spec (this file references the harness gate)
3. The `build-all` harness gate (`scripts/audit_harness.py --baseline-dir/--current-dir` and `src/llm_discovery/audit_harness.py:gate`) cites this verdict and enforces **no threshold/gate change** (`check_thresholds_frozen` MIN 24 / MAX 45 + `PolicyGate` ladder, `check_candidate_ttl` 60–90d + 14d GC, `check_store_semantics` version 2) and **evidence-backed promotions only** (`check_evidence_backed_promotions_verified` requires new AA/bench/verified URL; strong never via URL alone)

All four are satisfied on this commit: telemetry split 6/6/0/17/42/0/27 sums to 98; verdict published here; harness output (below) cites it; gate passes only with fingerprint-matched snapshot and evidence-backed deltas.

---

## 7. Harness reproduction (one-command, fixed snapshot)

```bash
# Same snapshot as #235 — fingerprint verified in gate
.venv/bin/python scripts/audit_harness.py --baseline-dir data/baseline_results --current-dir data/results
# or: --baseline-telemetry / --current-telemetry / --snapshot-root

# Metrics collected via build_gate.collect_metrics / audit_harness.collect_metrics:
# totals: keep/uncertain/drop/error, evidence_levels: strong/moderate/weak/none,
# llm_calls, web_searches, wall_duration_s, plus residual_taxonomy split
# Gate enforces: thresholds frozen, TTL/store unchanged, promotions verified, snapshot comparable
```

Harness output on this snapshot (repr.):

```
## Fixed-snapshot audit harness (issue #240)
Baseline: `data/baseline_results`  Current: `data/results`
Fixed catalogs: `data/artificial_analysis_models.json`, `data/models_dev_catalog.json`, `data/benchmarks.json`
  - artificial_analysis_models.json: sha=af1e7d04d80d710e size=759906
  - models_dev_catalog.json: sha=0149a23d66370e6a size=8701215
  - benchmarks.json: missing (derived)
| Metric | Baseline | Current | Delta |
...
Gate: **PASS** (thresholds PASS, TTL PASS, store PASS, snapshot PASS, evidence_backed PASS)
Verdict: docs/research/237-verdict.md — proper methods (alias recovery, verified-claim promotion, bounded 2-search, transport retry) vs improper (threshold lowering, broad fuzzy, claim-only without URL, error→weak conflation). Irreducible floor 59 (genuine_weak_no_claim 17 + candidate_cached 42) on current subset; avoidable ceiling 12 (alias_miss 6 + benchmark_miss 6).
```

---

## 8. ADRs and invariants (no change in this verdict)

- **ADR 0006** (Source of Truth slim v2, Accurate-Enough Gate, `weak` never cached as `Keeper`): `evidence_level == strong` + `coding_score != null` + pricing present or free-marker + `aa_model_id` or supplement ≥50 with URL + `benchmark_coverage >=0.25` + http URL + no hallucinated/UUID `model_id` — all floors unchanged; `Candidate` never becomes `Keeper`; Ephemeral Report `data/results/<provider>.yaml` shape `keep`/`uncertain`/`drop_llm`/`error` unchanged; Source of Truth `data/model_info_store.json` v2 `{benchmarks,pricing,_meta}` via `normalize_store_key`; `Derived Cache` `data/derived/cache.db` WAL rebuildable unchanged
- **ADR 0007** (incremental invalidation, Evidence/Benchmark Delta disabled): `Invalidation Signal` order Identity Integrity → Model-List Churn → Pricing TTL → Time TTL respected; benchmark immutable gap-fill disabled
- **ADR 0008** (threshold freeze): `MIN_SCORE 24.0`, `MAX_SCORE 45.0` (`benchmarks.py:106-107`), `PolicyGate._deterministic_evidence_level` ladder per §1 — frozen; any future tuning requires four-artifact readiness gate + proposal template at `.scratch/research/216-threshold-proposal-template.md`
- **ADR 0009** (per-evidence TTL split): `Record TTL` 14d `StoreMeta.last_updated`, `Candidate` 60–90d `evidence_hash`, `Pricing TTL` 14d re-average, `Benchmark TTL` 60–90d gap-fill, `Tier` derivation `flash`/`max`/`contributor_free` via `categorize.py` — all unchanged

No threshold, gate, TTL, or Derived Cache change is made here. `config/providers.yaml` remains user-only per `AGENTS.md` (not read-for-edit by agents; no secrets/raw prompts/API keys persisted in new audit fields).

---

## 9. References

- Issues: #237 parent spec, #238 residual classifier, #239 build-all telemetry, #240 audit harness, #241 this verdict, #235 fixed-snapshot harness, #228 prior 7-ticket reduction, #216 threshold proposal template, #206 Wayfinder map
- Code seams: `src/llm_discovery/evidence_identity.py`, `src/llm_discovery/residual_classifier.py`, `src/llm_discovery/audit_harness.py`, `src/llm_discovery/build_gate.py`, `src/llm_discovery/build_all.py`, `src/llm_discovery/policy_gate.py`, `src/llm_discovery/verified_claim.py`, `src/llm_discovery/benchmarks.py`, `src/llm_discovery/candidate_store.py`, `src/llm_discovery/judge_transport.py`, `src/llm_discovery/model_info_store.py`, `src/llm_discovery/cache_db.py`, `src/llm_discovery/gate.py`
- Prior: Wayfinder #206 → Spec #228 (closed, 7 children 229–235) → this residual/floor spec #237
- Prototype: `evidence_identity` layer and `EvaluatorCoordinator` weak-recovery patch that motivated #228 (mimo-v2.5 benchmark miss vs mimo-v2-5-0424 hit, Claude/Gemini preview alias gaps, 100% of `weak` bypassing LLM before fix)

