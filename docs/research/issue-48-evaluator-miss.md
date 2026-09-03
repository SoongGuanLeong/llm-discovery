# Research: Diagnose LLM evaluator miss for mimo / MiniMax / Muse Spark / Qwen free aliases (issue #48)

Part of #46 — Evaluate why 5 free-marker provider IDs miss AA matches and split into "No AA match" drops vs "invalid JSON" errors. Read-only diagnosis; no code changes.

## Question

Investigate per ticket 48:

- normalize_model_id and ModelMatcher for ids mimo-v2.5-free, minimax-m3-free, muse-spark-1.2-contributor-free, qwen3.8-flash-free, glm-5.3-free vs AA catalog slugs
- AA catalog coverage via src/llm_discovery/catalogs.py and data/artificial_analysis_models.json
- Evidence packet and prompt built in src/llm_discovery/llm.py LocalLLMEvaluator._build_prompt
- Explain why drops show "No AA match" and why 2 show invalid JSON error

## Snapshot

- Commit: 4885f21 (master) + current worktree modifies config/providers.yaml only; source inspected at that commit
- AA catalog: 631 models, fetched 2026-09-02, version 4.1, tier free (data/artificial_analysis_models.json)
- Results reference: data/results/nararouter.yaml evaluated 2026-09-03T09:23:36 (agnes-2.0-flash, min_score 24, max 45)
- Catalog readers: ArtificialAnalysisCatalog.search/filter/get_by_id (catalogs.py:10-35) — simple substring, no normalization; ModelMatcher.match is canonical
- Matcher inspected: src/llm_discovery/model_matching.py normalize_model_id (pure, dot-preserving via zzzdotzzz) + _generate_match_variants (0.95 version_format, 0.90 token_reorder) + ModelMatcher.match alias_map -> exact -> exact_base -> normalized -> variants -> similarity fallback

Nararouter outcome for the 5 + 1 extra alias (same provider, same run):

| provider_model_id | decision | tier | aa_model_id | aa_score | method | evidence |
|---|---|---|---|---|---|---|
| mimo-v2.5-free | drop | drop | null | null | unresolved | No AA match or coding benchmarks ... Xiaomi MiMo-V2.5 variant |
| minimax-m3-free | drop | drop | 277f939a... (minimax-m3) | 45.4 | normalized_slug | AA score 45.4 exceeds minimum but no coding benchmarks ... unconfirmed |
| muse-spark-1.2-contributor-free | drop | drop | null | null | unresolved | none->weak via deterministic signals (aa=None) |
| qwen3.8-flash-free | drop | drop | null | null | unresolved | same none->weak |
| glm-5.3-free | error | error | cd684ea4... (glm-5-3) | 59.5 | normalized_variant 0.95 | LLM failed to return a final evaluation: invalid JSON after retries |
| muse-spark-1.3-contributor-free | error | error | null | null | unresolved | same invalid JSON |
| glm-5.3-flash-free (control) | keep | flash | 19496b81... (glm-5-3-flash) | 57.5 | normalized_variant 0.95 | keep |
| deepseek-v4-flash-free (control) | keep | max | fe4c08... (deepseek-v4-flash) | 51.8 | alias_deepseek-new | keep |

## 1. normalize_model_id vs ModelMatcher for the 5 ids

### 1.1 normalize_model_id (pure)

model_matching.py:21-48:

```
value = value.lower().strip()
value = value.rsplit("/",1)[-1]
value = re.sub(r"[:/_-]free$", "", value) # strips :free/-free/_free//free
if value.startswith("nvidia-"): value = value[len("nvidia-"):]
value = re.sub(r"([a-z]{2,})(\d)", r"\1-\2", value)  # qwen3 -> qwen-3 (>=2 letters)
value = re.sub(r"(\d)\.(\d)", r"\1zzzdotzzz\2", value)
value = re.sub(r"[^a-z0-9.]+", "-", value)
value = value.replace("zzzdotzzz", ".")
# stray dots -> hyphen, collapse
```

Verified outputs:

