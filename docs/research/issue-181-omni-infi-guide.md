# Research #181 — OmniRoute + Infisical prerequisites, official links, and guide copy

Part of #180 — Wayfinder: Simple local UI for omniroute_export + build-all

## Question

What are the canonical OmniRoute + Infisical prerequisites, install/run URLs, and guide copy for the local UI? Collect official links (OmniRoute repo/docs + Infisical local vs cloud docs), install commands, env setup (.env, OMNIROUTE_API_KEY, LLM_SHARED_PROJECT_ID etc.), localhost:20128 default, and offline fallback wording. Produce concise guide copy the UI can embed (Prerequisites section) and links list.

Scope: reads repo README/CONTEXT/env examples + upstream docs; no code. AFK research.

## Method

Primary sources only:

- Local repo (master, 2026-09-09):
  - `README.md` — prerequisites, secrets table, Infisical env vars, OmniRoute export section
  - `.env.example` — LLM_SHARED_PROJECT_ID / LLM_DISCOVERY_PROJECT_ID template
  - `config/providers.yaml` — infisical.environment, 22 providers with secret names
  - `src/llm_discovery/secrets.py` — infisical export --projectId pattern, load_all_secrets idempotency
  - `src/llm_discovery/search.py` + `src/llm_discovery/pipeline.py` — BRAVE_API_KEY / DISABLE_WEB_SEARCH
  - `src/llm_discovery/omniroute_export.py` — DEFAULT_OMNIROUTE_URL http://localhost:20128, --dry-run vs --apply, bulk import + combos
  - `docs/research/issue-159-import-seam.md` + `issue-160-routing-seams.md` — import vs combos seam
- Upstream (fetched 2026-09-09 via web_fetch/read_page):
  - OmniRoute canonical repo: https://github.com/diegosouzapw/OmniRoute (README Quick Start + More install methods)
  - Infisical canonical repo: https://github.com/Infisical/infisical + docs site https://infisical.com/docs/*
  - Pages: /docs/cli/overview, /docs/cli/usage, /docs/cli/commands/export, /docs/cli/commands/run, /docs/cli/commands/login, /docs/self-hosting/overview, /docs/getting-started/introduction

No secondary blog summaries; every claim traces to the source that owns it.

## Findings

### 1. Official links (canonical, with citations)

#### OmniRoute

| What | Canonical URL | Notes |
|------|---------------|-------|
| Repo (MIT, 63k star, 352 providers) | https://github.com/diegosouzapw/OmniRoute | Source of truth; README is entry point. No separate docs site. [web_fetch raw README 2026-09-09] |
| README — Quick Start / Zero-config | https://github.com/diegosouzapw/OmniRoute#readme | Sections "Works the second you install it — no keys, no config" + "Quick Start". Declares `npm i -g omniroute` boots on localhost:20128, endpoint http://localhost:20128/v1, model auto works with no keys. [web_fetch raw README lines 192-197] |
| README — More install methods | https://github.com/diegosouzapw/OmniRoute#-more-install-methods--docker-source-pnpm-arch | Docker, Bun, source, pnpm, Arch AUR, Nix, Podman. Docker default OMNIROUTE_MEMORY_MB=1024, coding agents need 8192+. [web_fetch] |
| Docker Hub | https://hub.docker.com/r/diegosouzapw/omniroute | Tag :latest follows highest published SemVer; :next follows release/v*. Pre-release not for prod. [README] |
| npm | https://www.npmjs.com/package/omniroute | `npm install -g omniroute` or `pnpm add -g omniroute@latest --allow-build=better-sqlite3`. [README] |
| Website | https://omniroute.online | Badge in README header. |
| Free-tier methodology | https://github.com/diegosouzapw/OmniRoute/blob/main/docs/reference/FREE_TIERS.md | Referenced from README hero. |
| Docker Guide | https://github.com/diegosouzapw/OmniRoute/blob/main/docs/guides/DOCKER_GUIDE.md | Linked from More install methods. |
| Community | https://discord.gg/U47eFqAXCn / https://t.me/omnirouteOficial | README footer. |

> No omniroute.com/docs subdomain — docs live in-repo under docs/ and are linked from README.

#### Infisical

