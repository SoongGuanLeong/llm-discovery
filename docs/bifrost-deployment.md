# Bifrost Gateway Deployment

This document describes how to deploy the Bifrost AI Gateway locally using Podman Quadlet
(user systemd services) with an npx fallback for hosts where Quadlet is unavailable.

## Quick Start (Podman Quadlet)

### Prerequisites

- Podman 4.0+ with Quadlet support (podman --version)
- User linger enabled: loginctl enable-linger $USER
- Infisical CLI (for secrets) or manual environment file

### 1. Export Secrets to Environment File

Bifrost requires API keys for each provider. These are supplied via a single
environment file consumed by the Quadlet service.

**Option A: Infisical (recommended for team/shared secrets)**

```bash
# Login to Infisical (one-time)
infisical login

# Export dev environment secrets to the expected location
mkdir -p ~/.config/bifrost
infisical export --projectId $LLM_SHARED_PROJECT_ID --env dev > ~/.config/bifrost/bifrost.env
chmod 600 ~/.config/bifrost/bifrost.env
```

**Option B: Manual .env file**

Create ~/.config/bifrost/bifrost.env with your provider API keys:

```bash
mkdir -p ~/.config/bifrost
cat > ~/.config/bifrost/bifrost.env <<'EOF'
# Provider API keys (referenced as env.VAR in config.json)
GROQ_API_KEY=your-groq-key
CEREBRAS_API_KEY=your-cerebras-key
OPENAI_API_KEY=your-openai-key
ANTHROPIC_API_KEY=your-anthropic-key
# ... add other provider keys as needed

# Cloudflare requires BOTH vars to be present
CLOUDFLARE_API_KEY=your-cf-key
CLOUDFLARE_ACCOUNT_ID=your-cf-account-id

# Bifrost encryption key (optional for file-only mode)
# BIFROST_ENCRYPTION_KEY=your-encryption-key
EOF
chmod 600 ~/.config/bifrost/bifrost.env
```

### 2. Generate Bifrost Config

Run the generator after build_all (or manually) to create config.json and shim_map.json
in data/bifrost/:

```bash
# From project root
uv run python scripts/generate-bifrost-config.py
```

This reads data/results/*.yaml (Ephemeral Reports) and config/providers.yaml,
checks which provider keys are present in ~/.config/bifrost/bifrost.env,
and emits a file-only Bifrost config.

**Dry-run check** (lists providers with/without keys, exits non-zero if any tier empty):

```bash
uv run python scripts/generate-bifrost-config.py --check
```

### 3. Install and Start Quadlet Service

Copy Quadlet files to user systemd directory and customize the bind-mount path:

```bash
mkdir -p ~/.config/containers/systemd/
cp config/quadlet/bifrost-data.volume ~/.config/containers/systemd/
cp config/quadlet/bifrost.container ~/.config/containers/systemd/

# IMPORTANT: Edit the volume file to point to your project's data/bifrost directory
# Use absolute path - systemd user services don't have a project-relative working directory
sed -i "s|Source=/home/soongguanleong/projects/llm-discovery/data/bifrost|Source=$(pwd)/data/bifrost|" ~/.config/containers/systemd/bifrost-data.volume
```

Reload systemd and start the service:

```bash
systemctl --user daemon-reload
systemctl --user start bifrost
```

Enable auto-start on login (requires linger):

```bash
systemctl --user enable bifrost
loginctl enable-linger $USER
```

### 4. Verify Health

```bash
# Health endpoint (should return 200 OK with JSON)
curl http://localhost:8080/health

# List available models (shows all provider/model entries)
curl http://localhost:8080/v1/models
```

### 5. View Logs

```bash
# Follow logs
journalctl --user -u bifrost -f

# Show last 100 lines
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
- model: "max" - routes to any max-tier keep (77 variants)
- model: "contributor_free" - routes to contributor-marked keeps only (3 variants)

**Strict isolation:** No automatic cross-tier fallback. Empty tier returns
503 Service Unavailable with Retry-After and tier_unavailable error.

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
