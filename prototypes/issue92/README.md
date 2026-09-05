# Prototype #92 — In-pipeline cache lookup (strong-only, pricing TTL 14d)

## Seam decision

**Chosen: `pipeline.evaluate_model` early return** after `resolve_model`, before `EvidenceCollector`/ `Judge`.

- Single call site: `normalize_store_key(raw_id)` -> `store.get` -> `classify_hit`.
- Has benchmark context for gap-fill (fresh profile from BenchmarkDataCache without LLM).
- Testable in isolation (mock store, no ThreadPool lock).
- Keeps `discover_provider` /`build_all` unchanged except injecting store arg.

**Rejected:**

- `discover_provider` wrapper — too coarse, needs per-model benchmark context, duplicates stale/pricing logic, harder to unit-test hit vs miss per model.
- `build_all` loop — too late (after per-provider isolated discovery), forces cross-provider lock for merge and repeats backfill logic.

## Data flow (per #91 decisions)

```
raw provider model_id (exact case/prefix/free)
  -> normalize_store_key -> cache_key (lowercased, prefix/free stripped)
  -> store.get(cache_key)

if strong_hit (evidence_level==strong):
  pricing = stale(>14d) ? re-avg from catalog observations (aggregate_pricing) : verbatim copy
  benchmarks = gap-fill null->fill only from fresh BenchmarkDataCache profile (immutable, no delta rebuild)
  output = keep record with provider_model_id=raw_id (for yaml + Bifrost POST {model: raw_id})
           cache_key provenance, cached=true, reason=cache_hit:strong
  skip EvidenceCollector + Judge entirely
else (miss = none/moderate/weak/not found):
  full pipeline: EvidenceCollector -> Judge -> PolicyGate
  on keep+strong write back via ModelInfoRecord.from_provider_record + store.put
```

## Fallback when catalog delta forces rebuild

Per modified ADR 0007 (Identity -> new-key Churn -> Pricing TTL only, Evidence Delta disabled):

- **New normalized key** (model appears in provider but not in store) -> miss -> full LLM.
- **Identity fail** (UUID / hallucinated evidence) -> gate blocks, never cached (should_cache false).
- **Pricing change alone** -> never forces LLM; stale TTL just re-avg pricing verbatim.
- **Benchmark / AA score drift** -> ignored (immutable). Only null->fill via gap-fill; no rebuild.

## Concurrency / atomic write note

Cross-provider copy happens per-model inside evaluate_model (ThreadPool workers already per-provider). Store writes go via ModelInfoStore atomic pretty write (temp + rename) plus .bak; parallel discover_fn path (build_all max_workers) writes yaml per-provider then single-threaded backfill merge — no concurrent store put during discovery. If future parallel store.put needed, guard with file lock (see #93 Out-of-scope fog: atomic write).

## What this prototype skips

- Store schema slimming (benchmarks+pricing only, no evidence/_meta/source_providers bloat) — decided in #93.
- GC of stale keys when provider drops model (now moot if aliases only in yaml — confirm in #93).
- Real catalog pricing observation plumbing (fresh_pricing_obs arg stubbed; build_all will supply from AA/models_dev caches).

## Run

```bash
python -c "import prototypes.issue92.cache_seam_stub; print('stub import ok')"
# or wire into pipeline.evaluate_model for manual trace:
# patch pipeline.evaluate_model to evaluate_model_with_cache with a temp ModelInfoStore(tmp_path)
```

## Files

- `cache_seam_stub.py` — stub with classify_hit, pricing TTL, gap-fill, evaluate_model_with_cache
- This README

Human reaction needed: confirm seam placement + pricing re-avg plumbing before wiring into real pipeline.py.
