# llm-discovery

Discover and evaluate cloud LLMs across multiple providers. Enumerates provider `/models` endpoints, resolves each model against offline catalogs, judges coding relevance via LLM, and writes a curated keep-list per provider.

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.12** | `requires-python = ">=3.12"` in `pyproject.toml`. |
| **uv or venv** | Either `uv venv && uv pip install -e .` or `python -m venv .venv && .venv/bin/pip install -e .`. |
| **Infisical CLI** | Installed and authenticated (`infisical login`). Used to inject secrets at runtime — no keys are committed. |
| **Podman + systemd Quadlet** | Required on the gateway host (see `~/projects/llm-gateway`). Not needed to run discovery locally, but needed to deploy the keep-list to LiteLLM. |
| **Two Infisical project IDs** | Stored in local `.env` (gitignored): |
| | `INFISICAL_SHARED_PROJECT_ID` — shared project (judge LLM key, search keys). |
| | `INFISICAL_DISCOVERY_PROJECT_ID` — discovery project (per-provider API keys). |
| **Infisical secrets** | `OPENCODE_ZEN_API_KEY` lives in the **shared** project; each provider key (e.g. `GROQ_API_KEY`, `KILO_AI_API_KEY`, `CEREBRAS_API_KEY`) lives in the **discovery** project. The judge LLM key (`AGNES_AI_API_KEY` via `config/providers.yaml: judge_llm.secret`) also comes from the shared project. |
| **`.env` file** | Copy `.env.example` → `.env` and fill the two project IDs. Never commit `.env`. |

`.infisical.json` in the repo has a placeholder `workspaceId`; the actual project IDs come from `.env` via `--projectId` flags in `src/llm_discovery/secrets.py`.

Optional env vars (also via Infisical or local env):

- `BRAVE_API_KEY` — Brave Search API (higher quality web search; without it, DuckDuckGo is used). Set `DISABLE_WEB_SEARCH=1` for offline/Noop mode.
- `AA_API_KEY` / `ARTIFICIAL_ANALYSIS_API_KEY` — only needed for `refresh` (see below).

## Installation

```bash
# 1. Clone and create env
git clone <repo> && cd llm-discovery
uv venv --python 3.12          # or: python3.12 -m venv .venv
source .venv/bin/activate
uv pip install -e .            # or: .venv/bin/pip install -e .

# 2. Infisical CLI (if not installed)
# https://infisical.com/docs/cli/overview
infisical login

# 3. Local env — two project IDs
cp .env.example .env
# edit .env:
#   INFISICAL_SHARED_PROJECT_ID=<shared project id>
#   INFISICAL_DISCOVERY_PROJECT_ID=<discovery project id>

# 4. Verify secrets inject (no keys printed)
infisical export --projectId "$INFISICAL_SHARED_PROJECT_ID" --env dev --format json | jq length
infisical export --projectId "$INFISICAL_DISCOVERY_PROJECT_ID" --env dev --format json | jq length
```

No provider or judge keys are hardcoded. `src/llm_discovery/secrets.py:load_all_secrets()` calls `infisical export --projectId <id> --env dev` for both projects at runtime.

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

All runs load secrets from Infisical and write results to `data/results/`. No keys are requested interactively.

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
4. `EvidenceCollector` + `Judge` (via `AGNES_AI_API_KEY` / `agnes-2.0-flash`) + `PolicyGate` judge coding relevance and tier (`max` ≥45, `flash` 24–45, `drop` below).
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

- `keep` — coding-relevant models (use these).
- `drop_llm` — LLM-judged non-coding (free-model-rule drops are excluded from YAML entirely).
- `error` — judge/transport failures (not drops).

Tracer mode (`discover.py <provider>` without `--all`) writes a single-record YAML with the same keys at the top level (`provider, model_id, decision, tier, ...`) via `SingleModelWriter`.

Programmatic writers: `src/llm_discovery/results.py:ProviderBatchWriter` / `SingleModelWriter` and shims `save_provider_result()` / `save_yaml_result()`.

## Keep-list → llm-gateway (LiteLLM) handoff

The keep-list is the source of truth for `~/projects/llm-gateway` — a local LiteLLM gateway (Podman + systemd Quadlet + PostgreSQL + Infisical).

`llm-gateway` layout:

```
~/projects/llm-gateway/
  config.yaml              # LiteLLM: model_list: [] (populated from keep-list)
  catalog/providers.yaml   # provider catalog (type: native | openai_compatible)
  containers/litellm.container  # Quadlet — managed secrets injected between
                                #   # BEGIN MANAGED SECRETS / # END MANAGED SECRETS
  scripts/setup-secrets.sh # pulls Infisical LITELLM + shared projects → Podman secrets
  scripts/install.sh       # installs Quadlets, starts postgres + litellm
```

How to feed the keep-list:

1. Run discovery and inspect the keep-list:
   ```bash
   .venv/bin/python scripts/discover.py kilo_ai --all
   cat data/results/kilo_ai.yaml          # keep[].model_id is what to route
   # or for all providers:
   .venv/bin/python scripts/discover.py --all-providers
   ls data/results/*.yaml
   ```

2. Translate keep entries into `~/projects/llm-gateway/config.yaml: model_list`. Each keep entry maps to one LiteLLM model (use the provider's `base_url` from `config/providers.yaml` and the secret name from the same file). Example (illustrative):

   ```yaml
   # ~/projects/llm-gateway/config.yaml
   model_list:
     - model_name: kilo-minimax-m2.7
       litellm_params:
         model: openai/minimax/minimax-m2.7:free
         api_base: https://api.kilo.ai/api/gateway
         api_key: os.environ/KILO_AI_API_KEY
   ```

   Keys like `KILO_AI_API_KEY` / `OPENCODE_ZEN_API_KEY` are provisioned to the gateway via `setup-secrets.sh` (which syncs Infisical → Podman secrets → Quadlet `Secret=...,type=env,target=...`). Keep the Infisical project IDs in `~/projects/llm-gateway/.env` (`INFISICAL_LITELLM_PROJECT_ID` + `INFISICAL_SHARED_PROJECT_ID`) separate from this repo's `.env`.

3. Re-provision and restart the gateway:
   ```bash
   cd ~/projects/llm-gateway
   ./scripts/setup-secrets.sh   # idempotent: rebuilds Podman secrets from Infisical
   ./scripts/install.sh         # verifies secrets, installs Quadlets, starts services
   systemctl --user is-active litellm.service
   curl -fsS http://127.0.0.1:4000/health/liveliness  # -> "I'm alive!"
   ```

Tip: `data/results/*.yaml` can be diffed across runs to audit what entered/left the keep-list before promoting to the gateway.

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
