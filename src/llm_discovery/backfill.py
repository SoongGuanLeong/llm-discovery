"""Backfill seeding from data/results/*.yaml — slim v2.

Seeds slim store {benchmarks, pricing, _meta} by deduping normalized keys.
No gate on legacy fields (store slim already filtered); all keep[] entries merged.
Pricing aggregated via aggregate_pricing, benchmarks union-max via merge_records.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from .gate import is_accurate_enough
from .model_info_store import (
    ModelInfoRecord,
    ModelInfoStore,
    PricingSnapshot,
    aggregate_pricing,
    merge_records,
    normalize_store_key,
)


def _parse_results_file(path: Path) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    try:
        data = yaml.safe_load(path.read_text())
    except Exception:
        return None, None, []
    if not isinstance(data, dict):
        return None, None, []
    if "keep" in data:
        provider = data.get("provider")
        evaluated_at = data.get("evaluated_at")
        keep = data.get("keep") or []
        if not isinstance(keep, list):
            keep = []
        return provider, evaluated_at, keep
    if data.get("decision") == "keep" and "model_id" in data:
        provider = data.get("provider")
        evaluated_at = data.get("evaluated_at")
        return provider, evaluated_at, [data]
    return data.get("provider"), data.get("evaluated_at"), []


def backfill(
    results_dir: str | Path = "data/results",
    store_path: str | Path = "data/model_info_store.json",
) -> dict[str, Any]:
    results_dir = Path(results_dir)
    store_path = Path(store_path)
    store = ModelInfoStore(store_path)
    yaml_files = sorted(results_dir.glob("*.yaml")) if results_dir.exists() else []
    files_processed = len(yaml_files)
    pricing_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    record_groups: dict[str, list[ModelInfoRecord]] = defaultdict(list)
    total_keep = 0
    weak_skipped = 0
    all_evaluated_at: list[str] = []
    stale_skipped = 0
    gate_skipped = 0
    for yf in yaml_files:
        provider, evaluated_at, keep = _parse_results_file(yf)
        if evaluated_at:
            all_evaluated_at.append(str(evaluated_at))
        for rec in keep:
            total_keep += 1
            model_id = rec.get("model_id") or rec.get("provider_model_id") or ""
            # Accurate-Enough Gate: only Keeps passing all 7 floors become Keepers
            ok, reason = is_accurate_enough(rec)
            if not ok:
                gate_skipped += 1
                weak_skipped += 1
                continue
            key = normalize_store_key(str(model_id))
            if not key:
                weak_skipped += 1
                continue
            try:
                mir = ModelInfoRecord.from_provider_record(rec, provider=provider, evaluated_at=evaluated_at)
            except Exception:
                mir = ModelInfoRecord.from_provider_record({"benchmarks": rec.get("benchmarks"), "pricing": rec.get("pricing")}, provider=provider, evaluated_at=evaluated_at)
            record_groups[key].append(mir)
            pricing = rec.get("pricing")
            if pricing and isinstance(pricing, dict):
                obs = dict(pricing)
                obs["provider"] = provider
                pricing_groups[key].append(obs)
            elif pricing is not None:
                pricing_groups[key].append({"blended": pricing, "provider": provider})
            else:
                if mir.pricing and mir.pricing.blended is not None:
                    pricing_groups[key].append({"blended": mir.pricing.blended, "input": mir.pricing.input, "output": mir.pricing.output, "provider": provider})
    unique_models = len(record_groups)
    merged_conflicts = sum(1 for v in record_groups.values() if len(v) > 1)
    pricing_avgs = 0
    pricing_outliers = 0
    for key, recs in record_groups.items():
        merged: ModelInfoRecord | None = None
        for r in recs:
            merged = merge_records(merged, r)
        assert merged is not None
        obs_list = pricing_groups.get(key, [])
        if obs_list:
            agg = aggregate_pricing(obs_list)
            if agg is not None:
                merged.pricing = PricingSnapshot(
                    blended=agg.get("blended"),
                    input=agg.get("input"),
                    output=agg.get("output"),
                    per_provider_overrides=dict(agg.get("per_provider_overrides", {})),
                )
                if len(obs_list) >= 2:
                    pricing_avgs += 1
                pricing_outliers += len(agg.get("per_provider_overrides", {}))
        store.put(key, merged)
    stats = {
        "files_processed": files_processed,
        "total_keep_records": total_keep,
        "unique_models": unique_models,
        "merged_conflicts": merged_conflicts,
        "pricing_avgs": pricing_avgs,
        "pricing_outliers": pricing_outliers,
        "weak_skipped": weak_skipped,
        "gate_skipped": gate_skipped,
        "stale_skipped": stale_skipped,
        "evaluated_at_range": [min(all_evaluated_at), max(all_evaluated_at)] if all_evaluated_at else [],
        "store_path": str(store_path),
        "store_size": store.size(),
    }
    stats["outliers"] = pricing_outliers
    return stats


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Backfill model_info_store from data/results/*.yaml")
    parser.add_argument("--results-dir", default="data/results", help="Results yaml directory")
    parser.add_argument("--store-path", default="data/model_info_store.json", help="Store JSON path")
    args = parser.parse_args()
    stats = backfill(results_dir=args.results_dir, store_path=args.store_path)
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
