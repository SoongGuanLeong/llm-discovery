#!/usr/bin/env bash
set -euo pipefail
# Bifrost shim sidecar runner — strict-group serving
# Usage: ./scripts/run_sidecar.sh  -> :8081 -> :8080
# Env: SHIM_PORT=8081 BIFROST_URL=http://localhost:8080 SHIM_MAP_PATH=data/bifrost/shim_map.json
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"
PORT=${SHIM_PORT:-8081}
BIFROST_URL=${BIFROST_URL:-http://localhost:8080}
SHIM_MAP_PATH=${SHIM_MAP_PATH:-data/bifrost/shim_map.json}
export SHIM_MAP_PATH BIFROST_URL
echo "[shim] $SHIM_MAP_PATH -> $BIFROST_URL on :$PORT" >&2
if [[ ! -f "$SHIM_MAP_PATH" ]]; then
  echo "error: shim_map not found: $SHIM_MAP_PATH" >&2
  exit 1
fi
if command -v uv >/dev/null 2>&1; then
  exec uv run python -m llm_discovery.bifrost.sidecar
else
  exec python3 -m llm_discovery.bifrost.sidecar
fi
