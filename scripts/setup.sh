#!/usr/bin/env bash
# llm-discovery setup - reconciliation via Infisical + Podman/Quadlet
# Spec #126 / Issue #127 - --check is read-only preflight, --help shows flags.
#
# Interface (per #122): single scripts/setup.sh reconciliation command
#   bash canonical, check-only login, parse .env directly, Podman/Quadlet
#   canonical hard-fail, flags --check + --yes only, --secrets=file fallback.
# Secrets (per #123): podman secret type=env by default, file fallback via --secrets=file
# Prerequisites + idempotency (per #124): preflight fail-fast, atomic writes, daemon-reload only on change.
#
# Usage:
#   ./scripts/setup.sh --check          # read-only preflight, no writes
#   ./scripts/setup.sh                  # full run (prompts once if overwriting)
#   ./scripts/setup.sh --yes            # full run, no prompts
#   ./scripts/setup.sh --secrets=file   # explicit file handoff

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

SECRETS_MODE="podman"
FLAG_CHECK=0
FLAG_YES=0

BIFROST_ENV_FILE="$HOME/.config/bifrost/bifrost.env"
QUADLET_SRC_DIR="$REPO_ROOT/config/quadlet"
QUADLET_DST_DIR="$HOME/.config/containers/systemd"
DATA_BIFROST_DIR="$REPO_ROOT/data/bifrost"

for arg in "$@"; do
  case "$arg" in
    --check) FLAG_CHECK=1 ;;
    --yes) FLAG_YES=1 ;;
    --secrets=file) SECRETS_MODE="file" ;;
    --secrets=podman) SECRETS_MODE="podman" ;;
    --secrets=*) echo "Unknown --secrets value: $arg (use file|podman)" >&2; exit 2 ;;
    --help|-h)
      cat <<'HELP'
Usage: scripts/setup.sh [OPTIONS]

Single reconciliation command from fresh clone to running Bifrost gateway.
Parses .env directly (no source), verifies Infisical login check-only,
prepares secrets via Podman secret (default) or file fallback, generates
Bifrost config and Quadlet units. Idempotent; stops before daemon-reload.

Options:
  --check              Dry-run preflight: print [1/6]..[6/6] OK/WARN/FAIL and
                       PASS/FAIL table, write nothing, exit 0 only if all PASS
  --yes                Skip overwrite prompt for existing secrets
  --secrets=file|podman  Secret handoff mode (default: podman). Podman uses
                       podman secret type=env; file writes 0600 atomic file.
  --help, -h           Show this help and exit 0

Examples:
  scripts/setup.sh --check
  scripts/setup.sh --yes
  scripts/setup.sh --secrets=file --yes
HELP
      echo ""
      echo "Flags: --check (dry-run), --yes (no prompts), --secrets=file|podman (default: podman), --help"
      exit 0
      ;;
    *) echo "Unknown flag: $arg (try --help)" >&2; exit 2 ;;
  esac
done

log_step()  { echo "[$1] $2"; }
log_ok()    { echo "      -> OK: $1"; }
log_warn()  { echo "      -> WARN: $1" >&2; }
log_fail()  { echo "      -> FAIL: $1" >&2; }
die()       { echo "error: $1" >&2; exit 1; }

parse_dotenv_var() {
  local key="$1" file=".env" val=""
  if [[ -f "$file" ]]; then
    val=$(grep -E "^[[:space:]]*$key[[:space:]]*=" "$file" | tail -1 | sed -E "s/^[[:space:]]*$key[[:space:]]*=[[:space:]]*//" | sed -E 's/[[:space:]]*#.*$//' | sed -E 's/^\"(.*)\"$/\\1/' | sed -E "s/^'(.*)'$/\\1/" | xargs 2>/dev/null || true)
  fi
  if [[ -n "${!key:-}" ]]; then
    val="${!key}"
  fi
  printf '%s' "$val"
}

