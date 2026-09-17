#!/usr/bin/env python3
"""Fixed-snapshot build-all harness (issue #235).

Compares baseline vs improved on same snapshot; asserts uncertain+error drops
are due to acquisition (new AA/bench evidence), not threshold loosening.
Reports deltas for weak/none, error, strong/moderate/keep/drop, LLM calls,
web searches, wall duration. Gate fails if thresholds loosened.

Usage:
  # Compare two result dirs (CI: baseline snapshot vs current build)
  .venv/bin/python scripts/measure_build_all.py --baseline-dir data/baseline_results --current-dir data/results

  # Compare two telemetry JSON files (from build_all return)
  .venv/bin/python scripts/measure_build_all.py --baseline-telemetry baseline.json --current-telemetry current.json

  # From previous build snapshot (single snapshot dir with before/after subdirs)
  .venv/bin/python scripts/measure_build_all.py --snapshot-root .scratch/build-snapshot

  # JSON output for CI artifact
  .venv/bin/python scripts/measure_build_all.py --baseline-dir data/baseline_results --current-dir data/results --output .scratch/build-gate.json

Exit code: 0 gate PASS, 1 gate FAIL, 2 usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from llm_discovery.build_gate import (  # noqa: E402
    _collect_records_from_dir,
    check_candidate_ttl,
    check_store_semantics,
    check_thresholds_frozen,
    collect_metrics,
    diff_metrics,
    format_report,
    gate,
)


def _load_telemetry(path: Path) -> dict:
    txt = path.read_text()
    try:
        data = json.loads(txt)
    except Exception:
        data = yaml.safe_load(txt)
    if not isinstance(data, dict):
        return {}
    # handle build_all result wrapper
    if "telemetry" in data:
        data = data["telemetry"]
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, prog="measure_build_all.py")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--baseline-dir", type=Path, help="Baseline results dir (contains *.yaml)")
    g.add_argument("--baseline-telemetry", type=Path, help="Baseline telemetry JSON/YAML")
    ap.add_argument("--current-dir", type=Path, help="Current results dir (contains *.yaml)")
    ap.add_argument("--current-telemetry", type=Path, help="Current telemetry JSON/YAML")
    ap.add_argument("--snapshot-root", type=Path, help="Snapshot root with baseline/ and current/ subdirs")
    ap.add_argument("--output", type=Path, default=None, help="Write gate JSON to path")
    ap.add_argument("--json", action="store_true", help="Print JSON instead of markdown")
    args = ap.parse_args()

    baseline_source: Path | None = None
    current_source: Path | None = None

    if args.snapshot_root:
        baseline_source = args.snapshot_root / "baseline"
        current_source = args.snapshot_root / "current"
        if not baseline_source.exists() or not current_source.exists():
            print(f"snapshot-root requires {baseline_source} and {current_source} to exist", file=sys.stderr)
            sys.exit(2)
    else:
        if args.baseline_dir:
            baseline_source = args.baseline_dir
        elif args.baseline_telemetry:
            baseline_source = args.baseline_telemetry
        if args.current_dir:
            current_source = args.current_dir
        elif args.current_telemetry:
            current_source = args.current_telemetry

    if baseline_source is None or current_source is None:
        # No args: try default data dirs for local smoke (warn)
        default_baseline = ROOT / "data" / "baseline_results"
        default_current = ROOT / "data" / "results"
        if default_baseline.exists() and default_current.exists():
            baseline_source = default_baseline
            current_source = default_current
            print(f"[measure_build_all] using defaults baseline={baseline_source} current={current_source}")
        else:
            ap.print_help()
            print("\nProvide --baseline-dir/--baseline-telemetry and --current-dir/--current-telemetry, or --snapshot-root", file=sys.stderr)
            sys.exit(2)

    # Collect metrics
    def _metrics_from(src: Path) -> dict:
        if src.is_dir():
            return collect_metrics(src)
        return collect_metrics(_load_telemetry(src))

    baseline_metrics = _metrics_from(baseline_source)
    current_metrics = _metrics_from(current_source)

    # Collect records for evidence-backed check when dirs available
    baseline_recs: dict = {}
    current_recs: dict = {}
    try:
        if baseline_source.is_dir():
            baseline_recs = _collect_records_from_dir(baseline_source)
        elif baseline_source.is_file():
            # telemetry file may embed records; try to load sidecar results if present
            pass
        if current_source.is_dir():
            current_recs = _collect_records_from_dir(current_source)
    except Exception:
        pass

    gate_result = gate(baseline_metrics, current_metrics, baseline_recs, current_recs)
    deltas = gate_result["deltas"]

    # Also run threshold/TTL/store checks explicitly for report header
    t = gate_result["thresholds"]
    ttl = gate_result["ttl"]
    store = gate_result["store"]

    if args.json:
        out = {
            "baseline": baseline_metrics,
            "current": current_metrics,
            "deltas": deltas,
            "gate": gate_result,
        }
        print(json.dumps(out, indent=2))
    else:
        print("## Build-all before/after harness (issue #235)")
        print("")
        print(f"Baseline: `{baseline_source}`")
        print(f"Current:  `{current_source}`")
        print("")
        print(format_report(baseline_metrics, current_metrics, gate_result))
        print("")
        print(f"Thresholds frozen: {'PASS' if t['ok'] else 'FAIL'}")
        if not t["ok"]:
            for f in t["failures"]:
                print(f"  - {f}")
        print(f"Candidate TTL ({ttl['ok'] and 'PASS' or 'FAIL'}): 60-90d + 14d store GC")
        print(f"Derived Cache ({store['ok'] and 'PASS' or 'FAIL'}): version {store}")
        if gate_result["evidence_backed"]["unbacked"]:
            print(f"Unbacked promotions: {len(gate_result['evidence_backed']['unbacked'])}")
            for u in gate_result["evidence_backed"]["unbacked"][:10]:
                print(f"  - {u}")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        out = {
            "baseline": baseline_metrics,
            "current": current_metrics,
            "deltas": deltas,
            "gate": gate_result,
            "baseline_source": str(baseline_source),
            "current_source": str(current_source),
        }
        args.output.write_text(json.dumps(out, indent=2))
        print(f"\nWrote {args.output}")

    sys.exit(0 if gate_result["ok"] else 1)


if __name__ == "__main__":
    main()
