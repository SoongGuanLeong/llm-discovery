# Prototype #86 — Intelligent build_all per-model reuse and rebuild seam

## Question
How does `build_all` reuse fresh Keepers without LLM call and rebuild only stale/changed/new ids at per-model granularity?

Requires #84 (Cloudflare UUID→name) and #85 (ADR 0007 ranked invalidation signals).

## Seam
`prototypes/issue86/intelligent_build.py` exposes `intelligent_build(discovered, fresh_catalog_map, store_path, build_fn)`.

Enumerate discovered ids, diff against store keyed by `normalize_store_key`, decide per ranked signals, reuse vs rebuild, merge atomic via `ModelInfoStore.put`.

```
store = ModelInfoStore(path)
discovered = {normalize_store_key(m["id"]): m for m in discover_fn(provider)}
for key, meta in discovered.items():
    rec = store.get_by_key(key)
    fresh = fresh_catalog_lookup(key)  # cheap local JSON (AA + models_dev + benchmarks)
    if is_identity_bad(meta["id"]): build(key)          # Rank1 TTL0
    elif rec is None: build(key)                         # Rank2 new
    elif evidence_delta(rec, fresh): build(key)          # Rank4 AA>=2, coding null->non-null, new KEY_SIGNAL, score>=10%, coverage cross 0.25
    elif pricing_delta(rec.pricing, fresh.pricing): build(key)  # Rank4 blended >=0.05 or >=10%
    elif store.get_if_fresh(key,14) is None: build(key)  # Rank5 TTL
    else: reuse(rec)  # skip LLM judge + evaluator packet, gap-fill benchmarks/pricing
# Rank3: stored keys for provider not in discovered -> retain 14d then GC if not shared
```

## Reuse path details
- **Evaluator cache (deterministic packet) skipped**: packet build is only for `build()` path. Reuse uses `fresh_catalog_map` (already fetched cheap) for delta check, no packet recompute.
- **LLM judge skipped**: `build_fn` (simulates LLM judge + packet->record) called only on rebuild. `reused` count = LLM calls avoided.
- **Pricing/benchmark gap-fill on reuse**: `\_gap_fill_benchmarks` does `union_max` (keep max per signal) and merges pricing via `aggregate_pricing` without resetting `last_updated` (TTL window preserved). Only rebuild bumps `last_updated`.

## Mocked discovery demo (no network)
`demo.py` seeds a temp store with 4 Keepers (fresh, stale 20d, pricing-delta, gap) then runs 3 scenarios:

- **S1 mixed**: 5 discovered (fresh reuse 1, stale TTL 1, pricing delta 1, new KEY_SIGNAL 1, new id 1) → reused 1 (20%), rebuilt 4, LLM calls 4, store grows 4→5.
- **S2 second build same ids no delta**: discovered 3, all fresh now → reused 3 (100%), rebuilt 0, LLM calls 0, store stays 5. Shows packet+LLM skipped.
- **S3 UUID identity bad**: discovered includes `01564c52-8717-47dc-8efd-907a2ca18301` → identity_bad forces rebuild (TTL0) even though store empty; `fresh-model` still reuses. GC candidate counted for removed stale keys >14d.

Run:
```
PYTHONPATH=src:. python prototypes/issue86/demo.py
```

Stats written to `before_after.json` (discovered/reused/rebuilt/store_size/reuse_pct/reasons).

## Integration note
Current `src/llm_discovery/build_all.py` + `backfill.py` still do file-level 14d skip (`stale_skipped`) and overwrite YAMLs. Seam above is thin prototype; production lift is:
- remove `backfill` file-level `is_stale(evaluated_at,14)` (already planned per ADR 0006)
- add `is_identity_bad`, `pricing_delta_exceeds`, `evidence_delta_exceeds` to `model_info_store.py` (ADR 0007 Consequences)
- replace `build_all` discover→write→backfill with `intelligent_build` loop (enumerate, decide, build_fn only when needed, `ProviderBatchWriter.write` per rebuilt only)
- keep `backfill` for one-shot migration, no TTL gate.

## Validation
```
python -m py_compile prototypes/issue86/intelligent_build.py
PYTHONPATH=src:. python prototypes/issue86/demo.py  # expect S1 20% reuse, S2 100% reuse, S3 identity_bad
pytest -q -k "test_build_all"  # existing integration still green (no production change yet)
```

## Risks
- Fresh catalog fetch must be cheap (local JSON) — delta checks assume AA/models_dev cached local, not network per build.
- Pricing averaging (`aggregate_pricing`) on reused gap-fill vs rebuilt: reuse keeps original `last_updated` so TTL not reset on gap-fill (intentional per ADR 0007).