is_uuid() { [[ "$1" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]; }

TMP_FILES=()
cleanup() {
  for f in "${TMP_FILES[@]:-}"; do
    [[ -f "$f" ]] && rm -f "$f" || true
  done
  return 0
}
trap cleanup EXIT

log_step "1/6" "Parsing .env for project IDs (no source, per #121)"
if [[ ! -f .env ]]; then
  log_fail ".env not found in $REPO_ROOT (see .env.example)"
  MISSING_ENV=1
else
  log_ok ".env found"
  MISSING_ENV=0
fi
LLM_SHARED_PROJECT_ID=$(parse_dotenv_var LLM_SHARED_PROJECT_ID)
LLM_DISCOVERY_PROJECT_ID=$(parse_dotenv_var LLM_DISCOVERY_PROJECT_ID)

check_env_id() {
  local name="$1" val="$2"
  if [[ -z "$val" ]]; then
    log_fail "Missing $name in .env (see .env.example). Set it or export it before running setup."
    return 1
  fi
  if ! is_uuid "$val"; then
    log_warn "$name does not look like UUID: $val (continuing)"
  else
    log_ok "$name=$val"
  fi
  return 0
}

ENV_OK=0
check_env_id LLM_SHARED_PROJECT_ID "$LLM_SHARED_PROJECT_ID" || ENV_OK=1
check_env_id LLM_DISCOVERY_PROJECT_ID "$LLM_DISCOVERY_PROJECT_ID" || ENV_OK=1

log_step "2/6" "Checking Infisical CLI"
if ! command -v infisical >/dev/null 2>&1; then
  log_fail "infisical CLI not found. Install: https://infisical.com/docs/cli/overview"
  INFISICAL_BIN=0
else
  log_ok "infisical $(infisical --version 2>&1 | head -1)"
  INFISICAL_BIN=1
fi

log_step "3/6" "Checking Infisical auth (check-only, no auto-login)"
INFISICAL_AUTH=0
if [[ "$INFISICAL_BIN" -eq 1 && "$ENV_OK" -eq 0 ]]; then
  if infisical export --projectId "$LLM_SHARED_PROJECT_ID" --env dev --format json --include-imports=false --silent >/dev/null 2>&1; then
    log_ok "Infisical auth valid"
    INFISICAL_AUTH=1
  else
    log_fail "Not logged into Infisical. Run: infisical login (then re-run setup)."
    echo "         hint: infisical login stores token in ~/.infisical / OS keyring" >&2
  fi
else
  log_fail "Skipped auth check (missing CLI or project IDs)"
fi

log_step "4/6" "Checking Python 3.12+"
PYTHON_BIN=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver=$("$cand" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    if [[ "$major" -gt 3 ]] || [[ "$major" -eq 3 && "$minor" -ge 12 ]]; then
      PYTHON_BIN="$cand"
      log_ok "$cand $ver (>=3.12)"
      break
    else
      log_warn "$cand $ver < 3.12 (requires-python >=3.12 per pyproject.toml)"
    fi
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then
  log_fail "Python 3.12+ required. Install via https://www.python.org or uv."
fi
if command -v uv >/dev/null 2>&1; then
  log_ok "uv $(uv --version 2>&1 | head -1) (preferred)"
else
  log_warn "uv not found - will fall back to $PYTHON_BIN"
fi

log_step "5/6" "Checking Podman/Quadlet (canonical path)"
PODMAN_OK=0
if ! command -v podman >/dev/null 2>&1; then
  log_fail "Podman 4.0+ not found. Install Podman + enable systemd --user, or run npx fallback manually:"
  echo "         npx -y @maximhq/bifrost --app-dir ./data/bifrost (see docs/bifrost-deployment.md)" >&2
else
  podman_ver=$(podman --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1 || echo "?")
  log_ok "podman $podman_ver"
  PODMAN_OK=1
  if ! systemctl --user --version >/dev/null 2>&1; then
    log_warn "systemd --user not available (Quadlet needs it)"
    PODMAN_OK=0
  else
    log_ok "systemd --user available"
  fi
fi
PODMAN_SECRET_ENV=0
if [[ "$PODMAN_OK" -eq 1 ]]; then
  if podman secret --help 2>&1 | grep -q "secret"; then
    if podman secret create --help 2>&1 | grep -q "env"; then
      PODMAN_SECRET_ENV=1
      log_ok "podman secret type=env supported"
    else
      log_warn "podman secret exists but type=env not supported - upgrade or use --secrets=file"
    fi
  else
    log_warn "podman secret subcommand not found - will use file handoff"
  fi
fi
if [[ "$SECRETS_MODE" == "podman" && "$PODMAN_SECRET_ENV" -eq 0 ]]; then
  log_warn "--secrets=podman requested but not supported; hint: use --secrets=file or upgrade Podman"
fi

log_step "6/6" "Checking existing Bifrost artifacts"
if [[ -f "$BIFROST_ENV_FILE" ]]; then
  perms=$(stat -c %a "$BIFROST_ENV_FILE" 2>/dev/null || stat -f %A "$BIFROST_ENV_FILE" 2>/dev/null || echo "?")
  log_ok "$BIFROST_ENV_FILE exists (perms $perms) - will reconcile atomically"
  if [[ "$perms" != "600" && "$perms" != "0600" ]]; then
    log_warn "$BIFROST_ENV_FILE perms $perms != 600 - will fix on write"
  fi
else
  echo "      -> not found: $BIFROST_ENV_FILE (fresh install)"
fi
if podman secret exists bifrost-env >/dev/null 2>&1; then
  log_ok "podman secret bifrost-env exists - will replace atomically"
else
  echo "      -> not found: podman secret bifrost-env"
fi
if [[ -f "$DATA_BIFROST_DIR/config.json" ]]; then
  log_ok "$DATA_BIFROST_DIR/config.json exists"
else
  echo "      -> not found: $DATA_BIFROST_DIR/config.json (generator will create)"
fi
if command -v loginctl >/dev/null 2>&1; then
  linger=$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || echo "unknown")
  if [[ "$linger" == "yes" ]]; then
    log_ok "linger enabled"
  elif [[ "$linger" == "no" ]]; then
    log_warn "linger not enabled - Bifrost will not auto-start after logout. Run: loginctl enable-linger $USER"
  else
    echo "      -> linger: $linger"
  fi
fi

if [[ "$FLAG_CHECK" -eq 1 ]]; then
  echo ""
  echo "--check summary (read-only, no writes):"
  printf "  %-28s %s\\n" ".env parse" "$([[ "$ENV_OK" -eq 0 ]] && echo PASS || echo FAIL)"
  printf "  %-28s %s\\n" "infisical CLI" "$([[ "$INFISICAL_BIN" -eq 1 ]] && echo PASS || echo FAIL)"
  printf "  %-28s %s\\n" "infisical auth" "$([[ "$INFISICAL_AUTH" -eq 1 ]] && echo PASS || echo FAIL)"
  printf "  %-28s %s\\n" "python 3.12+" "$([[ -n "$PYTHON_BIN" ]] && echo PASS || echo FAIL)"
  printf "  %-28s %s\\n" "podman/quadlet" "$([[ "$PODMAN_OK" -eq 1 ]] && echo PASS || echo FAIL)"
  printf "  %-28s %s\\n" "secrets mode" "$SECRETS_MODE ($([[ "$SECRETS_MODE" == "podman" ]] && [[ "$PODMAN_SECRET_ENV" -eq 1 ]] && echo supported || [[ "$SECRETS_MODE" == "file" ]] && echo file || echo unsupported))"
  echo ""
  if [[ "$ENV_OK" -eq 0 && "$INFISICAL_BIN" -eq 1 && "$INFISICAL_AUTH" -eq 1 && -n "$PYTHON_BIN" && "$PODMAN_OK" -eq 1 ]]; then
    echo "All checks PASS - ready for: ./scripts/setup.sh --yes"
    exit 0
  else
    echo "Some checks FAIL - fix above, then re-run --check"
    exit 1
  fi
fi

if [[ "$ENV_OK" -ne 0 ]]; then die "Missing project IDs - fix .env then re-run."; fi
if [[ "$INFISICAL_BIN" -ne 1 ]]; then die "infisical CLI missing - install then re-run."; fi
if [[ "$INFISICAL_AUTH" -ne 1 ]]; then die "Not logged into Infisical - run: infisical login"; fi
if [[ -z "$PYTHON_BIN" ]]; then die "Python 3.12+ missing."; fi
if [[ "$PODMAN_OK" -ne 1 ]]; then die "Podman/Quadlet not available - install or use npx fallback manually (see docs/bifrost-deployment.md). No silent fallback."; fi

need_confirm=0
if [[ -f "$BIFROST_ENV_FILE" ]] || podman secret exists bifrost-env >/dev/null 2>&1; then
  need_confirm=1
fi
if [[ "$need_confirm" -eq 1 && "$FLAG_YES" -eq 0 ]]; then
  echo ""
  echo "Will overwrite existing Bifrost secrets ($BIFROST_ENV_FILE / podman secret bifrost-env)."
  read -r -p "Continue? [y/N] " ans
  case "$ans" in [yY]|[yY][eE][sS]) ;; *) echo "Aborted."; exit 1 ;;
  esac