| What | Canonical URL | Notes |
|------|---------------|-------|
| Marketing / platform | https://infisical.com/ | Hero: "The modern security platform for developers and agents". [web_search] |
| GitHub (29k star, MIT) | https://github.com/Infisical/infisical | Open-source platform for secrets/certs/PAM. [web_search] |
| CLI overview — installation | https://infisical.com/docs/cli/overview | All install commands (brew, winget, scoop, npm, apt, yum, apk, yay). Includes migration notice for Linux repo move to artifacts-cli.infisical.com (old Cloudsmith stops 2026-09-16). [read_page 2026-09-09] |
| CLI usage (login -> init -> run) | https://infisical.com/docs/cli/usage | 3-step quickstart: `infisical login` -> `infisical init` -> `infisical run --env=dev -- <cmd>`. Also Docker, CI export, domain config. [read_page] |
| `infisical login` reference | https://infisical.com/docs/cli/commands/login | Browser (default), direct (--email/--password), interactive, machine identities (universal-auth, kubernetes, azure, gcp-iam, aws-iam, oidc, jwt). Token via INFISICAL_TOKEN. [read_page] |
| `infisical export` reference | https://infisical.com/docs/cli/commands/export | Flags --env, --projectId, --format (dotenv/json/yaml/csv), --include-imports, --path, --expand. [read_page] |
| `infisical run` reference | https://infisical.com/docs/cli/commands/run | `infisical run [options] -- <cmd>` or `--command "<cmd>"`; --watch, --projectId, --token, --env, --path, --recursive. [read_page] |
| Self-hosting overview | https://infisical.com/docs/self-hosting/overview | Deployment options: Docker, Docker Compose, Kubernetes/Helm, Linux package, AWS, GCP. [read_page] |
| What is Infisical / Cloud vs self-hosted | https://infisical.com/docs/getting-started/introduction | Defines Cloud (app.infisical.com US/EU) vs self-hosted — same core, tradeoff is ops control vs managed guarantees. [read_page] |
| Intro: Cloud managed | https://app.infisical.com | Infisical Cloud (US + EU). Default for `infisical login` prompt. [read_page] |

### 2. OmniRoute — install / run commands

#### One-liner (recommended for local UI prerequisite list)

```bash
# npm (any OS, no Docker needed)
npm install -g omniroute        # or: pnpm add -g omniroute@latest --allow-build=better-sqlite3
omniroute                        # boots gateway + dashboard on http://localhost:20128
# verify
curl -s http://localhost:20128/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Hello!"}]}' | jq
```

Source: README "Works the second you install it — no keys, no config" diagram alt text: "1. Install — npm i -g omniroute, server boots on localhost:20128. 2. Point your tool at http://localhost:20128/v1 ... 3. It answers — call model auto" + raw README zero-config curl block [web_fetch].

#### Docker (alternative)

```bash
docker run -d --name omniroute --restart unless-stopped --stop-timeout 40 \
  -p 127.0.0.1:20128:20128 -v omniroute-data:/app/data diegosouzapw/omniroute:latest

# coding-agent workload (needs larger heap):
docker run -d --name omniroute --restart unless-stopped --stop-timeout 40 \
  -e OMNIROUTE_MEMORY_MB=8192 --memory=10g \
  -p 127.0.0.1:20128:20128 -v omniroute-data:/app/data diegosouzapw/omniroute:latest
```

Source: README "More install methods — Docker" [web_fetch].

#### From source (contributor)

```bash
cp .env.example .env && npm install
PORT=20128 npm run dev
```

Source: README More install methods — "From source" [web_fetch].

#### Other package managers (verbatim from README)

```bash
# pnpm
pnpm add -g omniroute@latest --allow-build=better-sqlite3 --allow-build=@swc/core && omniroute
# Arch AUR
yay -S omniroute-bin && systemctl --user enable --now omniroute.service
# Bun
bun install && bun run dev
```

### 3. OmniRoute — localhost:20128 default (critical for UI)

