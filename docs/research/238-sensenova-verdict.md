# Verdict on SenseNova uncertain — avoidable vs genuine

**Parent:** #263 Wayfinder: SenseNova evidence verdict — avoidable vs genuine uncertain
**Ticket:** #265
**Date:** 2026-09-18
**Status:** decision — cites research #264; no code, store, gate, or threshold change
**Fixed snapshot:** `data/artificial_analysis_models.json` sha `7024fb48004141fd` (652 models, 784143 B); `data/models_dev_catalog.json` sha `f624a90484edef45` (406 top-level models, 221 providers, 9215369 B); `data/benchmarks.json` sha `29ae5970dde79fd0` (769 keys, 397283 B)
**Evidence source:** research #264, branch `research/sensenova-evidence`, commit `f1dc86e` — findings `.scratch/research/sensenova-evidence.md`, raw record `.scratch/research/sensenova-datalearner-raw.json`
**Sources:** `data/model_candidate_store.json`, `src/llm_discovery/gate.py`, `src/llm_discovery/policy_gate.py`, `src/llm_discovery/verified_claim.py`, `src/llm_discovery/benchmarks.py`, `src/llm_discovery/catalogs.py`, `src/llm_discovery/evaluator.py`, `src/llm_discovery/build_gate.py`, ADRs 0006/0008/0009

---

## 1. Question and answer

**Question:** Is the `uncertain` result for the four SenseNova models — `sensenova-6.7-flash-lite`, `sensenova-6.8-flash-lite`, `sensenova-u1-fast`, `sensenova-u1.5-lite` — **avoidable** (admissible benchmark evidence exists and is reachable) or **genuine** (it does not)?

**Verdict: genuine.** Within the bounded budget — one DataLearnerAI pass scoped exclusively to SenseNova ids plus at most two targeted first-party searches — no admissible benchmark evidence exists for any of the four models. Both admissible-source families were reached and exhausted with a negative result. No KEY_SIGNAL (`aa_intelligence`, `swe_bench_verified`, `livecodebench`, `humaneval`) and no admissible supplement (`terminal_bench`, `terminal_bench_2_1`, `swe_bench_pro`) is available to lift `coding_score` off `null` or to reach `benchmark_coverage >= 0.25`.

Because the verdict is genuine, there is no implementation effort to specify: no pipeline code change, no store write, no Keeper promotion, and no Derived Cache rebuild would change this outcome without new external evidence. This ticket closes #263's destination as a decision document only.

**Scope:** SenseNova only. No other provider's result is touched, and no shared-semantics change (`BLOG_ALLOWLIST`, `MINI_TOKENS`, thresholds, gate floors, TTLs) is made. `config/providers.yaml` is untouched (user-only per `AGENTS.md`).

---

## 2. Per-model record

All four models share the same deterministic outcome and the same `evidence_hash`:

| Model | decision | tier | evidence_level | coding_score | aa_score | benchmarks | pricing | `evidence_hash` | `last_updated` |
|---|---|---|---|---|---|---|---|---|---|
| `sensenova-6.7-flash-lite` | `uncertain` | `uncertain` | `weak` | `null` | `null` | `{}` | `null` | `4ed96bbc580ff7d4` | 2026-09-17T13:39:31Z |
| `sensenova-6.8-flash-lite` | `uncertain` | `uncertain` | `weak` | `null` | `null` | `{}` | `null` | `4ed96bbc580ff7d4` | 2026-09-17T13:39:31Z |
| `sensenova-u1-fast` | `uncertain` | `uncertain` | `weak` | `null` | `null` | `{}` | `null` | `4ed96bbc580ff7d4` | 2026-09-17T13:39:30Z |
| `sensenova-u1.5-lite` | `uncertain` | `uncertain` | `weak` | `null` | `null` | `{}` | `null` | `4ed96bbc580ff7d4` | 2026-09-17T13:39:30Z |

