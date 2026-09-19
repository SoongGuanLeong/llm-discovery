# llm-discovery

Discover and evaluate cloud LLMs across multiple providers. Enumerates provider `/models` endpoints, resolves each model against offline catalogs, judges coding relevance via LLM, and writes a curated keep-list per provider.

## 5-minute Golden Path

Fresh clone to a curated keep-list applied to your OmniRoute gateway. Run every
command from the repo root.

**Prerequisites:** Python 3.12, `git`, a reachable OmniRoute gateway, and a
management key for it (required: `doctor` and `export apply` fail without one).
`config/providers.yaml` must list at least one provider - that file is yours to
edit by hand; no CLI command writes it. See Prerequisites below for its shape.

```bash
git clone <repo> && cd llm-discovery
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -e .

# 1. Check the prerequisites. Every failure names its own fix.
llm-discovery doctor

# 2. Store the gateway key. The value is read from stdin, never from argv.
OMNIROUTE_KEY="paste-management-key-here"
printf %s "$OMNIROUTE_KEY" | llm-discovery config set-key OMNIROUTE_API_KEY

# 3. See which Configured Providers discovery will iterate over.
llm-discovery providers list

# 4. Discover. With no provider argument this covers every Configured Provider.
llm-discovery discover --workers 4

# 5. Build the store: fresh Keepers are reused, Candidates are re-judged.
llm-discovery build

# 6. Inspect the payload that apply would send. Local files only, no network.
llm-discovery export dry-run

# 7. Apply it to the gateway.
llm-discovery export apply

# 8. Verify.
curl -s http://localhost:20128/api/combos | jq
curl -s http://localhost:20128/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"flash","messages":[{"role":"user","content":"hi"}]}' | jq
```

Steps 4 and 5 dominate the wall clock - they call provider APIs and the LLM
judge. Everything before them finishes in under a minute.

### What each step guarantees

| # | command | guarantee |
| --- | --- | --- |
| 1 | `doctor` | exits `0` only when every **required** prerequisite passes. Catalogs are **advisory**, so a fresh clone is never blocked by their absence. A failure is actionable: it prints the observed value and a copy-pasteable fix. |
| 2 | `config set-key` | reads the value from stdin (`getpass` on a TTY, raw read when piped), writes `.env` at `chmod 600`, and prints only the last four characters masked. |
| 3 | `providers list` | prints each provider's secret **env-var name**, never a value. |
| 4 | `discover` | progress goes to stderr; the result goes to stdout. |
| 5 | `build` | same split. Reuses Keepers inside the 14-day Record TTL, re-judges Candidates. |
| 6 | `export dry-run` | writes `data/derived/omniroute_import.json` + `data/derived/omniroute_combos.json`, no network. `data` **is** the payload `apply` would send, unwrapped. |
| 7 | `export apply` | POSTs the payload to the gateway. Gateway unreachable exits `4` (`pipeline`), not `1`. |
| 8 | verify | `combos` shows only non-empty tier combos (`strategy: auto`); chat call against `flash` answers. |

Prefer env over flags: `export dry-run|apply --api-key` survives, but
`OMNIROUTE_API_KEY` is preferred because argv leaks into `ps` and shell history.

### `set` means present, not valid

`config status` reports a key as `set` when it is *present*. It makes no claim
that the key works. Validity is `doctor`'s job: it probes the gateway with the
key and reports reachability (HTTP 200 means reachable, not validated).

### For agents

`--json` is accepted before or after the subcommand - root-first is the canonical
form - and produces exactly one envelope on stdout at exit: on success, on a
usage error, on an exception, and on Ctrl-C. Progress, warnings and prompts stay
on stderr, so a pipe is always parseable:

```bash
llm-discovery build --json 2>/dev/null | jq '.data.totals'

# just the fixes for whatever is not ready
llm-discovery doctor --json | jq -r '.data.checks[] | select(.status=="fail") | .fix'

# the human progress view, with the payload discarded
llm-discovery build 2>&1 >/dev/null
```

