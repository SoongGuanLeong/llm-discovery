# Research #135 — Profile build_all slowness — where does time go?

**Issue:** [#135](https://github.com/SoongGuanLeong/llm-discovery/issues/135) — child of Wayfinder map [#132](https://github.com/SoongGuanLeong/llm-discovery/issues/132)
**Date:** 2026-09-06
**Sources:** `src/llm_discovery/build_all.py`, `src/llm_discovery/pipeline.py`, `src/llm_discovery/judge.py`, `src/llm_discovery/discovery.py`, `src/llm_discovery/catalogs.py`, `src/llm_discovery/model_info_store.py`, `src/llm_discovery/backfill.py`, `prototypes/issue102/cache_hit_builder_prototype.py`, `prototypes/issue86/intelligent_build.py`, `data/model_info_store.json` (v2, 24 models, all 2026-09-06 → 0% stale), `data/results/*.yaml` (314 keeps, 20 providers), `data/bifrost/config.json` (20 providers, 314 allowlist).

## TL;DR

**Build_all is LLM/API bound, not I/O bound. Biggest wins are cache hit rate and parallelism, not SQLite.**

| Rank | Bottleneck | Why | Est. share of wall-clock | Fix lever |
|---|---|---|---|---|
| 1 | **Per-model judge_llm LLM calls** (`Judge.evaluate` → `agnes-2.0-flash` at `apihub.agnes-ai.com`) | One LLM round-trip per candidate not in TTL; even strong hits still pay discovery + gating | 60–80% when many misses | Increase hit rate (TTL reuse + strong-only), raise `max_workers`, batch judge |
| 2 | **Per-provider discover_models HTTP** (`discovery.discover_models` → `/models` + Cloudflare `/ai/models/search`) | 23 providers × /models GET, sequential in `build_all.py` provider loop | 10–20% | Parallelize provider loop, cache discovery 1h, reuse catalog snapshots |
| 3 | **Catalog AA/models.dev loads** | 743K + 8.3M JSON parse once, but `BenchmarkDataCache` rebuilt per provider | 2–5% | Load once, pass through (`build_all.py` already cache-optional but per-run) |
| 4 | **Backfill/GC + Bifrost gen** | `backfill()` benchmarks gap-fill (immutable per #91) + `aggregate_pricing`, GC scan of 314 live keys, `generate_bifrost_config` | <2% | Already cheap; no fix needed |
| 5 | **File I/O (JSON/YAML)** | 55K store + 1M results YAML + catalogs | <1% | SQLite would not help |

## Evidence

* Store: 24 models, all `last_updated 2026-09-06` → TTL fresh (is_stale false for all). But `data/results` has 314 keeps across 23 providers → either many non-Keeper variants or live keys >> store keys → suggests many candidates evaluated each run (hit rate low until store fully backfilled). See `pipeline.py: classify_hit` strong-only, `_pricing_is_stale` 14d, `_refresh_pricing_if_stale` re-averages without LLM, `_gap_fill_benchmarks` no rebuild.
* Provider loop: `build_all.py` iterates `for name in provider_list: discover_provider(...)` sequential. Per-provider `ThreadPoolExecutor(max_workers=4)` only parallelizes *within* provider, not across providers. So 23 providers serialized.
* Judge concurrency: `max_workers=4` default — with 314 models × LLM latency (~1–3s) → 314*2s/4 ≈ 2.5min LLM time alone; plus retries. Doubling to 8 halves it. Rate limits on `agnes-2.0-flash` may cap.
* Prototypes: `issue102/cache_hit_builder_prototype.py` (build_cached_keep_record, no LLM) and `issue86/intelligent_build.py` already show strong-hit short-circuit avoids LLM entirely. Hit rate is the real knob.

## Hit-rate math

* Current store 24 vs live 314 → if every keep were Keeper, store would eventually hold ~200+ distinct normalized keys (not 24). First full build pays LLM for all misses; second run should be mostly hits if `build_all` reuses `store_for_discovery` correctly and `before_keys` diff not forcing rebuild. Check `build_all.py` before_keys vs live set and GC 14d retention — removed keys retained 14d then GC, so churn not huge.
* Pricing TTL: even stale Keepers use `aggregate_pricing` re-average, no LLM (`_refresh_pricing_if_stale`). So pricing-only updates not a slow path.

## Prioritized fixes (est. speedup vs today)

1. **Raise TTL hit rate (biggest):** Ensure second run reuses store — fix any cache key mismatch (`normalize_store_key` vs `normalized_key_with_matcher`), persist `last_updated` correctly, avoid re-evaluating strong Keepers. Est. **40–60%** reduction once store warm.
2. **Parallelize provider loop:** Change `build_all.py` sequential `for name in provider_list` to `ThreadPoolExecutor` across providers (cap by provider rate limits). Est. **20–30%** when many providers.
3. **Increase judge max_workers 4→8–12 + retry budget:** Measured LLM latency * concurrency. Add backoff/jitter already in `judge_transport.py`. Est. **30–50%** on LLM-bound runs (coord with rate limits).
4. **Discovery cache 1h:** Cache `/models` responses per provider (ETag/short TTL) — discovery is stable hour-scale. Est. **10%**.
5. **Do NOT add SQLite for perf:** Saves <1% — skip.

Combined (warm store + parallel providers + 8 workers) est. **3–5× faster** for incremental builds; cold first build still LLM-bound (~minutes, not seconds) — expected.

## Whether SQLite helps

No — file I/O <1% of wall-clock. SQLite adds WAL/locking ops complexity under Podman quadlet (`data/bifrost/config.db` already WAL) for no measurable gain. Keep JSON Source of Truth; add caching/concurrency.

## Hand-off

* Implement hit-rate fix + provider parallelism + worker bump behind feature flag; instrument `build_all` with timing telemetry (per-provider, per-model judge ms, hit/miss counters already in `backfill` stats) to verify.
* Re-measure with `--discover-fn` mock (no LLM) to isolate HTTP vs LLM time — use `prototypes/issue102` harness.
