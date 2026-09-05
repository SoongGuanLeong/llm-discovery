#!/usr/bin/env python3
"""Generate Bifrost file-only config + shim map from Ephemeral Reports.

Pure generator lives in src/llm_discovery/bifrost/generator.py;
this wrapper handles I/O, env checks, atomic writes, and restart hint.

Usage:
  uv run python scripts/generate-bifrost-config.py
  uv run python scripts/generate-bifrost-config.py --check
  uv run python scripts/generate-bifrost-config.py --output-dir data/bifrost
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure src on path when run as script
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm_discovery.bifrost.generator import generate_bifrost_config, load_keeps_from_results_dir
from llm_discovery.config import load_config


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Bifrost config.json + shim_map.json from data/results/*.yaml (file-only, no container).")
    parser.add_argument("--results-dir", type=Path, default=Path("data/results"), help="Directory with provider YAML Ephemeral Reports")
    parser.add_argument("--config", type=Path, default=Path("config/providers.yaml"), help="Providers catalog YAML")
    parser.add_argument("--output-dir", type=Path, default=Path("data/bifrost"), help="Output directory for config.json + shim_map.json")
    parser.add_argument("--check", action="store_true", help="Dry-run: list providers with/without keys and tier counts, do not write")
    args = parser.parse_args()

    # Load provider catalog
    try:
        app_cfg = load_config(args.config)
        catalog = app_cfg.providers
    except Exception as exc:
        print(f"Failed to load {args.config}: {exc}", file=sys.stderr)
        return 2

    # Load keeps (respect pre-categorized tier, keep-all)
    keeps = load_keeps_from_results_dir(args.results_dir)
    print(f"Loaded {len(keeps)} keeps from {args.results_dir}")

    # Available env vars: real environment
    available = set(os.environ.keys())

    result = generate_bifrost_config(keeps, catalog, available)

    config = result["config"]
    shim_map = result["shim_map"]
    skipped = result["skipped"]
    tier_counts = result["tier_counts"]
    empty_tiers = result["empty_tiers"]

    # Report
    print(f"Tier counts: flash={tier_counts.get('flash',0)} max={tier_counts.get('max',0)} contributor_free={tier_counts.get('contributor_free',0)} (total {sum(tier_counts.values())})")
    if skipped:
        print(f"Skipped providers (missing env): {', '.join(skipped)}")
    else:
        print("No providers skipped (all env keys present)")
    if empty_tiers:
        print(f"Empty tiers (503 signal, no fallback): {', '.join(empty_tiers)}", file=sys.stderr)

    # Provider summary
    providers_emitted = sorted(config.get("providers", {}).keys())
    print(f"Providers emitted: {len(providers_emitted)} -> {', '.join(providers_emitted) if providers_emitted else '(none)'}")

    if args.check:
        # Exit non-zero if any tier would be empty (surfaces omission before restart)
        if empty_tiers:
            print(f"--check: empty tier detected -> exit 1", file=sys.stderr)
            return 1
        print("--check: dry-run ok, no files written")
        return 0

    # Write artifacts atomically
    out = args.output_dir
    _atomic_write_json(out / "config.json", config)
    _atomic_write_json(out / "shim_map.json", shim_map)
    print(f"Wrote {out / 'config.json'} and {out / 'shim_map.json'}")
    # Write empty-tier signal file for shim 503 handling (optional sidecar)
    if empty_tiers:
        print(f"Note: empty tiers {empty_tiers} will return 503 + Retry-After (no fallback). Ensure shim honors shim_map empty list.")
    print("Next: systemctl --user restart bifrost  (or restart npx: npx -y @maximhq/bifrost --app-dir ./data/bifrost)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