Source: `data/model_candidate_store.json` (version 1, 450 entries) supplies `decision`, `tier`, `evidence_level`, `evidence_hash`, and `last_updated`; the null `coding_score` / `aa_score` / `benchmarks` / `pricing` fields are from the four-model `uncertain` record in `data/results/sensenova.yaml` as cited by the parent map. The four are `Candidate` records written by the evaluator's candidate-cache-hit path (`evidence_reason` / `recovery_attempts` = `candidate_cache_hit`, `evaluator.py:1302-1303`) under the 60–90d TTL from ADR 0009 / issue #222 — the LLM never ran, and re-evaluation is gated on a change of `evidence_hash`, which requires new evidence.

**Evidence found: none, for any of the four.** No `aa_model_id`, no AA score, no benchmark profile, no pricing, no allowlisted first-party claim. The `weak` → `uncertain` classification is produced deterministically by the cheap-recovery path (canonical benchmark alias → AA alias/canonical → models.dev → cached evidence → provider first-party claim → `PolicyGate` recompute), all of which return empty for these ids.

**Snapshot note.** `data/results/sensenova.yaml` is gitignored Ephemeral Report output, not a tracked artifact; the durable per-model record is the candidate store above. A later full run on 2026-09-18T10:03Z wrote a single provider-level `error` record (`model_id: sensenova`, `stage: discovery`, `HTTP 404 — endpoint may not exist`) into that file instead, so the latest ephemeral snapshot no longer carries the four rows. That is a discovery/transport state, not benchmark evidence, and it does not change this verdict — the `uncertain` classification is still what the pipeline produces for these ids whenever discovery succeeds.

---

## 3. The exhausted-source record

### 3.1 Artificial Analysis — zero hits under every identity variant

`data/artificial_analysis_models.json` (652 models, sha `7024fb48004141fd`) contains **zero** SenseNova entries. A scan of every `id` / `slug` / `name` / `model_id` string field (3912 fields) for `sense[\s_-]?nova`, `6[.\-_]?7[\s_-]?flash[\s_-]?lite`, `6[.\-_]?8[\s_-]?flash[\s_-]?lite`, `u1[\s_-]?fast`, `u1[.\-_]?5[\s_-]?lite` returns **0 hits**. With no AA match, `aa_model_id` is null and no AA-backed waiver of the gate floors is available.

### 3.2 `data/benchmarks.json` — zero hits under the same variants

The derived benchmark cache (769 keys, sha `29ae5970dde79fd0`) contains **zero** SenseNova keys. The same variant set returns **0 hits**. Consequently `BenchmarkDataCache.get` is empty for all four ids, and the `profile.scores`-empty branch in `policy_gate.py:179-181` yields `coding_score = None`, `benchmarks = {}`, and `benchmark_coverage: null`.

### 3.3 Identity variants tried

Across both source families the following identity variants were tried, after normalizing case and mapping `.` / `_` / whitespace to `-`:

- `sensenova`, `sense-nova`
- `6-7-flash-lite`, `6.7-flash-lite`, `6_7_flash_lite`
- `6-8-flash-lite`, `6.8-flash-lite`, `6_8_flash_lite`
- `u1-fast`, `u1_fast`
- `u1-5-lite`, `u1.5-lite`, `u1_5_lite`
- `sensenova-*` prefixed forms of the above, and `sensetime/sensenova-6.7-flash-lite`

The variant matcher was validated with negative controls: synthetic positives (`SenseNova 6.7 Flash Lite`, `sensenova-u1-fast`, `SenseNova U1.5-Lite`, `SenseNova-6.8-Flash-Lite`, `sensetime/sensenova-6.7-flash-lite`) all match; negatives (`Qwen3 Coder 30B`, `DeepSeek-V3.2`, `Claude Sonnet 4.5`, `GPT-5`, `u1-lite`, `nova-6.7-flash`) do not. The negative result is a real absence, not a matcher defect.