fi

log_step "7/9" "Exporting secrets from Infisical (--format dotenv, --silent)"
TMP_ENV=$(mktemp)
TMP_FILES+=("$TMP_ENV")
chmod 600 "$TMP_ENV"
if ! infisical export --projectId "$LLM_SHARED_PROJECT_ID" --env dev --format dotenv --include-imports=false --silent > "$TMP_ENV" 2>&1; then
  cat "$TMP_ENV" >&2
  die "infisical export failed (check auth / projectId)"
fi
if [[ ! -s "$TMP_ENV" ]]; then
  die "infisical export produced empty file - check project has secrets"
fi
log_ok "exported $(wc -l < "$TMP_ENV" | tr -d ' ') vars to temp (0600)"

if [[ "$SECRETS_MODE" == "podman" ]]; then
  log_step "8/9" "Handoff via podman secret (type=env, default per #123)"
  if [[ "$PODMAN_SECRET_ENV" -eq 0 ]]; then
    die "podman secret type=env not supported. Upgrade Podman or re-run with --secrets=file"
  fi
  if podman secret exists bifrost-env >/dev/null 2>&1; then
    podman secret rm bifrost-env >/dev/null
    log_ok "removed old podman secret bifrost-env"
  fi
  if podman secret create --env-file "$TMP_ENV" bifrost-env >/dev/null 2>&1; then
    log_ok "podman secret bifrost-env created (type=env)"
  else
    if podman secret create bifrost-env "$TMP_ENV" >/dev/null 2>&1; then
      log_ok "podman secret bifrost-env created (file form)"
    else
      die "podman secret create failed - try --secrets=file"
    fi
  fi
  echo "      hint: Quadlet bifrost.container should use Secret=bifrost-env,type=env (not EnvironmentFile)"