Exit codes are 1:1 with `error.code`, so a switch on the exit status and a switch
on `error.code` can never disagree:

| exit | `error.code` | meaning |
| --- | --- | --- |
| 0 | - | success |
| 1 | `internal` | unexpected bug |
| 2 | `usage` | bad flag or argument |
| 3 | `prerequisite` | config, secret or gateway not ready |
| 4 | `pipeline` | the command ran and the pipeline failed |
| 130 | `interrupted` | Ctrl-C |

### Where to look next

- `llm-discovery catalog aa search <query>`, `catalog aa filter --min-score N`,
  `catalog models show <id>`, `catalog models providers <id>`,
  `catalog providers show <id>`, `catalog providers models <id>` - offline
  catalog queries. No network, no key.
- `llm-discovery refresh` - refresh the catalog snapshots (`AA_API_KEY` from the
  environment is the only path; there is no key-taking flag).
- `llm-discovery <command> --help` - help exists at every level.
- `docs/omni-infi-guide.md` - OmniRoute install/run, Infisical team setup, env
  table, offline fallback. Promoted from research `issue-181`; the retired `ui/`
  used to render it inline.

## Prerequisites

- **Python 3.12** (`requires-python = ">=3.12"` in `pyproject.toml`)
- **uv or venv** - `uv venv && uv pip install -e .` or `python -m venv .venv && .venv/bin/pip install -e .`
- **Provider configuration** - `config/providers.yaml` is the source of truth. Each entry declares `name`, `base_url`, and `secret` (env var name holding the API key):

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

- **Secrets** - provide API keys either directly via env vars or via Infisical (recommended for teams):

  | Method | How |
  |---|---|
  | **Plain env** | `export GROQ_API_KEY=... AGNES_AI_API_KEY=... AA_API_KEY=...` |
  | **Infisical** | Store keys in two projects and export via `infisical export`. Set local `.env` (gitignored, see `.env.example`): |

  ```bash
  LLM_SHARED_PROJECT_ID=<project with all provider keys + AGNES_AI_API_KEY>
  LLM_DISCOVERY_PROJECT_ID=<project with AA_API_KEY only>
  ```

  `LLM_SHARED_PROJECT_ID` holds every provider key (`GROQ_API_KEY`, `KILO_AI_API_KEY`, `CEREBRAS_API_KEY`, ...) plus the judge key `AGNES_AI_API_KEY`. `LLM_DISCOVERY_PROJECT_ID` holds only `AA_API_KEY` (used for catalog refresh). If you do not use Infisical, just export the keys and ignore these two vars - `src/llm_discovery/secrets.py:load_all_secrets()` only runs when the vars are set.

No Podman, systemd, or gateway setup is required to run discovery locally.

> **.infisical.json removed** - the repo previously shipped a placeholder `.infisical.json` with a stale `workspaceId`. It was not used (secrets are loaded via explicit `infisical export --projectId $LLM_*_PROJECT_ID`). The file has been deleted; the two `LLM_*_PROJECT_ID` env vars are now the only Infisical config.

Optional env vars:

- `BRAVE_API_KEY` - Brave Search API (higher quality web search; without it, DuckDuckGo is used). Set `DISABLE_WEB_SEARCH=1` for offline mode.
- `AA_API_KEY` / `ARTIFICIAL_ANALYSIS_API_KEY` - only needed for `refresh` (see below).

Full setup companion (gateway install, team secrets, offline mode):
`docs/omni-infi-guide.md`.

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
infisical run -- llm-discovery discover groq --all
```

No provider or judge keys are hardcoded. See `config/providers.yaml` for the full provider list and secret names.

## Catalog data sources

Discovery resolves every provider model against two offline snapshots in `data/` (gitignored, refreshed via `llm-discovery refresh`):

| Source | Snapshot | Origin | Refresh |
|---|---|---|---|
| **models.dev** | `data/models_dev_catalog.json` | `https://models.dev/catalog.json` (public, no key) | `llm-discovery refresh` |
| **Artificial Analysis** | `data/artificial_analysis_models.json` | `https://artificialanalysis.ai/api/v2/data/llms/models` (header `x-api-key: $AA_API_KEY`) | same; requires `AA_API_KEY` |
| **Benchmarks** | `data/benchmarks.json` | Rebuilt locally via `BenchmarkDataCache.collect_from_local(aa, models_dev)` - no network | same |

