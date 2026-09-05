# Research: Completeness contract for keep entries in Ephemeral Report — slim Source of Truth gaps (issue #101)

Part of #99 — Wayfinder Map: Fix cache reuse producing incomplete keep reports.

## Question

Define what a complete keep entry in data/results/<provider>.yaml must contain vs what slim Source of Truth persists. Compare ProviderBatchWriter._to_record expectations against build_cached_keep_record output. Show which fields default to null via fallbacks and which gaps are masked vs visible in data/results/agnes.yaml. Determine Accurate-Enough Gate minimums for reuse.

## Method

Primary sources only:
- src/llm_discovery/pipeline.py:build_cached_keep_record (lines 120-165), _pricing_is_stale, _gap_fill_benchmarks
- src/llm_discovery/results.py:ProviderBatchWriter._to_record + write (lines 123-210)
- src/llm_discovery/model_info_store.py slim v2 schema ModelInfoRecord {benchmarks, pricing, _meta}
- src/llm_discovery/policy_gate.py:PolicyGate.apply — cold-run complete record
- docs/adr/0006-accurate-enough-gate-and-store-source-of-truth.md — gate floors
- CONTEXT.md — Keeper, Evidence Level, Accurate-Enough Gate, Record TTL
- Evidence: data/results/agnes.yaml (4 keeps) vs data/model_info_store.json agnes keys

## Findings

### 1. Destination completeness contract

Per map #99 Destination and cold-run path PolicyGate.apply + ProviderBatchWriter._to_record, every keep entry must have:

- model_id (normalized) + decision: keep
- tier (flash/max via categorize_model — not null)
- aa_model_id, aa_score (AA intelligence index; null only if supplement bench >=50 with URL)
- coding_score (from build_benchmark_profile + compute_coding_score; null fails gate)
- pricing {blended, input, output, per_provider_overrides, price_1m_* aliases} — present or free-marker exception
- benchmarks {scores, raw_benchmarks, benchmark_coverage, coverage_with_supplements} — coverage >=0.25, scores non-empty
- confidence (llm confidence or deterministic 1.0)
- evidence_level (strong required for Keeper)
- evidence (at least one http URL, no hallucinated domains)
- coding_assessment (llm judge struct)

Sources: src/llm_discovery/policy_gate.py:apply builds evaluation dict; src/llm_discovery/results.py:ProviderBatchWriter._to_record projects these keys; docs/adr/0006 section 3 lists gate floors.

### 2. What slim Source of Truth persists

Slim v2 per src/llm_discovery/model_info_store.py:7,252-310:

    {version: 2, models: {key: {benchmarks, pricing, _meta}}}

- ModelInfoRecord = {benchmarks: BenchmarkSnapshot, pricing: PricingSnapshot, _meta: StoreMeta{first_seen, last_updated, version:2}}
- No tier, aa_model_id, aa_score, coding_score, confidence, evidence_level, evidence, coding_assessment persisted
- from_provider_record maps only rec.benchmarks + rec.pricing into slim; backfill + merge_records union-max benchmarks, re-aggregate pricing
- Existence implies Keeper (strong) per pipeline.classify_hit

Consequence: cache hit cannot reconstruct 7 of 10 fields from store alone.

### 3. What build_cached_keep_record outputs