else
  log_step "8/9" "Handoff via file $BIFROST_ENV_FILE (--secrets=file)"
  # workspace-write sandbox: HOME/.config may be read-only; fallback to repo-local demo file
  EFFECTIVE_ENV_FILE="$BIFROST_ENV_FILE"
  if ! mkdir -p "$(dirname "$BIFROST_ENV_FILE")" 2>/dev/null; then
    log_warn "$(dirname "$BIFROST_ENV_FILE") not writable (sandbox or RO FS) - using $REPO_ROOT/.tmp/bifrost.env (demo fallback)"
    EFFECTIVE_ENV_FILE="$REPO_ROOT/.tmp/bifrost.env"
    mkdir -p "$(dirname "$EFFECTIVE_ENV_FILE")"
  fi
  TMP_DST=$(mktemp "$(dirname "$EFFECTIVE_ENV_FILE")/.bifrost.env.XXXXXX")
  TMP_FILES+=("$TMP_DST")
  cat "$TMP_ENV" > "$TMP_DST"
  chmod 600 "$TMP_DST"
  mv -f "$TMP_DST" "$EFFECTIVE_ENV_FILE"
  TMP_FILES=("${TMP_FILES[@]/$TMP_DST}")
  log_ok "wrote $EFFECTIVE_ENV_FILE (0600, atomic)"
