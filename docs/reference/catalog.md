# Catalogs

Reference for the catalog snapshots under `data/`: their sources, refresh
mechanics, scheduled refresh, and offline queries. The keyless first-look
sequence (`refresh --only models_dev` plus `catalog models …` /
`catalog providers …`) is the README's Catalog Path.

## Catalog data sources

Discovery resolves every provider model against two offline snapshots in `data/` (gitignored, refreshed via `llm-discovery refresh`):

| Source | Snapshot | Origin | Refresh |
|---|---|---|---|
| **models.dev** | `data/models_dev_catalog.json` | `https://models.dev/catalog.json` (public, no key) | `llm-discovery refresh` |
| **Artificial Analysis** | `data/artificial_analysis_models.json` | `https://artificialanalysis.ai/api/v2/data/llms/models` (header `x-api-key: $AA_API_KEY`) | same; requires `AA_API_KEY` |
| **Benchmarks** | `data/benchmarks.json` | Rebuilt locally via `BenchmarkDataCache.collect_from_local(aa, models_dev)` - no network | same |

Queries against these snapshots: see [Query catalogs](#query-catalogs).

## Catalog refresh (T6)

One-command refresh of all JSON snapshots (`data/artificial_analysis_models.json`, `data/models_dev_catalog.json`, `data/benchmarks.json`) with atomic write + `.bak` backup:

```bash
# all three (requires AA_API_KEY for Artificial Analysis)
infisical run -- llm-discovery refresh
# or
export AA_API_KEY=aa_xxx  # or ARTIFICIAL_ANALYSIS_API_KEY
llm-discovery refresh
# equivalent module form (same single surface):
python -m llm_discovery refresh

# dry-run, or subset
llm-discovery refresh --dry-run
llm-discovery refresh --only models_dev benchmarks
```

`AA_API_KEY` from the environment is the only path for the Artificial Analysis
snapshot; there is no key-taking flag. The models.dev source alone
(`llm-discovery refresh --only models_dev`) is public and needs no key.

- AA source: `https://artificialanalysis.ai/api/v2/data/llms/models` (header `x-api-key: $AA_API_KEY`)
- models.dev source: `https://models.dev/catalog.json` (public)
- benchmarks: rebuilt locally via `BenchmarkDataCache.collect_from_local()` (no network)
- Backups: `data/*.json.bak` (prior snapshot copied before atomic rename)
- Atomic: temp file + `fsync` + `replace` in same directory

### Staleness gate in `build-all`

`build-all` checks catalog `fetched_at` before pricing re-average: if either catalog is older than 28 days it refreshes first (warn-only - a failed refresh never fails the build). Tune with `--catalog-max-age-days N` (0 disables) or `--no-catalog-refresh`.

Scheduling a periodic `llm-discovery refresh` is left to you - a cron entry or your own systemd unit. The repo ships no unit files.

## Query catalogs

```bash
llm-discovery catalog aa search "llama"
llm-discovery catalog aa filter --min-score 50
llm-discovery catalog models show <model-id>
llm-discovery catalog providers models <provider-id>
```

Queries read the local snapshots; they never touch the network. Before any
snapshot exists (fresh clone, `data/` gitignored), a query exits `3` with
`catalog not found: …` and the hint `run llm-discovery refresh`. The
`catalog aa` queries need the Artificial Analysis snapshot, which needs
`AA_API_KEY` to refresh — they are not part of the keyless Catalog Path.
