# LLM Discovery

Single context for discovering, evaluating, and caching LLM models suitable for coding tasks.

## Language

### Discovery & Evaluation

**Keeper**:
A model record that passed the Accurate-Enough Gate and is eligible for caching in the store and for 14-day TTL reuse.
_Avoid_: keep, approved model

**Candidate**:
A model record that has not passed the Accurate-Enough Gate (moderate, weak, or failing floors) and must be re-evaluated on every build.
_Avoid_: non-keeper, suspect keep

**Evidence Level**:
LLM-judge assessment of supporting evidence strength: strong, moderate, or weak. Promoted deterministically by AA score, coding_score, and benchmark coverage but never demoted from strong.
_Avoid_: confidence, trust level

**Benchmark Coverage**:
Share of KEY_SIGNALS (aa_intelligence, swe_bench_verified, livecodebench, humaneval) present in a record. Used as a floor for the Accurate-Enough Gate.
_Avoid_: coverage, bench score

### Source of Truth

**Source of Truth**:
The committed `data/model_info_store.json` slim v2 store (version 2, {benchmarks, pricing, _meta} per normalized key). It is the only durable artifact consulted for TTL reuse; per-model lookups normalize via `normalize_store_key`.
_Avoid_: cache, model cache, database

**Ephemeral Report**:
A per-provider `data/results/<provider>.yaml` file produced by discovery. Overwritten on each build and gitignored; its keep records are backfilled into the Source of Truth but the file itself is not retained for audit.
_Avoid_: results file, YAML store, provider report

**Derived Cache**:
The regenerable SQLite file `data/derived/cache.db` rebuilt at build_all tail from the Source of Truth for ad-hoc SQL / DBeaver inspection. WAL-enabled, gitignored, atomic temp->replace. Never consulted for TTL reuse or correctness; delete and rebuild from the store at any time.
_Avoid_: cache, model cache, store DB

**Accurate-Enough Gate**:
Predicate that decides whether a keep record may become a Keeper. Requires: evidence_level == strong, coding_score != null, pricing present or free-marker exception, aa_model_id present or qualifying supplement (SWE/Terminal >=50) with URL, benchmark_coverage >=0.25, at least one http URL in evidence, and no hallucinated or UUID model_id.
_Avoid_: eligibility gate, cache gate

**Record TTL**:
Per-record freshness window tracked by `StoreMeta.last_updated`. A Keeper with age <= 14 days may be reused without re-evaluation; older records are stale and must be rebuilt.
_Avoid_: file TTL, cache expiry, 14-day skip

**Hallucinated Evidence**:
Evidence strings that cite non-existent benchmarks or unverified domains (e.g. tokenmix.ai, callsphere.ai, benchlm) without a first-party URL. Such records fail the Accurate-Enough Gate.
_Avoid_: fake evidence, weak claim


**Invalidation Signal**:
Ranked condition that forces a Keeper to be rebuilt even before Record TTL expiry. Order: Identity Integrity → Model-List Churn (new) → Pricing TTL (14d re-average) → Time TTL. Evidence / Benchmark Delta disabled per Wayfinder 91 (benchmarks immutable gap-fill). Checked in build_all before reuse.
_Avoid_: stale reason, expiry trigger

**Model-List Churn**:
Difference between the current discovered normalized keys and the keys stored for a provider. New keys are always built; removed keys are retained 14 days then GC if not rediscovered and not shared by another provider.
_Avoid_: provider diff, list drift

**Evidence Delta**:
Disabled in slim v2 per Wayfinder 91. Benchmarks immutable gap-fill only; no Evidence Delta rebuild. (Legacy definition: AA ≥2.0, new KEY_SIGNAL, score ≥10%, or benchmark_coverage crossing 0.25.)
_Avoid_: score change, benchmark drift

**Pricing Delta**:
Pricing refresh via re-average from catalog when Record TTL >14d (no LLM). Blended pricing outlier handling via `aggregate_pricing`. Free-marker exception still satisfies pricing presence floor. Not a delta-triggered rebuild in slim v2 — just re-average.
_Avoid_: price change, cost drift

**UUID Model Id**:
A provider model_id that is a UUID (8-4-4-4-12 hex) rather than a human name. Never cacheable; blocked by the gate until a human-name mapping exists.
_Avoid_: infra id, opaque id
### Model Groups (Bifrost)

**Model Group**:

User-facing Bifrost virtual-model pool mapped to the internal `TIER_*` pool (`flash` 46, `max` 84, `contributor_free` 2 via `group_keeps_by_tier` keep-all, strict `contributor` substring). Served via shim sidecar `:8081` alias rewrite to uniform weighted pick within strict tier; empty pool returns `503 tier_unavailable` with `Retry-After: 60`, no cross-group fallback. Concrete provider models remain pinnable via `:8080`.
_Avoid_: tier group, model pool, virtual model (use Model Group for user-facing)

**Bifrost Gateway**:

File-only gateway on `:8080` (`data/bifrost/config.json` + `config.db` + `env.VAR` secrets from `~/.config/bifrost/bifrost.env` `0600`) that proxies to provider `base_url` with `keys.models` allowlist. `GET /api/models` lists 132 models across 19 emitted providers; `GET /v1/models` is health-filtered by key validity.
_Avoid_: gateway, proxy, router

**Shim Sidecar**:

Process on `:8081` (`src/llm_discovery/bifrost/sidecar.py` via `scripts/run_sidecar.sh`) that rewrites `model: flash|max|contributor_free` to a pool member via `pick_model_for_tier` and proxies to Bifrost `:8080`; exposes `GET /health` tier counts and `GET /v1/models` + `/api/models` augmented with alias virtual models for discoverability.
_Avoid_: shim, alias router

**Tier**:

Internal categorization token `flash`/`max`/`contributor_free` assigned by `categorize.py`. Groups derive purely from this pre-categorized field via `group_keeps_by_tier`; never recomputed at generation time. Legacy `contributor_special` normalizes to `contributor_free`; non-tier keeps (`drop`, `error`, `uncertain`) excluded.
_Avoid_: category, group type
