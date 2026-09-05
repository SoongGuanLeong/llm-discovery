#!/usr/bin/env python3
"""Run shim alias sidecar at :8081 -> proxy to Bifrost :8080."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llm_discovery.bifrost.shim import load_shim_map
from llm_discovery.bifrost.sidecar import create_app
import uvicorn

def main():
    shim_path = os.environ.get("SHIM_MAP_PATH", "data/bifrost/shim_map.json")
    shim_map = load_shim_map(shim_path)
    # Fallback try project root
    if all(len(v)==0 for v in shim_map.values()):
        alt = Path(__file__).resolve().parents[1] / "data" / "bifrost" / "shim_map.json"
        if alt.is_file():
            shim_map = load_shim_map(str(alt))
    bifrost_url = os.environ.get("BIFROST_URL", "http://localhost:8080")
    host = os.environ.get("SHIM_HOST", "0.0.0.0")
    port = int(os.environ.get("SHIM_PORT", "8081"))
    print(f"Shim sidecar: {host}:{port} -> {bifrost_url} tiers { {k: len(v) for k,v in shim_map.items()} }")
    app = create_app(shim_map, bifrost_url=bifrost_url)
    uvicorn.run(app, host=host, port=port)

if __name__ == "__main__":
    main()