| input | normalize_model_id | _normalize_model_key (benchmarks.py:365) | variants |
|---|---|---|---|
| mimo-v2.5-free | mimo-v2.5 | mimo-v2.5-free (keeps -free!) | [mimo-v2.5@1.0, mimo-v2-5@0.95] |
| minimax-m3-free | minimax-m3 | m3-free (strips minimax- prefix, keeps -free) | [minimax-m3@1.0] |
| muse-spark-1.2-contributor-free | muse-spark-1.2-contributor | muse-spark-1.2-contributor-free | [1.2-contributor@1.0, 1-2-contributor@0.95] |
| qwen3.8-flash-free | qwen-3.8-flash | qwen-3.8-flash-free | [qwen-3.8-flash@1.0, qwen-3-8-flash@0.95] |
| glm-5.3-free | glm-5.3 | glm-5.3-free | [glm-5.3@1.0, glm-5-3@0.95] |

Divergence: normalize_model_id strips -free before any transform; _normalize_model_key does NOT (it only strips :free and -preview/-beta, plus provider prefixes nvidia-/llama-/minimax-/...). So BenchmarkDataCache lookups via _normalize_model_key always fail for -free aliases, even when AA match exists. All 5 profiles show scores: {} for this reason.

### 1.2 ModelMatcher cascade

model_matching.py:447-600 — order: (1) base_slug = re.sub(r"[:/_-]\d{4}$","",provider_slug) (4-digit), (2) DeepSeek hard-alias, (3) alias_map (7 entries), (4) exact slug==provider_slug, (5) dated Mistral map, (6) generic dated fallback, (7) exact slug==base_slug, (8) normalized_slug loop, (9) variant loop, (10) similarity scorer (HIGH 0.75, MEDIUM 0.50).

Per-id trace via resolve_model(pid, aa, models_dev, cache):

- mimo-v2.5-free -> unresolved — alias misses because check is provider_slug.lower() == "mimo-v2.5" but provider_slug = "mimo-v2.5-free" (free not stripped). base_slug also stays "mimo-v2.5-free" (no 4-digit suffix), so alias never fires. Exact on "mimo-v2.5-free"/base fails (catalog has mimo-v2-5-0424, mimo-v2-5-pro, etc.). Normalized mimo-v2.5 vs catalog normalized mimo-v2-5-0424 — no equal (extra -0424). Variants mimo-v2.5/mimo-v2-5 also no hit (need -0424). Similarity <0.50 due to date suffix. -> No AA match is expected with current alias_map, even though catalog does contain base mimo-v2-5-0424 (38). Fix would be alias_map key matching after -free strip, or dated-like alias without date.

- minimax-m3-free -> minimax-m3 normalized_slug — not via alias; exact normalized hit on slug minimax-m3 (AA id 277f..., 45.4). Works because _normalize("minimax-m3-free") strips free via matcher normalize -> hits catalog slug minimax-m3 normalized minimax-m3. So this one DOES resolve despite alias miss.

- muse-spark-1.2-contributor-free -> unresolved — normalized muse-spark-1.2-contributor variants muse-spark-1-2-contributor. Catalog has muse-spark-1-2 (56.8), muse-spark-1-1, muse-spark — all without contributor. No alias for contributor; suffix likely internal contributor-preview, not AA-tracked. Similarity token_overlap ~0.75 but weighted confidence 0.48 <0.50 threshold -> rejected. Correctly unresolved per current catalog; would need alias muse-spark-1.2-contributor->muse-spark-1-2.

- qwen3.8-flash-free -> unresolved — normalized qwen-3.8-flash (qwen is 4 letters so qwen3 -> qwen-3). Catalog nearest is qwen3-8-flash-next (55.8) and qwen3-5-omni-flash. Slug qwen-3.8-flash vs catalog slug qwen3-8-flash-next normalized qwen-3-8-flash-next — extra -next prevents exact normalized hit. Variant qwen-3-8-flash also missing -next. Alias_map has no qwen entry. -> Catalog gap: AA has -next, provider omits -next; need alias or fuzzy -next suffix strip.