- **Default URL is `http://localhost:20128`** — hardcoded in llm-discovery as `DEFAULT_OMNIROUTE_URL = "http://localhost:20128"` (src/llm_discovery/omniroute_export.py:22) and surfaced as CLI flag `--omniroute-url` defaulting to that value. README confirms every example uses `http://localhost:20128/v1` as the single OpenAI-compatible endpoint [README].
- **No auth required for local gateway by default** — README zero-config: "no API key, no signup, no configuration. Keyless free providers OpenCode Free and Felo are pre-wired into the auto combo, so a fresh install responds out of the box." Auth via `OMNIROUTE_API_KEY` (aliases: OMNIROUTE_MANAGE_KEY / OMNIROUTE_TOKEN / OMNIROUTE_AUTH_TOKEN per get_auth_headers()) is optional; omniroute_export falls back to unauthenticated local request if absent.
- **UI should:**
  - Default the gateway URL input to `http://localhost:20128` (editable, persisted).
  - Treat omniroute_export --dry-run as safe default (no network).
  - Treat --apply as explicit user action (POST to gateway) with confirm + streaming logs + cancel, per #180 Decisions: "omniroute_export two actions (Dry Run default safe + Apply with http://localhost:20128 + confirm + streaming)".
  - Validate URL as http(s) and show hint: "OmniRoute must be running locally — see install links."

### 4. OmniRoute export — --dry-run vs --apply, bulk import + combos

Summarized from README "OmniRoute export (per-provider model control)" + src/llm_discovery/omniroute_export.py:

| Mode | Command | Network | Secrets resolved? | Output |
|------|---------|---------|-------------------|--------|
| Dry-run (safe) | `.venv/bin/python -m llm_discovery.omniroute_export --dry-run` | None | No — placeholder `apiKey: "env:SECRET"` | `data/derived/omniroute_import.json` ([{provider,name,apiKey:"env:SECRET",baseUrl}]) + `data/derived/omniroute_combos.json` ([{name,models:[{provider,model}],strategy:"reset-aware"}]) |
| Apply (idempotent POST) | `.venv/bin/python -m llm_discovery.omniroute_export --apply --omniroute-url http://localhost:20128` | Yes — bulk import + GET/POST/PUT /api/combos upsert | Yes — env vars (GROQ_API_KEY etc.) resolved; requires gateway reachable | Same files plus live gateway state; verify via `curl -s http://localhost:20128/api/combos | jq` and `curl -s http://localhost:20128/v1/chat/completions -d '{"model":"flash",...}'` |

- Import: one row per `config/providers.yaml` (22 providers), baseUrl verbatim, apiKey only at apply via resolve_import_secrets().
- Combos: pure tier partition keep-all (e.g. 100 keeps -> 100 targets), `contributor_special` normalized, strict contributor filter, sorted deterministic; empty tier skipped with warning. Default 3 combos (flash/max/contributor_free) strategy reset-aware.
- Auth for --apply: `--api-key` flag or env `OMNIROUTE_API_KEY` (also OMNIROUTE_MANAGE_KEY / OMNIROUTE_TOKEN / OMNIROUTE_AUTH_TOKEN). Falls back to unauthenticated if gateway allows it.
- Exit code 0 success, non-zero on validation; no secrets logged (redact_rows replaces with "***").
- Docs seam: issue-159 confirms bulk import is connections-only (no model pin); combos are the model-pin seam. Issue-160 confirms deterministic pin via provider/model or combo.

### 5. Infisical — local vs cloud setup (what the local repo actually does)

#### Repo actual usage (not generic tutorial)

- **Two-project model (llm-discovery convention, not Infisical-wide):**

| Env var | Holds | Purpose | Required? |
|---------|-------|---------|-----------|
| `LLM_SHARED_PROJECT_ID` | All provider keys + judge key (`GROQ_API_KEY`, `KILO_AI_API_KEY`, `CEREBRAS_API_KEY`, `OPENCODE_ZEN_API_KEY`, ..., `AGNES_AI_API_KEY` alias for judge) | `discover.py` + general runs | Only if using Infisical; else export keys directly |
| `LLM_DISCOVERY_PROJECT_ID` | Only `AA_API_KEY` (Artificial Analysis) | Catalog refresh (`refresh_catalogs.py`) | Only for refresh; negligible otherwise |

Sources: README Secrets table + .env.example comments (lines 2-9) + config/providers.yaml infisical block (environment: dev, shared_project_id_env, discovery_project_id_env) + src/llm_discovery/secrets.py:load_all_secrets() guards ("only runs when the vars are set").

- **.env file (gitignored):**

```bash
# .env.example template
LLM_SHARED_PROJECT_ID=
LLM_DISCOVERY_PROJECT_ID=
# BRAVE_API_KEY optional, DISABLE_WEB_SEARCH optional — see below
```

Actual setup:

```bash
cp .env.example .env
# edit .env to fill LLM_SHARED_PROJECT_ID + LLM_DISCOVERY_PROJECT_ID
# verify once:
infisical export --projectId "$LLM_SHARED_PROJECT_ID" --env dev --format json | jq length
infisical export --projectId "$LLM_DISCOVERY_PROJECT_ID" --env dev --format json | jq length
```

Source: README Installation 2b block.

- **Runtime pattern — Infisical run vs plain env fallback:**

| Method | Command | When |
|--------|---------|------|
| Plain env (simplest) | `export AGNES_AI_API_KEY=...; export GROQ_API_KEY=...; .venv/bin/python scripts/discover.py groq --all` | Personal / CI without Infisical, or no access to projects |
| Infisical injected | `infisical run -- .venv/bin/python scripts/discover.py groq --all` | Team setup where keys live in Infisical projects; reads .env project IDs -> export --projectId per project -> inject as env (secrets.py subprocess per project, idempotent via _secrets_loaded flag) |

Source: README "Secrets — provide API keys either directly via env vars or via Infisical (recommended for teams)" table + Installation 2a/2b + secrets.py _load_project_secrets() which calls `infisical export --projectId <id> --env <env> --format json --include-imports=false` via subprocess.

- **Historical note:** `.infisical.json` was removed (README callout: "The repo previously shipped a placeholder `.infisical.json` with a stale workspaceId. It was not used (secrets are loaded via explicit infisical export --projectId $LLM_*_PROJECT_ID). The file has been deleted; the two LLM_*_PROJECT_ID env vars are now the only Infisical config.") — UI guide must NOT tell users to create .infisical.json.

#### Infisical Cloud vs self-hosted (upstream definitions, for guide wording)

| Aspect | Cloud | Self-hosted |
|--------|-------|-------------|
| Host | https://app.infisical.com (US default) + https://eu.infisical.com (EU) | Your own domain (e.g. https://your-domain.infisical.com) |
| Login | `infisical login` -> choose Cloud (US/EU) or Self-hosted -> browser OAuth | Same, but must set domain via `INFISICAL_DOMAIN` env or `--domain` flag or .infisical.json domain field |
| Domain precedence | `--domain` > `INFISICAL_DOMAIN` > .infisical.json domain > default (app.infisical.com) | Same; legacy INFISICAL_API_URL still honored but INFISICAL_DOMAIN takes precedence |
| Ops | Managed — automated updates, availability guarantees | You own — Docker/Compose/K8s/Linux package / AWS / GCP; you patch, monitor, scale |
| When to use (per docs) | Default for most teams; minimal ops overhead | Compliance (SOC2/HIPAA/FIPS), air-gapped, tight internal integration |

Sources: https://infisical.com/docs/cli/usage#domain-configuration, https://infisical.com/docs/getting-started/introduction#deployment-models-cloud-vs-self-hosted, https://infisical.com/docs/self-hosting/overview.

Important for UI copy: llm-discovery uses Cloud by default; domain config only matters if the user runs a self-hosted Infisical. Do not force domain field unless they indicate self-hosted.

### 6. Infisical CLI — install / run commands (official)

#### Install (from https://infisical.com/docs/cli/overview#installation)

```bash
# macOS
brew install infisical/get-cli/infisical
# Windows (winget)
winget install infisical
# Windows (Scoop)
scoop bucket add org https://github.com/Infisical/scoop-infisical.git && scoop install infisical
# npm
npm install -g @infisical/cli
# Debian/Ubuntu
curl -1sLf 'https://artifacts-cli.infisical.com/setup.deb.sh' | sudo -E bash && sudo apt-get update && sudo apt-get install -y infisical
# RedHat/CentOS/Amazon Linux
curl -1sLf 'https://artifacts-cli.infisical.com/setup.rpm.sh' | sudo -E bash && sudo yum install infisical
# Alpine
apk add --no-cache bash sudo wget && wget -qO- 'https://artifacts-cli.infisical.com/setup.apk.sh' | sudo sh && apk update && sudo apk add infisical
# Arch
yay -S infisical-bin
```

Update: `brew update && brew upgrade infisical` (or respective package manager). Pin version in prod: https://github.com/Infisical/cli/releases. Source: /docs/cli/overview.

#### Auth (from https://infisical.com/docs/cli/usage + /docs/cli/commands/login)