### 3.4 Scoped DataLearnerAI result — reachable, but no rows

`BenchmarkDataCache.collect_from_web` (`benchmarks.py:368`) and its `LEADERBOARD_URLS` (`benchmarks.py:524-546`) were exercised by a throwaway scanner that reuses the exact fetch + `<tr>` parse but is scoped to SenseNova identities and never loads or writes `data/benchmarks.json`. `collect_from_web` itself was **never called** (a global pass would re-score every cache entry).

- **Reachability: yes.** `httpx.get` to `www.datalearner.com` returns HTTP 200 with no TLS/DNS/proxy error (1.6–2.7 s per page). This is "reachable but no rows", explicitly not a network failure.
- **All 17 leaderboards fetched once.** Totals: **0 SenseNova rows parsed** and **0 identity hits in raw HTML** (including embedded Next.js JSON) across all 17 pages. Three URLs 404 (`terminal_bench`, `terminal_bench_hard`, `aa_coding`); the rest render.
- `aa_intelligence` is the full 297-row AA quality-index mirror and returns **0 SenseNova rows**, consistent with §3.1.
- Parser negative control: the swe-bench-verified page parses real rows (e.g. `('Claude Opus 5', 96.0)`, `('Claude Fable 5', 95.0)`), and the raw scan finds `claude` 61× and `gpt` 10× on the same page.
- **Honest limitation:** every non-AA leaderboard renders only the top ~30 rows of page 1; deeper paginated pages were not fetched because that would exceed the one-pass budget. The full-row AA mirror covers the only leaderboard whose deep rows are an admissible KEY_SIGNAL.

### 3.5 First-party search result — benchmarks published, none admissible

Exactly two searches were used, then the budget stopped.

**Search 1** — SenseNova 6.7 / 6.8 coding benchmarks:
- `github.com/OpenSenseNova/SenseNova6.7` has a `## Benchmarks` section, but the numbers exist only as `assets/benchmark_en.jpg`. The chart publishes PinchBench 92, ClawEval 60.8, τ³-bench 67.2, Deep Planning 26.9, NovaPPTBench 90.7, AIDABench 58.2, GPQA-diamond 86.2, AA-LCR 66, MathVision 85.5, OCRBenchV2 65.4. **None is a KEY_SIGNAL or admissible supplement** (GPQA-diamond is not in the admissible set; AA-LCR is long-context reasoning, not the AA Intelligence Index).
- `sensetime.com/cn/research/51170639/` (2026-05-08 press release) is qualitative only — no numeric benchmark and no admissible benchmark named.

**Search 2** — 6.8 / U1 / U1.5 model cards:
- `github.com/OpenSenseNova/SenseNova6.8` publishes **no benchmark scores at all**; evaluations are explicitly deferred ("Public demo materials and complete evaluation results will be added as they become available").
- `github.com/OpenSenseNova/SenseNova-U1` publishes image / vision / generation benchmarks only (MMLU-Pro, IFBench, MMMUPro, RealWorldQA, MMBench, HallusionBench, MathVista, GenEval, DPGBench, QwenImageBench, WISE, LongTextBench, CVTG-2K, BizGenEval, IGenBench, OPENING, UniMMMU, RealUnify, VBVR-Pro, ImgEdit, GEdit-Bench, WeEdit, RISEBench, OmniRef-Bench). **No admissible coding benchmark.** MMLU-Pro carries no weight in `ALL_SIGNAL_WEIGHTS` and is not a KEY_SIGNAL, so it cannot yield a `coding_score`.
- `www.sensenova.cn` is product blurbs only; it confirms `sensenova-u1-fast` is the accelerated U1 variant and `sensenova-u1.5-lite` is an image-creation model.
- Search 2 returned only third-party secondary pages (Baidu Baike, CSDN, 51CTO, techgogogo) beyond the first-party sources above; secondary pages are not admissible primary evidence.