- glm-5.3-free -> glm-5-3 via variant 0.95 — normalized glm-5.3 exact miss (glm-5.3 not a slug; catalog uses glm-5-3), variant glm-5-3 hits catalog id cd684... (GLM-5.3 max, 59.5). This resolves to base glm-5-3, not glm-5-3-flash (57.5). Control glm-5.3-flash-free similarly resolves via same variant to glm-5-3-flash (flash) and keeps. So free without flash correctly maps to base, with flash to flash.

## 2. AA catalog coverage

ArtificialAnalysisCatalog loads data/artificial_analysis_models.json (631 models). Relevant slugs:

- mimo: [mimo-v2-5-pro-non-reasoning, mimo-v2-0206, mimo-v2-5-0424, mimo-v2-5-pro, ...] (9 slugs, hyphenated, dated only for base 0424)
- minimax: [minimax-m3, m2, m1-40k, m2-1, m2-5, m2-7, m1-80k] — plain m3 exists at 45.4
- muse: [muse-spark-1-2 (56.8), muse-glimmer, muse-spark-1-1 (53.2), muse-spark (44.3)] — no contributor variants, no dot, hyphenated 1-2
- qwen flash: [qwen3-8-flash-next (55.8), qwen3-5-omni-flash] — no qwen3.8-flash without -next, no dot
- glm: [glm-5-3-flash 57.5, glm-5-2 52.6, glm-5-3 59.5, glm-4.5 (dot preserved!), glm-4-7-flash ...] — both flash and base for 5.3, hyphenated 5-3, but glm-4.5 keeps dot

Alias targets checked: mimo-v2-5-0424 true, glm-5-3-flash true, glm-5-3 true, claude-4-5-haiku true, minimax-m2.7 false (catalog has minimax-m2-7 hyphen).

Coverage: AA does cover underlying families for 3/5 (mimo via dated 0424, minimax-m3, glm-5.3), but catalog slugs use hyphenated, sometimes dated forms, while provider ids use dot versions and contributor/-next suffixes that break exact/normalized equality. Qwen and Muse contributor are true gaps (suffix not in AA).

## 3. Evidence packet and prompt built in llm.py

### 3.1 EvidenceCollector

EvidenceCollector.collect builds EvidencePacket: benchmarks via BenchmarkDataCache.get(model_id) (keyed by _normalize_model_key), classified, plus provider_claims from models_dev description (coding keywords), plus aa_match/pricing from ModelResolution.

All 5 ids had benchmarks: [], provider_claims: [], deterministic_flags: [] because cache.get returned None (free suffix + prefix stripping mismatch) and models_dev lookup on free id misses (models_dev keys are without free, e.g. alibaba/qwen3.8-flash, minimax/MiniMax-M3). Even minimax, which has rich SWE-Bench 80.5/Terminal 66 in models_dev under minimax/MiniMax-M3, produced empty packet because lookup uses model_id_lower minimax-m3-free vs stored key minimax/MiniMax-M3 lowercased, no match.

So packets:

- mimo: aa_match {matched:false}, pricing null, benchmarks []
- minimax: {matched:true, id:277f..., name:MiniMax-M3, score:45.4}, pricing 0.525, benchmarks [] (benchmarks gap despite AA hit)
- muse: false, null, []
- qwen: false, null, []
- glm-5.3: {matched:true, id:cd68..., name:GLM-5.3 (max), score:59.5}, pricing 2.15, benchmarks [] (AA hit but no benchmarks in packet)

### 3.2 Judge request and _build_prompt

Judge.evaluate builds ModelEvaluationRequest from packet + build_benchmark_profile(model_id, provider, cache) (also misses cache due to free suffix, so profile scores {}). pricing from packet. _build_prompt then produces JSON payload:

```
{
  "provider": "nararouter",
  "model_id": "mimo-v2.5-free",
  "artificial_analysis": {"matched":false,"model_id":null,"score":null},
  "benchmarks": {"benchmark_coverage":0.0,"scores":{},"raw_benchmarks":[]},
  "pricing": null,
  "evidence": {"benchmarks":[],"provider_claims":[],"deterministic_flags":[],"artificial_analysis":{...},"pricing":null,"polarity":{}},
  "minimum_aa_intelligence_index":24
}
```