fi

log_step "9/9" "Generating Bifrost config (data/bifrost/config.json)"
GEN_OK=0
# uv cache may be RO outside workspace (workspace-write sandbox); use repo cache
if command -v uv >/dev/null 2>&1; then
  if UV_CACHE_DIR="$REPO_ROOT/.uv-cache" uv run python scripts/generate-bifrost-config.py 2>&1; then
    GEN_OK=1
  else
    log_warn "uv run generate-bifrost-config.py failed; trying python fallback"
  fi
fi
if [[ "$GEN_OK" -eq 0 ]]; then
  # Prefer venv python which has httpx installed; fall back to system python
  VENV_PY="$REPO_ROOT/.venv/bin/python"
  if [[ -x "$VENV_PY" ]]; then
    if "$VENV_PY" scripts/generate-bifrost-config.py 2>&1; then
      GEN_OK=1
    else
      log_fail "generate-bifrost-config.py failed - check data/results/*.yaml"
    fi
  elif "$PYTHON_BIN" scripts/generate-bifrost-config.py 2>&1; then
    GEN_OK=1
  else
    log_fail "generate-bifrost-config.py failed - check data/results/*.yaml"
  fi
fi
if [[ "$GEN_OK" -eq 1 ]]; then
  log_ok "Bifrost config generated in $DATA_BIFROST_DIR"
fi

echo ""
echo "Quadlet prepare (dry, no daemon-reload yet - prototype boundary per #122):"
if ! mkdir -p "$QUADLET_DST_DIR" 2>/dev/null || ! touch "$QUADLET_DST_DIR/.writetest" 2>/dev/null; then
  log_warn "$QUADLET_DST_DIR not writable (sandbox RO) - using $REPO_ROOT/.tmp/quadlet (demo fallback)"
  QUADLET_DST_DIR="$REPO_ROOT/.tmp/quadlet"
  mkdir -p "$QUADLET_DST_DIR"
else
  rm -f "$QUADLET_DST_DIR/.writetest"
fi
for unit in bifrost.container bifrost-data.volume; do
  src="$QUADLET_SRC_DIR/$unit"
  dst="$QUADLET_DST_DIR/$unit"
  if [[ ! -f "$src" ]]; then
    log_warn "source $src missing - skipping"
    continue
  fi
  tmp_unit=$(mktemp)
  TMP_FILES+=("$tmp_unit")
  if [[ "$unit" == "bifrost-data.volume" ]]; then
    sed "s|Source=.*|Source=$DATA_BIFROST_DIR|" "$src" > "$tmp_unit"
  else
    if [[ "$SECRETS_MODE" == "podman" ]]; then
      sed -E "s|^EnvironmentFile=.*|Secret=bifrost-env,type=env|" "$src" > "$tmp_unit"
    else
      cat "$src" > "$tmp_unit"
    fi
  fi
  if [[ -f "$dst" ]] && diff -q "$tmp_unit" "$dst" >/dev/null 2>&1; then
    echo "  $unit: up-to-date (no copy)"
  else
    tmp_dst=$(mktemp "$QUADLET_DST_DIR/.$unit.XXXXXX")
    TMP_FILES+=("$tmp_dst")
    cat "$tmp_unit" > "$tmp_dst"
    mv -f "$tmp_dst" "$dst"
    TMP_FILES=("${TMP_FILES[@]/$tmp_dst}")
    echo "  $unit: installed -> $dst (changed)"
  fi
done
echo ""
echo "Next (manual, prototype stops before daemon-reload per #122):"
echo "  systemctl --user daemon-reload"
echo "  systemctl --user enable --now bifrost   # or: systemctl --user restart bifrost"
echo "  curl http://localhost:8080/health"
echo ""
echo "npx fallback (if Podman unavailable, per docs/bifrost-deployment.md):"
echo "  npx -y @maximhq/bifrost --app-dir ./data/bifrost"
echo ""
echo "Done. Re-run any time: ./scripts/setup.sh [--yes] [--secrets=file|podman]"
