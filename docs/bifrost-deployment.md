# Bifrost Gateway Deployment

This document describes how to deploy the Bifrost AI Gateway locally using Podman Quadlet
(user systemd services) with an npx fallback for hosts where Quadlet is unavailable.

## Quick Start (Podman Quadlet via setup.sh)

### Prerequisites

- Podman 4.0+ with Quadlet support (`podman --version`)
- User linger enabled: `loginctl enable-linger $USER`
- Infisical CLI (`infisical --version`) and `.env` with `LLM_SHARED_PROJECT_ID` + `LLM_DISCOVERY_PROJECT_ID` (see `.env.example`)
- Python 3.12+ (and `uv` preferred, fallback to `.venv/bin/python`)

### 0. Configure .env (once, no sourcing needed)

```bash
cp .env.example .env
# edit .env: set LLM_SHARED_PROJECT_ID + LLM_DISCOVERY_PROJECT_ID (UUIDs)
# exported env vars override .env; quotes/comments/whitespace are stripped, last occurrence wins
cat .env
```

### 1. Preflight + Setup (one command after infisical login)

```bash
# Login to Infisical (one-time, stores token in ~/.infisical / OS keyring)
infisical login

# Read-only preflight: prints [1/7]..[7/7] OK/WARN/FAIL + PASS/FAIL table, writes nothing
# [7/7] is Bifrost drift: file vs db/api counts + mtimes (fails if 20 vs 16 or 314 vs 304)
scripts/setup.sh --check
# -> All checks PASS - ready for: ./scripts/setup.sh --yes
# If drift FAIL: restore secret/env, regen, restart:
#   uv run python scripts/generate-bifrost-config.py
#   systemctl --user restart bifrost
#   # or npx fallback: npx -y @maximhq/bifrost --app-dir ./data/bifrost

# Default: Podman secret handoff (Secret type=env, no plaintext file)
scripts/setup.sh --yes

# Fallback: explicit file handoff (atomic 0600 file at ~/.config/bifrost/bifrost.env)
scripts/setup.sh --yes --secrets=file
```

What setup does (idempotent reconciliation, safe to re-run):
- Parses `.env` directly (no `source`), validates UUIDs
- Probes `infisical export` check-only; on fail prints `infisical login` hint
- Exports secrets via `infisical export --format dotenv --include-imports=false --silent` and hands to Bifrost:
  - default `podman`: `podman secret create --env-file` (`type=env`), removes stale plaintext file, Quadlet uses `Secret=bifrost-env,type=env`
  - `--secrets=file`: atomic `mktemp + chmod 600 + mv` to `~/.config/bifrost/bifrost.env` (or `.tmp/bifrost.env` fallback when HOME read-only), Quadlet uses `EnvironmentFile=%h/.config/bifrost/bifrost.env`
- Generates `data/bifrost/config.json` + `shim_map.json` (env.VAR refs, never inline secrets)
- Installs Quadlet units to `~/.config/containers/systemd/` (diff-before-copy, `Source=` patched to absolute `$(pwd)/data/bifrost`, 0644 perms, daemon-reload only on change, then `restart`/`enable --now`)

Flags: `--check` (dry-run, no writes), `--yes` (skip overwrite prompt), `--secrets=file|podman` (default: podman), `--help`.

Permissions: secrets file is forced 0600 (warn/fix if drift). Re-run is idempotent: `up-to-date (no rewrite)` and quadlet `up-to-date (no copy)`, daemon-reload skipped when unchanged.

### 2. Manual secrets (without Infisical)

Create `~/.config/bifrost/bifrost.env` with provider keys (0600):

```bash
mkdir -p ~/.config/bifrost
cat > ~/.config/bifrost/bifrost.env <<'EOF'
GROQ_API_KEY=your-groq-key
CEREBRAS_API_KEY=your-cerebras-key
# ... add other provider keys (see Environment File Schema below)
CLOUDFLARE_API_KEY=your-cf-key
CLOUDFLARE_ACCOUNT_ID=your-cf-account-id
EOF
chmod 600 ~/.config/bifrost/bifrost.env
# then run setup with file mode or generate directly:
scripts/setup.sh --yes --secrets=file
# or: uv run python scripts/generate-bifrost-config.py
```