For AA-hit cases (minimax, glm-5.3) same structure but artificial_analysis is {matched:true, score:45.4/59.5} and pricing populated, while benchmarks.scores stays {}. So LLM sees contradictory signals: AA says strong, but benchmarks empty and evidence empty. SYSTEM_PROMPT (llm.py:9-90) governs:

- coding must be true to be eligible for keep
- if no AA candidate matches, may identify parent/base only when clearly documented (mimo/muse/qwen have no AA, so must drop unless web search finds coding claim with URL)
- without verified AA score, keep allowed only when strong model-specific coding evidence with URL
- triangulation: for no-AA / claim-only models, verify via web search (max 2 searches: "{model_id} model card coding..." then "{model_id} coding benchmark...")
- final JSON must have canonical_name, coding(bool), aa_relevance, confidence, decision(keep|drop), evidence_level, evidence[<=2] under 200 tokens

When web search disabled or Brave key missing, make_searcher returns empty, so 3 no-AA payloads have zero supporting evidence; LLM correctly drops with weak and messages seen in yaml ("No AA match...", "none->weak"). Minimax has AA 45.4 but no benchmarks -> still drops per PolicyGate (Model assessed as non-coding (LLM + deterministic); forced drop) — AA alone without coding benchmarks insufficient per prompt.

### 3.3 Why 2 show invalid JSON error

llm.py:LocalLLMEvaluator.evaluate loop (max_searches 2, max_iterations 6): posts to agnes-2.0-flash via JudgeTransport; on tool_calls absent, calls extract_and_validate(raw_content) (json_repair.py: tries fenced JSON, JSONDecoder.raw_decode, repair_json for unescaped quotes/newlines/trailing commas, single-quote fallback; finally validates against ModelEvaluation pydantic). On ValueError appends repair nudge "Return ONLY a JSON object with exactly these keys: ..." and sets disable_next=True; next iteration retries. After 6 iterations without valid JSON raises RuntimeError("LLM failed to return a final evaluation: invalid JSON after retries"), caught in pipeline.py:evaluate_model -> PolicyGate.error_record -> yaml error bucket.

For glm-5.3-free (AA 59.5, benchmarks {}) and muse-spark-1.3-contributor-free (no AA, benchmarks {}), LLM exhausted retries and errored while other 4 with same empty evidence succeeded. Likely triggers:

- agnes-2.0-flash sometimes wraps JSON in prose/markdown or emits explanation before JSON when evidence is contradictory (AA 59.5 but no benchmarks, pricing 2.15 for glm-5.3 may cause verbose reasoning exceeding 200-token limit or unescaped quotes in evidence strings). json_repair handles fences and trailing commas but not long prose + multiple JSON objects.
- Muse 1.3 vs 1.2: both no-AA but 1.2 succeeded with drop JSON and 1.3 errored — suggests web search for 1.3 returned no results or LLM timeout, then repair prompts accumulate and LLM returns empty or single-quoted JSON that extract_and_validate fails to repair. No persisted LLM raw logs in repo to confirm; needs server-side judge logs or re-run with --log-level debug.

No change to closing issue — diagnosis only.

## Cross-cutting root causes

1. Free suffix not stripped uniformly: normalize_model_id strips -free, but alias_map equality and _normalize_model_key do not. So mimo alias misses, and all benchmark cache lookups miss (empty profiles). Fix levers: strip -free/:free/_free//free before alias_map check and at top of _normalize_model_key (same regex as matcher), or canonicalize via _normalize_model_key(normalize_model_id(id)).

2. Dot vs hyphen version drift: mimo-v2.5 vs mimo-v2-5, glm-5.3 vs glm-5-3 handled via variant 0.95, but mimo needs extra -0424 date suffix; not covered. Muse contributor and Qwen -next similarly need suffix-strip alias (contributor, _next, free) or explicit alias entries.