src/llm_discovery/pipeline.py:120-165 returns only:

    provider_model_id = raw_model_id (raw verbatim per #90)
    cache_key = normalize_store_key(raw_id)
    benchmarks = gap-fill only via _gap_fill_benchmarks
    pricing = re-average if stale else verbatim
    decision = keep, cached = True, cache_hit_level = strong, provider, source = cache, coding = True

Missing vs cold-run: tier, aa_model_id/aa_name/aa_slug, aa_score, coding_score, confidence, evidence_level, evidence, coding_assessment. Only benchmarks+pricing+identity preserved.

Pricing freshness: _pricing_is_stale via is_stale(_meta.last_updated, 14); if stale, _refresh_pricing_if_stale re-averages from fresh AA observations. Benchmarks: _gap_fill_benchmarks — null->fill only, immutable, raw_benchmarks union deduped.

### 4. ProviderBatchWriter._to_record fallbacks — masked vs visible gaps

src/llm_discovery/results.py:126-150 fallbacks:

| Field | _to_record expression | If cached missing | Masked? | agnes.yaml |
|---|---|---|---|---|
| model_id | _normalize_model_id(rec.provider_model_id or rec.model_id) | uses raw id | — | present |
| decision | rec.get(\"decision\",\"keep\") | defaults keep | Masked | keep |
| tier | _normalize_tier(rec.get(\"tier\", rec.get(\"category\"))) | None | Visible null | tier: null on all 4 |
| aa_model_id | rec.get(\"aa_model_id\") | None | Visible null | null on all 4 |
| aa_score | rec.get(\"aa_score\") | None | Visible null | null on all 4 |
| coding_score | rec.get(\"coding_score\") | None | Visible null | null on all 4 |
| pricing | rec.get(\"pricing\") | {} or dict | Masked if {} | agnes-2.5-pro has {per_provider_overrides:{}} only |
| benchmarks | stripped benchmarks dict | {} or gap-filled | Partial | flash scores:{} empty |
| confidence | rec.get(\"confidence\",0.9) | defaults 0.9 | Masked — looks valid | 0.9 on all 4 |
| evidence_level | rec.get(\"evidence_level\",\"strong\") | defaults strong | Masked — strong without evidence | strong on all 4 despite [] |
| evidence | clean_evidence(rec.get(\"evidence\",[])) | [] | Visible empty | evidence: [] on all 4 |
| coding_assessment | rec.get(\"coding_assessment\") | None | Visible null | null on all 4 |

Key insight: confidence and evidence_level defaults hide incompleteness. A cached keep appears strong 0.9 even though no LLM judged it, no URL, no tier. The 6 visible nulls/empties are the only signals that cache reuse is incomplete.

Evidence in data/results/agnes.yaml (2026-09-05):
- agnes-2.5-flash: benchmarks {scores:{}} empty, tier/aa/coding null, pricing present, evidence []
- agnes-2.5-pro: pricing {per_provider_overrides:{}} only (blended null), benchmarks aa_intelligence 49.1/aa_coding 62.3 present but coding_score null
- agnes-2.5-pro-alpha: distinct pricing blended 0.563 + benchmarks 39.7/58.8 (correct)
- agnes-2.5-pro-beta: shares pro scores 49.1/62.3 + pricing blended 0.15 (alias, see #100)

Store corroboration data/model_info_store.json:
- agnes-2.5-pro pricing truncated to per_provider_overrides only (no blended)
- agnes-2.5-flash benchmarks empty scores

### 5. Accurate-Enough Gate minimums for reuse

Per docs/adr/0006 section 3 + CONTEXT.md — all must pass for Keeper:

1. evidence_level == strong (moderate/weak never cached; promotion via PolicyGate allowed)
2. coding_score != null
3. pricing present OR free-marker (:free in id OR AA blended==0)
4. aa_model_id != null OR supplement bench >=50 (swe_bench_verified / terminal_bench / terminal_bench_2_1) + http URL in evidence
5. benchmark_coverage >=0.25 (KEY_SIGNALS: aa_intelligence, swe_bench_verified, livecodebench, humaneval)
6. evidence contains at least one http URL
7. model_id not UUID-shaped, not hallucinated domain (tokenmix.ai, callsphere.ai, benchlm)

TTL: per-record is_stale(_meta.last_updated, 14) — pricing re-average when >14d; benchmarks immutable gap-fill only.

### 6. Implications for map #99 fix

Destination requires cached hit produce identical completeness to cold run without LLM. Two paths (decision to #102/#103):

A. Stay slim: derive missing fields at cache-hit time — compute tier via categorize_model (needs aa_score/coding_score), compute coding_score from cached benchmarks + fresh build_benchmark_profile, resolve aa_model_id/aa_score via resolve_model against AA catalog, synthesize evidence URLs from BenchmarkSnapshot.scores[].source, stub coding_assessment, confidence from deterministic signals.

B. Extend store: persist additional fields in slim v2 — larger store, simpler hit, but widens Source of Truth beyond benchmarks/pricing and reintroduces audit duplication ADR 0006 removed.

Either way, build_cached_keep_record must be expanded and ProviderBatchWriter._to_record defaults for confidence/evidence_level must not mask gaps.

## Sources

- CONTEXT.md — Keeper/Candidate, Evidence Level, Benchmark Coverage, Source of Truth, Ephemeral Report, Accurate-Enough Gate, Record TTL
- docs/adr/0006-accurate-enough-gate-and-store-source-of-truth.md — strong-only, slim v2, gate floors, per-record TTL
- docs/adr/0007-incremental-build-invalidation-policy.md — Evidence Delta disabled, pricing TTL 14d, gap-fill only
- src/llm_discovery/pipeline.py:120-200 — build_cached_keep_record, _gap_fill_benchmarks, _pricing_is_stale, classify_hit
- src/llm_discovery/results.py:123-210 — ProviderBatchWriter._to_record + write, fallback defaults
- src/llm_discovery/policy_gate.py:apply — cold-run evaluation dict
- src/llm_discovery/model_info_store.py:252-310,403-530 — slim schema, PricingSnapshot/BenchmarkSnapshot, is_stale
- src/llm_discovery/benchmarks.py:40-110 — KEY_SIGNALS, coverage, coding_score weights
- data/results/agnes.yaml — 4 keeps with tier null, aa null, coding_score null, evidence []
- data/model_info_store.json — agnes keys pricing/benchmark gaps
