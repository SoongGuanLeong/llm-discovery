# llm-discovery

Discover and evaluate cloud LLMs across multiple providers. Enumerates provider `/models` endpoints, resolves each model against offline catalogs, judges coding relevance via LLM, and writes a curated keep-list per provider.

## Prerequisites

- **Python 3.12** (`requires-python = ">=3.12"` in `pyproject.toml`)
- **uv or venv** — `uv venv && uv pip install -e .` or `python -m venv .venv && .venv/bin/pip install -e .`
- **Provider configuration** — `config/providers.yaml` is the source of truth. Each entry declares `name`, `base_url`, and `secret` (env var name holding the API key):

  ```yaml
  providers:
    - name: groq
      base_url: https://api.groq.com/openai/v1
      secret: GROQ_API_KEY
    - name: kilo_ai
      base_url: https://api.kilo.ai/api/gateway
      secret: KILO_AI_API_KEY
  ```

  Add a provider there; no code change needed. `judge_llm` and `artificial_analysis` sections define the judge model and AA score thresholds.

- **Secrets** — provide API keys either directly via env vars or via Infisical (recommended for teams):

  | Method | How |
  |---|---|
  | **Plain env** | `export GROQ_API_KEY=... AGNES_AI_API_KEY=... AA_API_KEY=...` |
  | **Infisical** | Store keys in two projects and export via `infisical export`. Set local `.env` (gitignored, see `.env.example`): |

  ```bash
  LLM_SHARED_PROJECT_ID=<project with all provider keys + AGNES_AI_API_KEY>
  LLM_DISCOVERY_PROJECT_ID=<project with AA_API_KEY only>
  ```

  `LLM_SHARED_PROJECT_ID` holds every provider key (`GROQ_API_KEY`, `KILO_AI_API_KEY`, `CEREBRAS_API_KEY`, `OPENCODE_ZEN_API_KEY`, ...) plus the judge key `AGNES_AI_API_KEY`. `LLM_DISCOVERY_PROJECT_ID` holds only `AA_API_KEY` (used for catalog refresh). If you do not use Infisical, just export the keys and ignore these two vars — `src/llm_discovery/secrets.py:load_all_secrets()` only runs when the vars are set.

No Podman, systemd, or gateway setup is required to run discovery locally.

> **.infisical.json removed** — the repo previously shipped a placeholder `.infisical.json` with a stale `workspaceId`. It was not used (secrets are loaded via explicit `infisical export --projectId $LLM_*_PROJECT_ID`). The file has been deleted; the two `LLM_*_PROJECT_ID` env vars are now the only Infisical config.

Optional env vars:

- `BRAVE_API_KEY` — Brave Search API (higher quality web search; without it, DuckDuckGo is used). Set `DISABLE_WEB_SEARCH=1` for offline mode.
- `AA_API_KEY` / `ARTIFICIAL_ANALYSIS_API_KEY` — only needed for `refresh` (see below).

## Installation

```bash
# 1. Clone and create env
git clone <repo> && cd llm-discovery
uv venv --python 3.12          # or: python3.12 -m venv .venv
source .venv/bin/activate
uv pip install -e .            # or: .venv/bin/pip install -e .

# 2a. Plain env (simplest)
export AGNES_AI_API_KEY=...
export GROQ_API_KEY=...
export KILO_AI_API_KEY=...
# AA key only needed for refresh:
export AA_API_KEY=aa_xxx

# 2b. Or via Infisical (team setup)
# Install + auth once: https://infisical.com/docs/cli/overview
infisical login
cp .env.example .env
# edit .env with LLM_SHARED_PROJECT_ID + LLM_DISCOVERY_PROJECT_ID
infisical export --projectId "$LLM_SHARED_PROJECT_ID" --env dev --format json | jq length
infisical export --projectId "$LLM_DISCOVERY_PROJECT_ID" --env dev --format json | jq length
# run with injected env:
infisical run -- .venv/bin/python scripts/discover.py groq --all
```

No provider or judge keys are hardcoded. See `config/providers.yaml` for the full provider list and secret names.

## Catalog data sources

Discovery resolves every provider model against two offline snapshots in `data/` (gitignored, refreshed via `refresh_catalogs.py`):