Filtered catalogs can be queried offline without secrets:

```bash
llm-discovery catalog aa search "llama"
llm-discovery catalog aa filter --min-score 50
llm-discovery catalog models show <model-id>
```

## How to run

All runs load secrets from env (or Infisical if configured) and write results to `data/results/`.

```bash
# Tracer: evaluate ONE model for a provider (deterministic pick, cheapest smoke test)
llm-discovery discover groq
llm-discovery discover kilo_ai

# Batch: evaluate ALL models for one provider, in parallel
llm-discovery discover groq --all
llm-discovery discover kilo_ai --all
llm-discovery discover openrouter --all --workers 4   # default 4

# All providers: evaluate every configured provider
llm-discovery discover
llm-discovery discover --workers 4

# Help - shows configured providers from config/providers.yaml
llm-discovery discover --help
```

### What happens per run

1. `discover_models(base_url, api_key)` (or Cloudflare/BazaarLink special paths) enumerates `/models`.
2. Free-model filter (`_split_by_free_rule`) drops non-free models before any LLM cost.
3. `BenchmarkDataCache` + `ModelResolver` resolve each model against AA/models.dev/benchmarks.
4. `EvidenceCollector` + `Judge` (via `AGNES_AI_API_KEY` / `agnes-2.0-flash`) + `PolicyGate` judge coding relevance and tier (`max` >=45, `flash` 24–45, `drop` below).
5. Failures are isolated - one model error goes to the `error` bucket, other models still complete.

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

- `keep` - coding-relevant models (downstream gateway consumes this).
- `drop_llm` - LLM-judged non-coding (free-model-rule drops are excluded from YAML entirely).
- `error` - judge/transport failures (not drops).

Tracer mode (`discover <provider>` without `--all`) writes a single-record YAML with the same keys at the top level (`provider, model_id, decision, tier, ...`) via `SingleModelWriter`.

Programmatic writers: `src/llm_discovery/results.py:ProviderBatchWriter` / `SingleModelWriter` and shims `save_provider_result()` / `save_yaml_result()`.

### Downstream handoff

`data/results/*.yaml` is the keep-list. To feed a gateway, read `keep[].model_id` per provider and map each to the gateway config (use `base_url` and `secret` from `config/providers.yaml`). Diffing `data/results/*.yaml` across runs audits what entered/left the keep-list before promotion.

### OmniRoute export (per-provider model control)

Single command pins every keep to an OmniRoute tier combo (`flash`/`max`/`contributor_free`, keep-all, `auto` strategy). Only tiers with at least one keep get a combo; an empty tier's stale gateway combo is deleted on apply. Source of truth stays `config/providers.yaml` + `data/results/*.yaml`; secrets never inline.

```bash
# Dry-run - writes files only, no network
llm-discovery export dry-run
cat data/derived/omniroute_import.json        # [{provider,name,apiKey:"env:SECRET",baseUrl}]
cat data/derived/omniroute_combos.json        # [{name,models:[{provider,model}],strategy:"auto"}]
# Example fixture committed for shape reference
cat data/derived/examples/omniroute_combos.example.json

# Apply - resolves env secrets and POSTs to gateway (idempotent)
# Requires OmniRoute reachable at localhost:20128 and env keys (GROQ_API_KEY, etc.)
# Auth via env OMNIROUTE_API_KEY or --api-key, fallback to unauthenticated local gateway
llm-discovery export apply --gateway-url http://localhost:20128
# Verify
curl -s http://localhost:20128/api/combos | jq    # shows only non-empty tier combos, strategy "auto"
curl -s http://localhost:20128/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"flash","messages":[{"role":"user","content":"hi"}]}' | jq
```