3. Catalog suffix policy inconsistency: GLM keeps 4.5 with dot, others hyphen; Qwen -next vs plain; Mimo -0424 dated. Variant generator only swaps dot<->hyphen, not suffixes.

4. Benchmark cache population requires benchmarks: collect_from_local only inserts models with benchmarks present; thus alibaba/qwen3.8-flash (if benchmarks missing) never enters cache, so even after fixing normalization it would still be empty. Models.dev entries for those aliases have no benchmarks, so empty evidence is accurate — evaluator must rely on web search + AA only.

5. LLM hedging on high AA without coding evidence: Minimax (45.4) and GLM-5.3 (59.5) have AA above threshold but zero coding benchmarks in packet. Prompt says coding must be true and benchmarks support AA, but LLM+PolicyGate still drops minimax while glm-5.3 errors instead of dropping — non-deterministic formatting under contradictory evidence; robust fix is deterministic fallback when LLM invalid JSON (already error bucket) or hybrid evidence_level promotion as in issue #35 dirty diff, not LLM-only.

## Recommendations (read-only)

- Normalize before alias: provider_slug_stripped = re.sub(r"[:/_-]free$","",provider_slug,flags=re.I).lower() and compare stripped form to alias_map keys; same for base_slug. Already done in matcher normalized loop, but alias_map early return should use stripped form.
- Unify -free stripping in benchmarks.py:_normalize_model_key (add re.sub(r"[:/_-]free$","",name)) and consider reusing normalize_model_id canonical form for cache key construction. Without this, all free aliases lose benchmark hits permanently (observed: minimax SWE 80.5 in models_dev never surfaced).
- Add alias entries for the 5: mimo-v2.5->mimo-v2-5-0424 after free strip (existing but unreachable), muse-spark-1.2-contributor->muse-spark-1-2 (and 1.3), qwen3.8-flash->qwen3-8-flash-next (or suffix-strip -next in variant loop), glm-5.3->glm-5-3 already works via variant but consider prioritizing flash vs base: stripped -free alone vs -flash-free correctly routes, keep.
- For invalid JSON, log raw LLM content to data/results/*.log for post-mortem; current yaml only stores "invalid JSON after retries". Re-running nararouter with valid Brave key and web search enabled may reduce hedging for no-AA cases.

## Verification steps (for follow-up fix PR)

- Reproduce locally: .venv/bin/python -c "from llm_discovery.model_matching import normalize_model_id,ModelMatcher; ..." for each 5 ids, assert match slug and method as above; then BenchmarkDataCache.get after collect — expect None pre-fix, hit post-fix for minimax/glm after free-strip fix.
- Check YAML: yq '.drop_llm[]|select(.model_id|contains("free"))|.model_id' data/results/nararouter.yaml vs error bucket; after fix expect mimo and qwen to gain AA hits and move from unresolved to alias/variant, minimax to keep if coding evidence surfaced via models_dev (SWE 80.5), glm-5.3 to keep.
- Re-run discover_provider for nararouter with fixed matcher/cache and capture LLM raw responses to confirm invalid JSON disappears.

## Files inspected

- src/llm_discovery/model_matching.py:21-48,54-90,383-680 (normalize, variants, ModelMatcher.match)
- src/llm_discovery/catalogs.py:1-40 (ArtificialAnalysisCatalog)
- data/artificial_analysis_models.json (631 slugs)
- src/llm_discovery/benchmarks.py:365-410,225-295 (_normalize_model_key, cache get, collect_from_local)
- src/llm_discovery/evidence_collector.py:54-140 (packet build)
- src/llm_discovery/evidence_packet.py:41-95 (packet summary)
- src/llm_discovery/llm.py:9-90,138-240 (SYSTEM_PROMPT, _build_prompt, evaluate loop, tool disable)
- src/llm_discovery/json_repair.py:81-160 (extract_and_validate)
- src/llm_discovery/judge.py:1-45, src/llm_discovery/pipeline.py:50-120,447-540 (request building, error_record)
- data/results/nararouter.yaml (5 free aliases + controls)
