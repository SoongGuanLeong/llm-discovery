# CLI reference

Reference material for the `llm-discovery` CLI. The walkthrough lives in the
README's 5-minute Golden Path; this document holds the guarantees, the
configuration surface, and the automation contract.

## What each step guarantees

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

## `set` means present, not valid

`config status` reports a key as `set` when it is *present*. It makes no claim
that the key works. Validity is `doctor`'s job: it probes the gateway with the
key and reports reachability (HTTP 200 means reachable, not validated).

## For agents and scripts

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

## Configuration and secrets

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
- `AA_API_KEY` / `ARTIFICIAL_ANALYSIS_API_KEY` - only needed for `refresh` (see `docs/reference/catalog.md`).

Full setup companion (gateway install, team secrets, offline mode):
`docs/omni-infi-guide.md`.

Team Infisical walkthrough:

```bash
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

## `discover` — how to run

All runs load secrets from env (or Infisical if configured) and write results to `data/results/`.

```bash
# Tracer: evaluate ONE model for a provider (deterministic pick, cheapest smoke test)
llm-discovery discover groq
llm-discovery discover kilo_ai

# Batch: evaluate ALL models for one provider, in parallel
llm-discovery discover groq --all
llm-discovery discover kilo_ai --all

# All providers: evaluate every configured provider
llm-discovery discover
llm-discovery discover --workers 4

# Help - shows configured providers from config/providers.yaml
llm-discovery discover --help
```

### What happens per run

1. `discover_models(base_url, api_key)` (or Cloudflare/BazaarLink special paths) enumerates `/models`.
2. Free Rule (`free_rule.split`) drops non-free models before any LLM cost.
3. `BenchmarkDataCache` + `ModelResolver` resolve each model against AA/models.dev/benchmarks.
4. `EvidenceCollector` + `Judge` (via `AGNES_AI_API_KEY` / `agnes-2.0-flash`) + `PolicyGate` judge coding relevance and tier (`max` >=45, `flash` 24–45, `drop` below).
5. Failures are isolated - one model error goes to the `error` bucket, other models still complete.

Concurrency: bounded `ThreadPoolExecutor(max_workers=4)` with synchronous `httpx`; results are sorted for determinism.
