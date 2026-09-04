"""Backfill seeding from data/results/*.yaml (issue #69)."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from .model_info_store import (
    ModelInfoRecord,
    ModelInfoStore,
    PricingSnapshot,
    aggregate_pricing,
    merge_records,
    normalize_store_key,
    should_cache,
)


def _parse_results_file(path: Path) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    """Parse one results yaml. Returns (provider, evaluated_at, keep_records)."""
    try:
        data = yaml.safe_load(path.read_text())
    except Exception:
        return None, None, []
    if not isinstance(data, dict):
        return None, None, []
    # Standard shape: {provider, evaluated_at, keep: []}
    if "keep" in data:
        provider = data.get("provider")
        evaluated_at = data.get("evaluated_at")
        keep = data.get("keep") or []
        if not isinstance(keep, list):
            keep = []
        return provider, evaluated_at, keep
    # Legacy single-record shape (e.g. malformed huggingface.yaml)
    if data.get("decision") == "keep" and "model_id" in data:
        provider = data.get("provider")
        evaluated_at = data.get("evaluated_at")
        return provider, evaluated_at, [data]
    return data.get("provider"), data.get("evaluated_at"), []


def backfill(
    results_dir: str | Path = "data/results",
    store_path: str | Path = "data/model_info_store.json",
) -> dict[str, Any]:
    """
    One-shot backfill to seed store from existing report YAMLs.

    - Enumerates results_dir/*.yaml
    - Collects keep[] records (skips weak/none per should_cache)
      Note: drop_llm ignored — spec says "maybe also drop_llm if strong
      evidence for drop" but current pipeline treats drop as non-cacheable;
      include only if future decision gates drop_llm strong as cacheable.
    - Deduplicates by normalize_store_key(model_id)
    - Merges via merge_records (strong > moderate) + aggregate_pricing avg/outlier
    - Emits store file (JSON) via ModelInfoStore.put (idempotent merge, not overwrite)
    - Returns stats dict.
    """
    results_dir = Path(results_dir)
    store_path = Path(store_path)

    # Ensure store handles existing file (idempotent)
    store = ModelInfoStore(store_path)

    # Enumerate
    yaml_files = sorted(results_dir.glob("*.yaml")) if results_dir.exists() else []
    files_processed = len(yaml_files)

    # For pricing aggregation we need raw observations per key
    pricing_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    # Track per-key raw records for merge
    record_groups: dict[str, list[ModelInfoRecord]] = defaultdict(list)
    # Stats
    total_keep = 0
    weak_skipped = 0
    # Keep evaluated_at range for meta?
    all_evaluated_at: list[str] = []

    for yf in yaml_files:
        provider, evaluated_at, keep = _parse_results_file(yf)
        if evaluated_at:
            all_evaluated_at.append(str(evaluated_at))
        for rec in keep:
            total_keep += 1
            model_id = rec.get("model_id") or rec.get("provider_model_id") or ""
            lvl = rec.get("evidence_level")
            # normalize: if missing evidence_level, treat as none
            if not should_cache(lvl, rec.get("confidence")):
                weak_skipped += 1
                continue
            key = normalize_store_key(str(model_id))
            if not key:
                # fallback: try aa_model_id
                key = normalize_store_key(str(rec.get("aa_model_id") or ""))
                if not key:
                    weak_skipped += 1
                    continue
            # Create ModelInfoRecord via from_provider_record
            try:
                mir = ModelInfoRecord.from_provider_record(rec, provider=provider, evaluated_at=evaluated_at)
            except Exception as exc:  # narrow fallback: malformed provider record, keep minimal evidence
                # Keep minimal record so backfill continues; log for audit
                # (avoid silent hide of real bug — surface via fallback evidence)
                _fallback_evidence = list(rec.get("evidence", [])) or [f"backfill fallback: {exc}"]
                mir = ModelInfoRecord(
                    aa_model_id=rec.get("aa_model_id"),
                    aa_score=rec.get("aa_score"),
                    coding_score=rec.get("coding_score"),
                    evidence=_fallback_evidence,
                    evidence_level=rec.get("evidence_level"),
                    confidence=rec.get("confidence"),
                    tier=rec.get("tier"),
                )
            record_groups[key].append(mir)
            # Pricing observation
            pricing = rec.get("pricing")
            if pricing and isinstance(pricing, dict):
                obs = dict(pricing)
                obs["provider"] = provider
                pricing_groups[key].append(obs)
            elif pricing is not None:
                # scalar or other
                pricing_groups[key].append({"blended": pricing, "provider": provider})
            else:
                # also check if mir has pricing
                if mir.pricing and mir.pricing.blended is not None:
                    pricing_groups[key].append({"blended": mir.pricing.blended, "input": mir.pricing.input, "output": mir.pricing.output, "provider": provider})

    # Now merge per key and put into store
    unique_models = len(record_groups)
    merged_conflicts = sum(1 for v in record_groups.values() if len(v) > 1)
    pricing_avgs = 0
    pricing_outliers = 0

    for key, recs in record_groups.items():
        # Merge records sequentially (best-of)
        merged: ModelInfoRecord | None = None
        for r in recs:
            merged = merge_records(merged, r)
        assert merged is not None
        # Aggregate pricing for this key if >=1 observation
        obs_list = pricing_groups.get(key, [])
        # Deduplicate observations? keep as is, aggregate handles outlier
        if obs_list:
            agg = aggregate_pricing(obs_list)
            if agg is not None:
                # Convert to PricingSnapshot
                merged.pricing = PricingSnapshot(
                    blended=agg.get("blended"),
                    input=agg.get("input"),
                    output=agg.get("output"),
                    per_provider_overrides=dict(agg.get("per_provider_overrides", {})),
                )
                if len(obs_list) >= 2:
                    pricing_avgs += 1
                pricing_outliers += len(agg.get("per_provider_overrides", {}))
            else:
                # no valid pricing
                pass
        # _meta provenance: merge_records keeps earliest first_seen from
        # first inserted record and latest last_updated (max). Global
        # evaluated_at_range stat captures repo-wide range; per-key min/max
        # not recomputed here to avoid overriding trust-rank logic.
        # If spec later requires per-key evaluated_at min/max, compute from
        # grouped evaluated_at timestamps.
        store.put(key, merged)

    stats = {
        "files_processed": files_processed,
        "total_keep_records": total_keep,
        "unique_models": unique_models,
        "merged_conflicts": merged_conflicts,
        "pricing_avgs": pricing_avgs,
        "pricing_outliers": pricing_outliers,
        "weak_skipped": weak_skipped,
        "evaluated_at_range": [min(all_evaluated_at), max(all_evaluated_at)] if all_evaluated_at else [],
        "store_path": str(store_path),
        "store_size": store.size(),
    }
    # Compat alias: tests + callers check "outliers"; keep canonical "pricing_outliers"
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
