# OmniRoute + Infisical setup guide

Setup companion for the README Golden Path. The retired `ui/` used to render
this content inline; this page replaces it. All commands run from the repo root.

## OmniRoute (local AI gateway)

OmniRoute is a free MIT AI gateway that runs locally on `http://localhost:20128`
and exposes a single OpenAI-compatible endpoint (`/v1/chat/completions`).
Install once, run it, leave it up while you work the Golden Path.

```bash
# npm (any OS, no Docker needed)
npm install -g omniroute
omniroute                        # boots gateway + dashboard on http://localhost:20128

# verify
curl -s http://localhost:20128/api/combos | jq
curl -s http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Hello!"}]}' | jq
```

Docker alternative:

```bash
docker run -d --name omniroute --restart unless-stopped --stop-timeout 40 \
  -p 127.0.0.1:20128:20128 -v omniroute-data:/app/data diegosouzapw/omniroute:latest
```

No API key, no signup, no configuration for a fresh install: keyless `auto`
model answers out of the box. Management auth for `export apply` is optional
and falls back to unauthenticated local when the gateway allows it.

### Export: dry-run vs apply

| Mode | Command | Network | Output |
|------|---------|---------|--------|
| Dry-run (safe default) | `llm-discovery export dry-run` | None | `data/derived/omniroute_import.json` + `data/derived/omniroute_combos.json` |
| Apply (explicit) | `llm-discovery export apply` | Yes, bulk import + `GET`/`POST`/`PUT /api/combos` upsert | Same files plus live gateway state |

Import is one row per `config/providers.yaml` (`baseUrl` verbatim, `apiKey`
resolved from env only at apply). Combos partition the keep-list by tier
(`flash`/`max`/`contributor_free`, strategy `auto`); empty tiers are skipped
and a stale empty-tier combo on the gateway is deleted on apply.

Auth for apply: env `OMNIROUTE_API_KEY` preferred (also honoured:
`OMNIROUTE_MANAGE_KEY` / `OMNIROUTE_TOKEN` / `OMNIROUTE_AUTH_TOKEN`).
`--api-key` survives but argv leaks into `ps` and shell history, so prefer env.

Verify after apply:

```bash
curl -s http://localhost:20128/api/combos | jq
curl -s http://localhost:20128/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"flash","messages":[{"role":"user","content":"hi"}]}' | jq
```

## Infisical (secrets manager, team setup)

Infisical stores API keys outside the repo. Plain env export always works;
use Infisical when keys live in shared projects.

Two-project convention (llm-discovery only, not Infisical-wide):

| Env var | Holds | Purpose |
|---------|-------|---------|
| `LLM_SHARED_PROJECT_ID` | All provider keys + judge key (`GROQ_API_KEY`, `KILO_AI_API_KEY`, `AGNES_AI_API_KEY`, ...) | `discover` + general runs |
| `LLM_DISCOVERY_PROJECT_ID` | Only `AA_API_KEY` (Artificial Analysis) | Catalog `refresh` |

Setup:

```bash
infisical login
cp .env.example .env
# edit .env: fill LLM_SHARED_PROJECT_ID + LLM_DISCOVERY_PROJECT_ID
infisical export --projectId "$LLM_SHARED_PROJECT_ID" --env dev --format json | jq length
infisical export --projectId "$LLM_DISCOVERY_PROJECT_ID" --env dev --format json | jq length
# run with injected env:
infisical run -- llm-discovery discover groq --all
```

Plain-env fallback (no Infisical): export keys directly and leave `LLM_*` empty.

```bash
export AGNES_AI_API_KEY=... GROQ_API_KEY=... KILO_AI_API_KEY=...
export AA_API_KEY=aa_xxx   # only needed for refresh
```

Cloud default is https://app.infisical.com. Self-hosted only needs an
`INFISICAL_DOMAIN` override. Never create `.infisical.json`; the two
`LLM_*_PROJECT_ID` vars are the only Infisical config.

## Environment variables

| Variable | Required? | Purpose |
|----------|-----------|---------|
| `LLM_SHARED_PROJECT_ID` / `LLM_DISCOVERY_PROJECT_ID` | Only if using Infisical | Project IDs in `.env` (gitignored) |
| `GROQ_API_KEY`, `KILO_AI_API_KEY`, `AGNES_AI_API_KEY`, `AA_API_KEY`, ... (one per provider, see `secret` in `config/providers.yaml`) | Yes for each provider run | Provider auth + judge + AA refresh |
| `OMNIROUTE_API_KEY` (aliases above) | Only for `export apply` when gateway requires auth | Gateway management key |
| `BRAVE_API_KEY` | Optional | Higher-quality web search; without it DuckDuckGo is used |
| `DISABLE_WEB_SEARCH=1` | Optional | Fully offline runs |
| `AA_API_KEY` / `ARTIFICIAL_ANALYSIS_API_KEY` | Only for `refresh` | Artificial Analysis catalog |
| `CLOUDFLARE_ACCOUNT_ID` | Only for cloudflare provider | Resolves template URL in providers.yaml |

Secrets are never logged; `config set-key` masks to the last four characters.

## Offline fallback

Web search is optional. Without `BRAVE_API_KEY`, the pipeline falls back to
DuckDuckGo (no key). Set `DISABLE_WEB_SEARCH=1` to run fully offline:
discovery plus `export dry-run` still work; only `export apply` and Brave
need network.

## Links

**OmniRoute**

- Repo + docs: https://github.com/diegosouzapw/OmniRoute
- Quick start (zero-config): https://github.com/diegosouzapw/OmniRoute#readme
- Docker Hub: https://hub.docker.com/r/diegosouzapw/omniroute
- npm: https://www.npmjs.com/package/omniroute
- Site: https://omniroute.online
- Free-tier catalog: https://github.com/diegosouzapw/OmniRoute/blob/main/docs/reference/FREE_TIERS.md

**Infisical**

- Site: https://infisical.com
- GitHub: https://github.com/Infisical/infisical
- CLI overview and install: https://infisical.com/docs/cli/overview
- CLI usage (login/init/run): https://infisical.com/docs/cli/usage
- Command: login: https://infisical.com/docs/cli/commands/login
- Command: export: https://infisical.com/docs/cli/commands/export
- Command: run: https://infisical.com/docs/cli/commands/run
- Cloud app (US): https://app.infisical.com
- Self-hosting: https://infisical.com/docs/self-hosting/overview
- Cloud vs self-hosted: https://infisical.com/docs/getting-started/introduction

## Source

Condensed from research `issue-181` (2026-09-09, primary sources only:
repo README / `.env.example` / `config/providers.yaml` /
`src/llm_discovery/secrets.py`, `search.py`, `omniroute_export.py` plus the
upstream OmniRoute README and Infisical docs above). Interface updated for
the `llm-discovery` CLI (#267 map, ADR 0010): `llm-discovery export dry-run`
/ `export apply` replace `python -m llm_discovery.omniroute_export --dry-run`
/ `--apply`, and `llm-discovery refresh` replaces `scripts/refresh_catalogs.py`.