| Source | Snapshot | Origin | Refresh |
|---|---|---|---|
| **models.dev** | `data/models_dev_catalog.json` | `https://models.dev/catalog.json` (public, no key) | `scripts/refresh_catalogs.py` / `python -m llm_discovery.refresh` |
| **Artificial Analysis** | `data/artificial_analysis_models.json` | `https://artificialanalysis.ai/api/v2/data/llms/models` (header `x-api-key: $AA_API_KEY`) | same; requires `AA_API_KEY` |
| **Benchmarks** | `data/benchmarks.json` | Rebuilt locally via `BenchmarkDataCache.collect_from_local(aa, models_dev)` — no network | same |

Filtered catalogs can be queried offline without secrets:

```bash
.venv/bin/python -m llm_discovery.cli aa search "llama"
.venv/bin/python -m llm_discovery.cli aa filter --min-score 50
.venv/bin/python -m llm_discovery.cli models show groq
```

## How to run

All runs load secrets from env (or Infisical if configured) and write results to `data/results/`.

```bash
# Tracer: evaluate ONE model for a provider (deterministic pick, cheapest smoke test)
.venv/bin/python scripts/discover.py groq
.venv/bin/python scripts/discover.py kilo_ai

# Batch: evaluate ALL models for one provider, in parallel (T3)
.venv/bin/python scripts/discover.py groq --all
.venv/bin/python scripts/discover.py kilo_ai --all
.venv/bin/python scripts/discover.py openrouter --all --workers 4   # default 4

# All providers: evaluate every configured provider (each provider in parallel)
.venv/bin/python scripts/discover.py --all-providers
.venv/bin/python scripts/discover.py --all-providers --workers 4

# Help — shows configured providers from config/providers.yaml
.venv/bin/python scripts/discover.py --help
```

### What happens per run

1. `discover_models(base_url, api_key)` (or Cloudflare/BazaarLink special paths) enumerates `/models`.
2. Free-model filter (`_split_by_free_rule`) drops non-free models before any LLM cost.
3. `BenchmarkDataCache` + `ModelResolver` resolve each model against AA/models.dev/benchmarks.
4. `EvidenceCollector` + `Judge` (via `AGNES_AI_API_KEY` / `agnes-2.0-flash`) + `PolicyGate` judge coding relevance and tier (`max` >=45, `flash` 24–45, `drop` below).
5. Failures are isolated — one model error goes to the `error` bucket, other models still complete.

Concurrency: bounded `ThreadPoolExecutor(max_workers=4)` with synchronous `httpx`; results are sorted for determinism.

## Output

### Location

```
data/results/<provider>.yaml    # e.g. data/results/kilo_ai.yaml, data/results/groq.yaml
```

- One file per provider, overwritten idempotently on each run.
- `data/` is gitignored (`.gitignore:21`); snapshots and results stay local. Commit only `.env.example` and `config/providers.yaml`.

### Schema (per file)

```yaml
provider: kilo_ai
evaluated_at: '2026-09-02T12:54:29.590323+00:00'
keep:
  - model_id: minimax/minimax-m2.7:free
    decision: keep
    tier: flash            # max | flash
    category: flash        # mirrors tier (spec wording)
    aa_model_id: 4bbceacb-cf47-464b-b60f-e1d1fe016d67
    aa_score: 38.9
    coding_score: 58.23
    benchmarks: { scores: { swe_bench_verified: { score: 79.9, ... } }, ... }
    confidence: 0.95
    evidence_level: strong
    evidence: ["AA Intelligence Index 38.9 exceeds minimum threshold of 24.0", ...]
    coding_assessment: null
drop_llm:
  - model_id: some-model
    decision: drop
    tier: drop
    # same keys as keep
error:
  - model_id: some-model
    decision: error
    tier: error
    evidence: ["LLM evaluation failed: ..."]
```

- `keep` — coding-relevant models (downstream gateway consumes this).
- `drop_llm` — LLM-judged non-coding (free-model-rule drops are excluded from YAML entirely).
- `error` — judge/transport failures (not drops).

Tracer mode (`discover.py <provider>` without `--all`) writes a single-record YAML with the same keys at the top level (`provider, model_id, decision, tier, ...`) via `SingleModelWriter`.

Programmatic writers: `src/llm_discovery/results.py:ProviderBatchWriter` / `SingleModelWriter` and shims `save_provider_result()` / `save_yaml_result()`.

### Downstream handoff

`data/results/*.yaml` is the keep-list consumed by the gateway. The previous gateway is being retired in favor of **Bifrost**. To feed a new gateway, read `keep[].model_id` per provider and map each to the gateway config (use `base_url` and `secret` from `config/providers.yaml`). Diffing `data/results/*.yaml` across runs audits what entered/left the keep-list before promotion.

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