### 3. Verify Health

```bash
curl http://localhost:8080/health
curl http://localhost:8080/v1/models
systemctl --user status bifrost
# enable linger for auto-start after logout:
loginctl enable-linger $USER
```

### 4. View Logs

```bash
journalctl --user -u bifrost -f
journalctl --user -u bifrost -n 100
```

## npx Fallback (No Podman/Quadlet)

On hosts where Podman cannot create /run/user/1000/libpod (read-only FS,
rootless limitations, etc.), use the npx fallback which provides identical
file-only semantics without systemd integration.

```bash
# From project root (where data/bifrost/ exists)
npx -y @maximhq/bifrost --app-dir ./data/bifrost
```

This runs Bifrost directly with the same data/bifrost/config.json and
data/bifrost/shim_map.json. No auto-restart, no journal logs, no health
check automation - but identical routing behavior.

**Note:** The npx process runs in foreground. Use a terminal multiplexer (tmux,
screen) or background it with nohup for persistence.

## Environment File Schema

The ~/.config/bifrost/bifrost.env file uses standard VAR=value format.
All provider secrets are referenced as env.VAR in the generated
config.json (never inlined).

Required variables (matching config/providers.yaml):

| Provider | Env Var | Notes |
|----------|---------|-------|
| agnes | AGNES_AI_API_KEY | |
| ainative | AINATIVE_API_KEY | |
| bazaarlink | BAZAARLINK_API_KEY | |
| cerebras | CEREBRAS_API_KEY | |
| cloudflare | CLOUDFLARE_API_KEY + CLOUDFLARE_ACCOUNT_ID | Both required |
| cohere | COHERE_API_KEY | |
| google | GEMINI_API_KEY | |
| groq | GROQ_API_KEY | |
| kilo_ai | KILO_AI_API_KEY | |
| llm7 | LLM7_API_KEY | |
| mistral | MISTRAL_API_KEY | |
| modelscope | MODELSCOPE_API_KEY | |
| nararouter | NARAROUTER_API_KEY | |
| navy_ai | NAVY_AI_API_KEY | |
| opencode_zen | OPENCODE_ZEN_API_KEY | |
| openrouter | OPENROUTER_API_KEY | |

Providers with missing keys are omitted from the generated config (logged as
"skipped" by the generator). This is intentional - no placeholder entries that
would 401 at inference time.

## Tier Routing

The gateway exposes three logical model aliases via the shim (Phase 2):

- model: "flash" - routes to any flash-tier keep (46 variants)
- model: "max" - routes to any max-tier keep (84 variants)
- model: "contributor_free" - routes to contributor-marked keeps only (2 variants)

**Strict isolation:** No automatic cross-tier fallback. Empty tier returns
503 Service Unavailable with Retry-After and tier_unavailable error.

## DSH Wiring

DSH (`llm-pi-ai` adapter) consumes the three Model Groups via the shim sidecar
on `:8081` (alias) and direct pins via Bifrost on `:8080`. Full wiring,
credentials, and verification are documented in [DSH Bifrost Wiring](dsh-bifrost-wiring.md).

- Examples: `config/dsh/cordis.patch.yml.example` (profile overlay) and
  `config/dsh/settings.yaml.example` (hot-reloaded settings)
- Runner: `scripts/run_sidecar.sh` / `scripts/run-shim-sidecar.py` -> `:8081`
- DSH needs only a dummy `BIFROST_API_KEY=sk-bifrost-dummy` (via `apiKeyEnv`);
  real provider keys stay in `~/.config/bifrost/bifrost.env` (0600, `env.VAR` refs)

Quick verify:

```bash
./scripts/run_sidecar.sh &   # :8081
curl -s http://localhost:8081/health | jq .   # tiers 46/84/2
dsh --profile web --dump-config | jq '.[] | select(.id=="llm-pi-ai")'
# In browser: window.__DSH_BOOT__.plugins['llm-pi-ai']
```

## File Layout