```bash
# 1. Interactive login (browser) — picks Cloud US/EU or Self-hosted
infisical login
# Headless/SSH/WSL2/Codespaces
infisical login -i
# Domain override (self-hosted or EU)
export INFISICAL_DOMAIN="https://your-domain.infisical.com"
infisical login
# Or per-command
infisical login --domain="https://your-domain.infisical.com"
# Machine identity (CI, universal-auth)
infisical login --method=universal-auth --client-id=<id> --client-secret=<secret> --silent --plain
export INFISICAL_TOKEN=$(infisical login --method=universal-auth --client-id=<id> --client-secret=<secret> --silent --plain)
```

#### Export / run patterns used by llm-discovery

```bash
# verify secrets reachable (team setup)
infisical export --projectId "$LLM_SHARED_PROJECT_ID" --env dev --format json | jq length
infisical export --projectId "$LLM_DISCOVERY_PROJECT_ID" --env dev --format json | jq length
# export to file
infisical export --format=dotenv-export > .env
infisical export --format=json > secrets.json
# inject and run (reads .env project IDs)
infisical run -- .venv/bin/python scripts/discover.py groq --all
infisical run --env=dev -- npm run dev
infisical run --env=dev -- flask run
# machine-identity variant (CI)
infisical run --projectId <project-id> --env prod --token "$INFISICAL_TOKEN" -- <cmd>
```

Flags: --env (slug, default dev), --projectId (override .infisical.json), --format (dotenv/dotenv-export/dotenv-eval/csv/json/yaml), --include-imports, --path/--recursive, --expand, --token, --watch, --command. [read_page export/run].

### 7. Env setup table (authoritative for UI)

For UI "Environment" section + tooltip copy. Source of truth: .env.example + README Secrets + config/providers.yaml + secrets.py + search.py + omniroute_export.py.

| Variable | Required? | Where set | Purpose | Example |
|----------|-----------|-----------|---------|---------|
| `LLM_SHARED_PROJECT_ID` | Only if using Infisical | .env (gitignored) | Project holding all provider keys + judge key | UUID (e.g. 7686072c-...) — llm-discovery convention |
| `LLM_DISCOVERY_PROJECT_ID` | Only if using Infisical + catalog refresh | .env | Project holding only AA_API_KEY | UUID |
| `GROQ_API_KEY`, `KILO_AI_API_KEY`, `AGNES_AI_API_KEY`, `AA_API_KEY`, ... (one per provider, per config/providers.yaml secret) | Yes for each provider you run | Infisical project *or* plain env export | Provider auth + judge (agnes-2.0-flash) + AA refresh | sk-..., aa_xxx |
| `OMNIROUTE_API_KEY` (aliases: OMNIROUTE_MANAGE_KEY, OMNIROUTE_TOKEN, OMNIROUTE_AUTH_TOKEN) | Optional | .env or UI keychain | Gate omniroute_export --apply (management auth to localhost:20128). Falls back to unauthenticated if gateway allows | or_... or dashboard token |
| `BRAVE_API_KEY` | Optional | env / Infisical | Brave Search API (higher quality, $5 free credits/mo) | BSA... |
| `DISABLE_WEB_SEARCH` | Optional | env | Forces NoopSearcher (offline mode). Set 1 to disable all web search | 1 |
| `CLOUDFLARE_ACCOUNT_ID` | Only for cloudflare provider | env (interpolated in baseUrl ${CLOUDFLARE_ACCOUNT_ID}) | Resolves template URL in providers.yaml | account uuid |
| `INFISICAL_DOMAIN` | Only for self-hosted Infisical | env | Domain override for CLI | https://infisical.example.com |
| `INFISICAL_TOKEN` | Only for machine identity / CI | env | Bearer for infisical CLI without interactive login | opaque JWT |

Notes:
- secrets.py only runs infisical export when LLM_*_PROJECT_ID vars are present — otherwise plain env is authoritative ("If you do not use Infisical, just export the keys and ignore these two vars").
- BRAVE_API_KEY fallback: search chain is Brave (if key) -> DuckDuckGo (no key) -> Noop (if DISABLE_WEB_SEARCH=1). Without Brave key, DuckDuckGo is automatic.
- Do NOT commit .env or any *_API_KEY — data/ and .env gitignored. redact_rows in omniroute_export never logs secrets.

### 8. Offline fallback wording (for UI banner / empty state)