- Import: one row per `config/providers.yaml`, `baseUrl` verbatim, `apiKey` resolved from env only at apply.
- Combos: pure tier partition keep-all (e.g. 100 keeps → 100 targets), `contributor_special` normalized, strict `contributor` filter, sorted deterministic.
- CLI: `dry-run` (local only), `apply` (bulk import + `GET`/`POST`/`PUT /api/combos` upsert), `--gateway-url`, `--api-key` (prefer env: argv leaks into `ps` and shell history); exit 0 success, non-zero on validation; no secrets logged.

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

- AA source: `https://artificialanalysis.ai/api/v2/data/llms/models` (header `x-api-key: $AA_API_KEY`)
- models.dev source: `https://models.dev/catalog.json` (public)
- benchmarks: rebuilt locally via `BenchmarkDataCache.collect_from_local()` (no network)
- Backups: `data/*.json.bak` (prior snapshot copied before atomic rename)
- Atomic: temp file + `fsync` + `replace` in same directory

### Automated refresh (systemd timer, issue #140)

A daily user timer (`config/quadlet/refresh-catalogs.service` + `.timer`, `OnCalendar=daily`, diff-before-copy) runs `llm-discovery refresh` from the repo root with the repo venv python (models.dev needs no key).

```bash
systemctl --user status refresh-catalogs.timer   # next run + last status
journalctl --user -u refresh-catalogs -f         # follow a run
```

The timer is optional - manual refresh above always works. Independently, `build-all` checks catalog `fetched_at` before pricing re-average: if either catalog is older than 14 days it refreshes first (warn-only - a failed refresh never fails the build). Tune with `--catalog-max-age-days N` (0 disables) or `--no-catalog-refresh`.

## Query catalogs

```bash
llm-discovery catalog aa search "llama"
llm-discovery catalog aa filter --min-score 50
llm-discovery catalog models show <model-id>
llm-discovery catalog providers models <provider-id>
```

## Interface decisions (why no UI, no server)

Recorded from Wayfinder #267 so the retired surface is not re-suggested.
Normative contract: `docs/adr/0010-cli-replaces-ui-parity-contract.md`.

- **The CLI is the contract. No HTTP surface.** `ui/` removed entirely (#273).
  Terminal streaming on stderr replaces SSE; Ctrl-C (exit 130) replaces the
  cancel endpoint; `--json` replaces SSE for programmatic callers.
- **Invocation:** `llm-discovery` after `pip install -e .`;
  `python -m llm_discovery` keeps working. One surface only.
- **Surface:** `config status|set-key|clear-key`, `providers list`, `discover`,
  `build`, `export dry-run|apply`, `refresh`, `catalog ...`, `doctor`.
- **Naming:** `providers` means the Configured Provider set
  (`config/providers.yaml`). models.dev / AA queries live under `catalog`.
- **`doctor` is the gate:** required checks block the Golden Path, advisory
  checks warn. Every failure carries a copy-pasteable `fix`.
- **Contract:** stdout is payload only, stderr is progress; exit taxonomy
  0/1/2/3/4/130 maps 1:1 to `error.code`. Secrets enter via stdin or env,
  never argv (hence no `refresh --aa-api-key`).
- **Retired flags (do not re-add):** `discover --all-providers` (use
  `discover` with no provider, or `<provider> --all`), `build --max-workers`
  (use `--workers`) and the hidden positional provider slot, `refresh
  --aa-api-key` (env only), `export --check` (use `dry-run`), `--omniroute-url`
  (renamed `--gateway-url`). Bare `omniroute_export` with no mode used to exit
  0; the CLI requires explicit `dry-run|apply`.
- **Deletion gate (done):** `ui/` plus redundant `scripts/` deleted only after
  `pytest tests/parity` passed (#272 → #273). Do not reintroduce `scripts/discover.py`, `scripts/build_all.py`, `scripts/query.py`, `scripts/refresh_catalogs.py`, or a gateway daemon.