```
project-root/
|-- config/
|   |-- providers.yaml          # Provider catalog (base_url, secret env var)
|   |-- quadlet/
|       |-- bifrost-data.volume # Volume unit (bind-mounts data/bifrost)
|       |-- bifrost.container   # Container unit (runs Bifrost)
|-- data/
|   |-- results/                # Ephemeral Reports (gitignored, per-build)
|   |   |-- agnes.yaml
|   |   |-- groq.yaml
|   |   |-- ...
|   |-- bifrost/                # Generated artifacts (gitignored)
|       |-- config.json         # Bifrost file-only config
|       |-- shim_map.json       # Tier -> model_id mapping
|-- scripts/
|   |-- generate-bifrost-config.py  # Generator CLI
|-- ~/.config/bifrost/
    |-- bifrost.env             # Secrets (0600, gitignored, Infisical export)
```

## Troubleshooting

### Service fails to start

```bash
# Check service status
systemctl --user status bifrost

# Check journal for errors
journalctl --user -u bifrost -n 50
```

Common issues:
- Image pull timeout: Increase TimeoutStartSec in bifrost.container
- Missing environment file: Ensure ~/.config/bifrost/bifrost.env exists and is 0600
- Port 8080 in use: Check ss -ltnp | grep 8080
- SELinux denied: Volume uses :Z label; on non-SELinux hosts this is ignored
- Bind mount path wrong: Ensure bifrost-data.volume Source points to absolute path of project's data/bifrost

### Health check fails

```bash
# Test manually
curl -v http://localhost:8080/health

# Check Bifrost logs
journalctl --user -u bifrost -f
```

### Config not updating after regeneration

The Quadlet service must be restarted to pick up new config:

```bash
uv run python scripts/generate-bifrost-config.py
systemctl --user restart bifrost
```

For npx fallback, restart the npx process.

### Drift: file fresh but DB stale (providers/models mismatch, health-filter)

Symptom: `data/bifrost/config.json` (e.g. 20 providers, 314 models) is fresh but `data/bifrost/config.db` is stale (e.g. 16 providers, 304 models) or Bifrost `/api/models` differs. Caused by stale `config.db` + unresolved `env.VAR` health-filter: Bifrost stores keys as `env.VAR` refs and at request time resolves them against the container env; missing vars cause every key to be marked invalid and `/v1/models` health-filters to zero (only Cloudflare models survive if only Cloudflare vars were injected), while `/api/providers` still lists the catalog.

Preflight catches it (read-only, no writes):

```bash
uv run python scripts/generate-bifrost-config.py --check   # compares config.json tier_counts vs live config.db / /api/models counts + mtimes; exits 1 on drift
scripts/setup.sh --check                                    # includes same drift check (file vs db/api + mtimes), fails preflight on drift
```

Fix: restore secrets/env, regenerate, restart (file→DB sync, no DB-only migration):

```bash
# 1. Restore secrets (Infisical or manual .env)
infisical login  # or recreate ~/.config/bifrost/bifrost.env (0600) with all provider keys
# 2. Regenerate file configs from Ephemeral Reports (keeps file→DB sync)
uv run python scripts/generate-bifrost-config.py
# 3. Restart Bifrost so config.db re-ingests the fresh file
systemctl --user restart bifrost
# Verify: curl http://localhost:8080/api/models?limit=1000 | jq .total  (should match shim_map total)
#         curl http://localhost:8080/api/providers | jq length          (should match config.json providers)
```

Health check remains in `bifrost.container` (`/health`); no DB-only migration is used — the file (`config.json` + `shim_map.json`) is the source of truth, `config.db` is derived on restart.

## Security Notes

- config.json contains only env.VAR references, no plaintext secrets
- ~/.config/bifrost/bifrost.env is 0600 and gitignored
- data/bifrost/ is gitignored (entire data/ except model_info_store.json)
- Quadlet runs as user (UID 1000), not root
- Private network access enabled only for local backends (vLLM/Ollama)

## Updating Bifrost Version

Edit config/quadlet/bifrost.container and change the Image tag:

```
Image=docker.io/maximhq/bifrost:v1.2.3
```

Then reload and restart:

```bash
systemctl --user daemon-reload
systemctl --user restart bifrost
```