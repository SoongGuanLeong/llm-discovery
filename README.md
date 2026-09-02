# llm-discovery

Discover and evaluate cloud LLMs.

## Catalog refresh (T6)

One-command refresh of all JSON snapshots (`data/artificial_analysis_models.json`, `data/models_dev_catalog.json`, `data/benchmarks.json`) with atomic write + `.bak` backup:

```bash
# all three (requires AA_API_KEY for Artificial Analysis)
infisical run -- .venv/bin/python scripts/refresh_catalogs.py
# or
export AA_API_KEY=aa_xxx  # or ARTIFICIAL_ANALYSIS_API_KEY
.venv/bin/python scripts/refresh_catalogs.py

# alternatives (same logic):
.venv/bin/python -m llm_discovery.refresh
.venv/bin/python -m llm_discovery.cli refresh

# dry-run, or subset
.venv/bin/python scripts/refresh_catalogs.py --dry-run
.venv/bin/python scripts/refresh_catalogs.py --only models_dev benchmarks
```

- AA source: `https://artificialanalysis.ai/api/v2/data/llms/models` (header `x-api-key: $AA_API_KEY`)
- models.dev source: `https://models.dev/catalog.json` (public)
- benchmarks: rebuilt locally via `BenchmarkDataCache.collect_from_local()` (no network)
- Backups: `data/*.json.bak` (prior snapshot copied before atomic rename)
- Atomic: temp file + `fsync` + `replace` in same directory

## Query catalogs

```bash
.venv/bin/python -m llm_discovery.cli aa search "llama"
.venv/bin/python -m llm_discovery.cli aa filter --min-score 50
.venv/bin/python -m llm_discovery.cli models show groq
```