For when user is offline, has no BRAVE_API_KEY, or sets DISABLE_WEB_SEARCH=1. Based on README Optional env + search.py:

> **Offline mode:** Web search is optional. Without `BRAVE_API_KEY`, the pipeline falls back to DuckDuckGo (no key). Set `DISABLE_WEB_SEARCH=1` to run fully offline — evidence collection skips web search and uses offline catalogs only. Discovery, evaluation, and omniroute_export --dry-run all work offline; only --apply and Brave require network.

Short badge variant (for inline hint):

> `BRAVE_API_KEY` optional -> DuckDuckGo used. `DISABLE_WEB_SEARCH=1` = offline (no web calls).

Long help variant (for tooltip):

> Evidence search: (1) if `DISABLE_WEB_SEARCH=1` -> NoopSearcher (no network, no API key needed); (2) else if `BRAVE_API_KEY` present -> BraveSearcher (higher quality, ~ $5 free credits/mo); (3) else -> DuckDuckGo (no key, lower quality). All three paths produce the same Evidence score; only the web evidence strings differ.

### 9. Concise Prerequisites section copy the UI can embed (3–5 lines per product) + links list

Copy below is verbatim-ready for the local UI Prerequisites section (mirrors #180 Decisions: local-first, secrets never logged, .env + live env, localhost:20128 default). Each block is 3–5 lines, plus a compact links list.

---

#### OmniRoute (local AI gateway)

> **OmniRoute** is a free MIT AI gateway (352 providers, 90+ free tiers) that runs locally on `http://localhost:20128` and exposes a single OpenAI-compatible endpoint (`/v1/chat/completions`, `/v1/responses`). Install once with `npm install -g omniroute` and run `omniroute` — no API keys required; keyless `auto` model works out of the box. llm-discovery's `omniroute_export` pins your keep-list into 3 reset-aware combos (flash/max/contributor_free): use **Dry Run** to preview files locally, then **Apply** to bulk-import providers + upsert combos to the running gateway at `http://localhost:20128` (auth via `OMNIROUTE_API_KEY` if set, otherwise unauthenticated local). [README zero-config + omniroute_export section]

#### Infisical (secrets manager)

> **Infisical** stores API keys outside the repo. llm-discovery uses two projects: `LLM_SHARED_PROJECT_ID` (all provider keys + judge `AGNES_AI_API_KEY`) and `LLM_DISCOVERY_PROJECT_ID` (`AA_API_KEY` for catalog refresh) — set both in `.env` (see `.env.example`), then run via `infisical login` + `infisical run -- <cmd>` or `infisical export --projectId ... --env dev`. If you do not use Infisical, just `export GROQ_API_KEY=... AGNES_AI_API_KEY=...` and leave `LLM_*` empty — plain env fallback is fully supported. Cloud default is https://app.infisical.com; self-hosted only needs `INFISICAL_DOMAIN` override. [.env.example + README Secrets + secrets.py]

#### Offline / Search fallback

> Web search is optional. Without `BRAVE_API_KEY` DuckDuckGo is used; set `DISABLE_WEB_SEARCH=1` for fully offline runs — discovery + --dry-run still work, only --apply/Brave need network.

---

#### Links list (copy-paste for UI footer / help icon)

```markdown
**OmniRoute**
- Repo + docs: https://github.com/diegosouzapw/OmniRoute
- Quick start (zero-config): https://github.com/diegosouzapw/OmniRoute#readme
- Install (Docker/source/pnpm/Arch): https://github.com/diegosouzapw/OmniRoute#-more-install-methods--docker-source-pnpm-arch
- Docker Hub: https://hub.docker.com/r/diegosouzapw/omniroute
- npm: https://www.npmjs.com/package/omniroute
- Site: https://omniroute.online
- Free-tier catalog: https://github.com/diegosouzapw/OmniRoute/blob/main/docs/reference/FREE_TIERS.md

**Infisical**
- Site: https://infisical.com
- GitHub: https://github.com/Infisical/infisical
- CLI overview & install: https://infisical.com/docs/cli/overview
- CLI usage (login/init/run): https://infisical.com/docs/cli/usage
- Command: login: https://infisical.com/docs/cli/commands/login
- Command: export: https://infisical.com/docs/cli/commands/export
- Command: run: https://infisical.com/docs/cli/commands/run
- Cloud app (US): https://app.infisical.com
- Self-hosting: https://infisical.com/docs/self-hosting/overview
- Getting started / Cloud vs self-hosted: https://infisical.com/docs/getting-started/introduction

**llm-discovery local**
- README: config/providers.yaml source of truth, data/ gitignored
- .env.example: LLM_SHARED_PROJECT_ID / LLM_DISCOVERY_PROJECT_ID template
- OmniRoute export: README "OmniRoute export" + data/derived/omniroute_{import,combos}.json
```

### 10. Checks and next steps for UI (#180 not-yet-specified)

- UI default gateway URL must be `http://localhost:20128` (editable); validate as URL, hint "Is OmniRoute running? curl http://localhost:20128/api/combos".
- Dry Run is safe default (no secrets resolved, placeholder env:...) — primary button. Apply is secondary + confirm dialog + auth header from OMNIROUTE_API_KEY (masked .env write + live env).
- Provider dropdown source: `config/providers.yaml` live read (22 providers, secret field tells which env var). No provider-keys editor in this phase (explicit out-of-scope).
- Folder icon: copy path + try code/xdg-open/open fallback + file:// link per #180 Decisions.
- Offline banner: show when DISABLE_WEB_SEARCH=1 or fetch fails; wording above.
- No .infisical.json creation — only .env with two LLM_* vars.

## Appendix — one-liners for the guide

```bash
# OmniRoute — install + run
npm install -g omniroute && omniroute
# or Docker
docker run -d --name omniroute --restart unless-stopped --stop-timeout 40 -p 127.0.0.1:20128:20128 -v omniroute-data:/app/data diegosouzapw/omniroute:latest
# verify
curl -s http://localhost:20128/api/combos | jq
curl -s http://localhost:20128/v1/chat/completions -H "Content-Type: application/json" -d '{"model":"auto","messages":[{"role":"user","content":"hi"}]}' | jq

# llm-discovery — OmniRoute export
.venv/bin/python -m llm_discovery.omniroute_export --dry-run
cat data/derived/omniroute_import.json
cat data/derived/omniroute_combos.json
.venv/bin/python -m llm_discovery.omniroute_export --apply --omniroute-url http://localhost:20128   # needs OMNIROUTE_API_KEY if gateway requires auth

# Infisical — install + auth + run
brew install infisical/get-cli/infisical   # or: npm install -g @infisical/cli
infisical login
cp .env.example .env  # fill LLM_SHARED_PROJECT_ID + LLM_DISCOVERY_PROJECT_ID
infisical export --projectId "$LLM_SHARED_PROJECT_ID" --env dev --format json | jq length
infisical export --projectId "$LLM_DISCOVERY_PROJECT_ID" --env dev --format json | jq length
infisical run -- .venv/bin/python scripts/discover.py groq --all
# plain-env fallback (no Infisical)
export GROQ_API_KEY=... AGNES_AI_API_KEY=... AA_API_KEY=...
.venv/bin/python scripts/discover.py groq --all
```

## Citations

- Local repo: README.md (Secrets table, Installation 2a/2b, OmniRoute export, Catalog refresh), .env.example (LLM_* vars, BRAVE_API_KEY/DISABLE_WEB_SEARCH comments), config/providers.yaml (infisical environment dev, providers[].secret), src/llm_discovery/secrets.py (subprocess infisical export --projectId --env --format json), src/llm_discovery/search.py + pipeline.py (BRAVE_API_KEY / DISABLE_WEB_SEARCH tri-state), src/llm_discovery/omniroute_export.py (DEFAULT_OMNIROUTE_URL, --dry-run/--apply, bulk import + combos, get_auth_headers).
- OmniRoute upstream: https://github.com/diegosouzapw/OmniRoute README (zero-config diagram alt text, curl http://localhost:20128/v1/chat/completions, More install methods table, Docker Hub/npm links, free-tier methodology) — fetched 2026-09-09 via web_fetch raw README.
- Infisical upstream: https://infisical.com/docs/cli/overview (install), /docs/cli/usage (login/init/run, domain config INFISICAL_DOMAIN precedence), /docs/cli/commands/export, /docs/cli/commands/run, /docs/cli/commands/login (user vs universal-auth vs k8s/azure/gcp/aws/oidc/jwt), /docs/self-hosting/overview (Docker/Compose/K8s/Linux/AWS/GCP), /docs/getting-started/introduction (Cloud vs self-hosted) — fetched 2026-09-09 via read_page.
