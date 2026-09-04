# Prototype #84 — Cloudflare model identity fix (UUID → name)

## Decision
Canonical `model_id` = Cloudflare `name` (`@cf/...`) when present and not UUID-shaped.
UUID preserved as `source_id` + `cloudflare_id` for audit, never as primary key.

## Why
- `discovery._normalize_models` used `id` (UUID) → 65 records unactionable, 0 keeps, 0 benchmark/AA joins.
- `model_info_store.normalize_store_key(UUID)` returns UUID → no dedup, breaks per-record TTL reuse.
- Evidence collector saw UUID → empty packet → all weak drops.

## Change
- `src/llm_discovery/discovery.py`:
  - added `_is_uuid`, `_UUID_RE`, `_UUID_HEX32_RE`
  - added `_normalize_cloudflare_models(models)` — Cloudflare-specific, prefers `name` human, keeps UUID as auxiliary
  - `discover_cloudflare_models` now calls `_normalize_cloudflare_models` not generic `_normalize_models`
- Generic `_normalize_models` unchanged (other providers).
- `normalize_store_key` already handles `@cf/` prefix via `rsplit("/",1)[-1]` → `qwen2.5-coder-32b-instruct` etc. No store change needed.

## Scope
- `name` missing or UUID-shaped → fallback to `id` (still UUID, will fail Accurate-Enough Gate per ADR 0006 — UUID denylist). Logs no crash.
- `description`/`task` preserved for evidence collector without extra fetch.
- `source_id`/`cloudflare_id` kept for backfill audit; not used for store key.

## Before / After (from prototypes/issue84/cloudflare_search_sample.json)

| UUID | Before `model_id` (bug) | After `model_id` (fix) | `source_id` | store_key |
|---|---|---|---|---|
| 01564c52-8717-47dc-8efd-907a2ca18301 | `01564c52-...` | `@cf/deepgram/aura-1` | uuid | `aura-1` |
| f47ac10b-58cc-4372-a567-0e02b2c3d479 | uuid | `@cf/qwen/qwen2.5-coder-32b-instruct` | uuid | `qwen2.5-coder-32b-instruct` |
| 6ba7b810-9dad-11d1-80b4-00c04fd430c8 | uuid | `@cf/deepseek-ai/deepseek-v4-pro-0813` | uuid | `deepseek-v4-pro-0813` |

Full diff: `before_after.json` and `before_after_yaml_snippet.md`.

## Migration
Existing `data/results/cloudflare.yaml` holds 65 UUID-keyed records (51 drop + 14 error). No store UUID keys (store was Keeper-only, cloudflare 0 keeps). Migration:
- YAML: next `discover_provider(cloudflare)` overwrites file with human keys. No manual rewrite needed; old file replaced on next build.
- Store: purge if any UUID keys exist. Script `migration.py` does `is_uuid(key) → delete` and reports count. Safe to run even when 0 matches (idempotent).
- Backfill: after fix, re-run `python -m llm_discovery.backfill` — human keys now join `BenchmarkDataCache` and AA via `normalize_store_key` / alias map.

See `migration.py` for runnable purge.

## Validation
```
PYTHONPATH=src:.venv/lib/python3.12/site-packages:$PYTHONPATH pytest -q -k cloudflare
PYTHONPATH=src:.venv/lib/python3.12/site-packages:$PYTHONPATH python prototypes/issue84/migration.py --dry-run
```
- Old path: all `_is_uuid(model_id)=True` → gate `is_accurate_enough` fails (UUID denylist).
- New path: `_is_uuid(model_id)=False`, `is_accurate_enough` can pass if strong evidence + pricing + coverage ≥0.25.

## Risks
- Cloudflare may return models where `name` is absent — fallback UUID still lands in YAML but fails gate (visible, not silent).
- Store key collision: `@cf/meta/llama-3.2-...` vs `meta/llama-3.2-...` from other provider collapses to same key — intended (same logical model dedup via store merge per #64).
