#!/usr/bin/env python3
"""
One-shot migration: v1 bloated store -> slim v2.

- Reads data/model_info_store.json (or --store-path)
- Purges non-Keepers via is_accurate_enough gate (moderate/weak, cs_null, missing pricing not free, UUID, hallucinated)
- Projects remaining to slim shape {benchmarks, pricing, _meta} with _meta={first_seen,last_updated,version:2}
- Atomic tmp+rename, .bak backup, bumps STORE_FILE_VERSION to 2
- Idempotent: re-running on v2 slim keeps same result
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

from llm_discovery.model_info_store import STORE_FILE_VERSION, is_accurate_enough, normalize_store_key

def _is_slim_record(d: dict) -> bool:
    if not isinstance(d, dict):
        return False
    allowed = {"benchmarks", "pricing", "_meta"}
    # slim if no legacy top-level fields present
    legacy = {"aa_model_id","aa_score","coding_score","evidence","evidence_level","confidence","tier"}
    return not any(k in d for k in legacy) and set(d.keys()).issubset(allowed.union(legacy)) and "benchmarks" in d

def _project_slim(raw: dict, key: str) -> dict:
    bm = raw.get("benchmarks") or {}
    # ensure shape
    if not isinstance(bm, dict):
        bm = {}
    if "scores" not in bm:
        bm = {"scores": {}, "raw_benchmarks": [], **(bm if isinstance(bm, dict) else {})}
    if "raw_benchmarks" not in bm:
        bm["raw_benchmarks"] = raw.get("benchmarks", {}).get("raw_benchmarks", []) if isinstance(raw.get("benchmarks"), dict) else []
    pricing = raw.get("pricing") or {}
    if not isinstance(pricing, dict):
        pricing = {}
    # ensure per_provider_overrides present for compat
    if "per_provider_overrides" not in pricing and "overrides" not in pricing:
        pricing = {**pricing, "per_provider_overrides": {}} if pricing else {"per_provider_overrides": {}}
    # _meta slim
    meta = raw.get("_meta") or {}
    slim_meta = {
        "first_seen": meta.get("first_seen"),
        "last_updated": meta.get("last_updated"),
        "version": 2,
    }
    # fallback timestamps if missing
    if not slim_meta["first_seen"]:
        slim_meta["first_seen"] = slim_meta["last_updated"]
    if not slim_meta["last_updated"]:
        slim_meta["last_updated"] = slim_meta["first_seen"]
    return {"benchmarks": bm, "pricing": pricing, "_meta": slim_meta}

def migrate(store_path: Path) -> dict:
    store_path = Path(store_path)
    if not store_path.exists():
        return {"status": "missing", "kept": 0, "purged": 0, "version": STORE_FILE_VERSION}
    raw_text = store_path.read_text()
    try:
        raw = json.loads(raw_text)
    except Exception as e:
        return {"status": f"invalid_json: {e}", "kept": 0, "purged": 0}
    if isinstance(raw, dict) and "models" in raw:
        version = int(raw.get("version", 1))
        models_raw = raw.get("models", {}) or {}
    elif isinstance(raw, dict):
        version = int(raw.get("_version", 1))
        models_raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    else:
        models_raw = {}
        version = 1

    kept = {}
    purged = 0
    purged_keys = []
    for key, rec in (models_raw or {}).items():
        if not isinstance(rec, dict):
            purged += 1
            purged_keys.append(str(key))
            continue
        # If already slim v2, keep without gate (idempotent)
        # Detect slim by absence of legacy fields AND version 2 meta
        meta = rec.get("_meta") or {}
        is_slim = not any(k in rec for k in ("aa_model_id","aa_score","coding_score","evidence","evidence_level","confidence","tier"))                   and not any(k in meta for k in ("source_providers","source_evidence_levels"))
        # Also check version in meta
        if version == 2 and is_slim:
            # Already slim: keep
            # ensure version 2 in meta
            kept[key] = _project_slim(rec, key)
            continue
        # v1 path: gate check
        # Augment with model_id for gate (store key as fallback)
        probe = dict(rec)
        if "model_id" not in probe and "provider_model_id" not in probe:
            probe["model_id"] = key
        # Also ensure pricing/model_id handling for free check: key may not contain free, but original record's pricing may be 0
        ok, reason = is_accurate_enough(probe)
        if not ok:
            purged += 1
            purged_keys.append(f"{key}:{reason}")
            continue
        # Also explicit moderate/weak check (redundant with gate but for clarity)
        lvl = str(rec.get("evidence_level") or "").lower()
        if lvl in ("moderate","weak","none",""):
            # is_accurate_enough already fails for non-strong, but keep explicit
            purged += 1
            purged_keys.append(f"{key}:level={lvl}")
            continue
        kept[key] = _project_slim(rec, key)

    # Atomic write
    payload = {"version": 2, "models": kept}
    # Backup original if not already .bak or if first run
    bak_path = store_path.with_suffix(store_path.suffix + ".bak")
    # Always backup current file before overwrite (idempotent: overwrite bak only if not exists? spec says backup exists)
    # Keep first backup; if bak exists we still overwrite? Safer to keep original first backup, don't overwrite
    if not bak_path.exists():
        try:
            shutil.copy2(store_path, bak_path)
        except Exception:
            # fallback copy text
            bak_path.write_text(raw_text)

    # tmp+rename
    store_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(store_path.parent), prefix=".tmp-store-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=False)
            fh.write("\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
        os.replace(tmp, store_path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass

    return {"status": "migrated", "kept": len(kept), "purged": purged, "purged_keys": purged_keys[:20], "version": 2, "bak": str(bak_path)}

def main():
    p = argparse.ArgumentParser(description="Migrate store v1 -> slim v2")
    p.add_argument("--store-path", default="data/model_info_store.json")
    args = p.parse_args()
    res = migrate(Path(args.store_path))
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    main()
