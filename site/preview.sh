#!/usr/bin/env sh
# Local preview for site/. Serves the directory over plain HTTP so the page can
# be opened in a browser before deploy. No build step, no dependencies.
#
#   ./site/preview.sh          # http://localhost:8000/
#   ./site/preview.sh 8080     # pick a port
set -e
cd "$(dirname "$0")"
port="${1:-8000}"
echo "Prototype preview: http://localhost:${port}/  (Ctrl-C to stop)"
exec python3 -m http.server "$port"