### 3.6 models.dev — provider entry present, no benchmark fields, resolution gap

`data/models_dev_catalog.json` carries `providers.sensenova.models` with 5 entries (`sensenova-6.8-flash-lite`, `glm-5.2`, `deepseek-v4-flash`, `kimi-k3`, `deepseek-v4-pro`), all `cost: {input: 0, output: 0, cache_read: 0}` and **no benchmark fields**. Only one of the four in-scope models (`6.8-flash-lite`) appears at all. The provider-scoped entries are also unreachable through the catalog: `ModelsDevCatalog.models_for_provider` (`catalogs.py:71-77`) intersects with the top-level `models` map, which contains **0** sensenova ids (406 top-level models, 221 providers), and `ModelMatcher._build_catalog_index` (`model_matching.py:406`) reads only the top-level map — so the lookup returns `{}`. This is the same gap shared by 216 of 221 providers and is out of scope (#263 Out of scope). Free-tier pricing (`cost: 0`) is explicitly not admissible capability evidence.

**Conclusion of the record:** the one admissible machine-readable source (DataLearnerAI) is reachable but carries no SenseNova rows on any leaderboard; models.dev carries a provider entry but no benchmark fields and is unreachable through the provider-scoped lookup; the first-party publisher deliberately publishes non-coding-agent benchmarks for 6.7, defers evaluation for 6.8, and publishes only image/vision benchmarks for U1/U1.5. No admissible evidence exists within the bounded budget.

---

## 4. Structural blockers deliberately left unchanged (out of scope) and their cost

Two shared-semantics levers were deliberately **not** changed, per #263's standing preferences:

1. **`BLOG_ALLOWLIST`** (`verified_claim.py:22-56`) does not contain `sensenova.cn` or `sensetime.com`. `github.com` is allowlisted but owner-matched only, and `is_owner_matched_url` does not match repo `OpenSenseNova/SenseNova6.7` against model id `sensenova-6.7-flash-lite`, so the first-party GitHub cards cannot be promoted either.
2. **`MINI_TOKENS`** (`verified_claim.py:62`) includes `lite`, and `is_mini_variant` (`verified_claim.py:215-223`) is checked first in `has_verified_claim` (`verified_claim.py:225-228`), returning `False` before any URL check.

**Cost.** The three `lite` models — `sensenova-6.7-flash-lite`, `sensenova-6.8-flash-lite`, `sensenova-u1.5-lite` — have first-party claim promotion **structurally blocked regardless of the allowlist**: adding `sensenova.cn` to `BLOG_ALLOWLIST` would not unblock them, because the `lite` token short-circuits `has_verified_claim`. For `sensenova-u1-fast` the blocker is the allowlist/owner-match, not `MINI_TOKENS`.

That cost is **zero for this verdict**, for two independent reasons:

- The published first-party numbers are non-admissible (§3.5), so there is no claim to promote.
- Even a promoted verified claim tops out at `moderate`, and `moderate` still fails the Accurate-Enough Gate: `coding_score is None` fails floor 2 (`gate.py:126`) and `benchmark_coverage < 0.25` fails floor 5 (`gate.py:187-188`). `coding_score` is benchmark-derived only (`policy_gate.py:179-181`), and `moderate` requires `aa_model_id` (`gate.py:172-174`). No claim path produces either.

Both levers change shared evidence semantics across all 221 providers and are explicitly out of scope (#263 Out of scope; ADR 0008). Leaving them unchanged is the correct call: it changes no other provider's result, and it changes nothing for SenseNova.

---

## 5. No-collateral invariant and how it was checked

**Invariant:** a fixed-snapshot before/after run in which the per-provider diff is empty for every provider except `sensenova`.

**How it was checked.** This ticket and research #264 made no pipeline code change, no gate/threshold change, no store write, and no Derived Cache rebuild, so no new results were produced and the per-provider diff is empty for **every** provider — including `sensenova`; the "except `sensenova`" allowance is unused. Concretely:

- `git status` shows no tracked file modified by this effort; the only new file is this document. The working tree is otherwise clean.
- Research #264 never called `BenchmarkDataCache.collect_from_web` (§3.4) and never wrote `data/benchmarks.json`, `data/model_info_store.json`, `data/model_candidate_store.json`, or `data/derived/cache.db`. Its scanner is a throwaway script under `.scratch/` that reuses the fetch + parse in isolation and filters rows to SenseNova identities.
- No Keeper promotion occurred, so `Source of Truth` slim v2 (`data/model_info_store.json`) and `Derived Cache` (`data/derived/cache.db`) semantics are unchanged (ADR 0006, ADR 0009).

**If an implementation effort were ever opened**, the invariant would be re-established the same way #237 did: a fixed-snapshot before/after run compared with `scripts/audit_harness.py --baseline-dir/--current-dir` (metrics collected by `build_gate.collect_metrics`, compared by `build_gate.diff_metrics`), asserting an empty per-provider delta for every provider except the one in scope. That check is not required here because nothing ran.

---

## 6. If the verdict had been avoidable

Not applicable — the verdict is genuine. For completeness, the shape a separate implementation effort would have taken, had admissible evidence been found, is recorded only to close the branch: it would have been scoped to SenseNova ids, would have had to pass the Accurate-Enough Gate on real benchmark evidence rather than a threshold change, and would have had to re-establish the §5 invariant. None of it is done or specified here, and none of it is needed.

---

## 7. ADRs and invariants (no change in this verdict)

- **ADR 0006** (Source of Truth slim v2, Accurate-Enough Gate, `weak` never cached as `Keeper`): floors unchanged — `evidence_level == strong`/`moderate`, `coding_score != null`, pricing present or free-marker, `aa_model_id` or supplement ≥50 with http URL, `benchmark_coverage >= 0.25`, http URL present, no UUID/hallucinated `model_id`. `Candidate` never becomes `Keeper`.
- **ADR 0008** (threshold freeze): `MIN_SCORE 24.0` / `MAX_SCORE 45.0` (`benchmarks.py:106-107`) and the `PolicyGate` evidence-level ladder are unchanged. Free-tier generosity is not an admissible basis for a Keeper.
- **ADR 0009** (per-evidence TTL split): `Candidate` 60–90d `evidence_hash` TTL and `Record TTL` 14d unchanged; the four SenseNova records stay `Candidate` until `evidence_hash` changes.

No threshold, gate, TTL, allowlist, or Derived Cache change is made here. `config/providers.yaml` remains user-only per `AGENTS.md`.

---

## 8. References

- Issues: #263 parent map, #264 research (closed, this verdict's evidence source), #265 this verdict, #222 candidate-cache TTL, #209 web-search budget precedent, #237 residual-uncertain verdict (shape precedent), #241 published that verdict
- Research artifacts: `.scratch/research/sensenova-evidence.md`, `.scratch/research/sensenova-datalearner-raw.json`, `.scratch/research/sensenova_datalearner_scan.py` (branch `research/sensenova-evidence`, commit `f1dc86e`)
- Code seams: `src/llm_discovery/benchmarks.py` (`collect_from_web`, `LEADERBOARD_URLS`), `src/llm_discovery/gate.py`, `src/llm_discovery/policy_gate.py`, `src/llm_discovery/verified_claim.py`, `src/llm_discovery/catalogs.py`, `src/llm_discovery/model_matching.py`, `src/llm_discovery/evaluator.py`, `src/llm_discovery/build_gate.py`, `src/llm_discovery/candidate_store.py`
- Data: `data/model_candidate_store.json`, `data/artificial_analysis_models.json`, `data/models_dev_catalog.json`, `data/benchmarks.json`
