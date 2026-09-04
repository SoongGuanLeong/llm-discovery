#!/usr/bin/env python3
"""Migration for Cloudflare UUID records — prototypes/issue84/migration.py

Removes UUID-shaped keys from data/model_info_store.json and reports.
Also shows how data/results/cloudflare.yaml will be overwritten on next discover.

Usage:
  python prototypes/issue84/migration.py --dry-run   # report only
  python prototypes/issue84/migration.py --apply      # purge UUID keys in place
  python prototypes/issue84/migration.py --yaml cloudflare.yaml  # lint a yaml file
"""
import argparse, json, re, sys, pathlib, yaml

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
UUID_HEX32 = re.compile(r"^[0-9a-f]{32}$", re.I)

def is_uuid(s: str) -> bool:
    return bool(UUID_RE.match(s) or UUID_HEX32.match(s))

def migrate_store(path: pathlib.Path, dry_run=True):
    data = json.loads(path.read_text())
    models = data.get("models", {})
    uuid_keys = [k for k in models if is_uuid(k)]
    print(f"Store {path}: {len(models)} keys, {len(uuid_keys)} UUID keys")
    for k in uuid_keys:
        print(f"  - {k} -> would purge" + (" (dry-run)" if dry_run else ""))
    if not dry_run and uuid_keys:
        for k in uuid_keys:
            del models[k]
        data["models"] = models
        # atomic write
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(path)
        print(f"Purged {len(uuid_keys)} UUID keys, wrote {path}")
    else:
        print("No changes" if dry_run else f"Purged 0")
    return len(uuid_keys)

def lint_yaml(path: pathlib.Path):
    data = yaml.safe_load(path.read_text())
    # batch shape: drop_llm + keep + error
    uuid_ids = []
    for bucket in ("keep", "drop_llm", "drop", "error"):
        for rec in data.get(bucket, []) or []:
            mid = rec.get("model_id") or rec.get("provider_model_id") or ""
            if is_uuid(str(mid)):
                uuid_ids.append(mid)
    print(f"YAML {path}: {len(uuid_ids)} UUID model_ids out of {(len(data.get('keep',[]))+len(data.get('drop_llm',[]))+len(data.get('error',[]))) } records")
    for mid in uuid_ids[:10]:
        print(f"  - {mid}")
    return uuid_ids

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="data/model_info_store.json")
    ap.add_argument("--yaml", default="data/results/cloudflare.yaml")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    store_p = pathlib.Path(args.store)
    yaml_p = pathlib.Path(args.yaml)
    if store_p.exists():
        migrate_store(store_p, dry_run=not args.apply)
    else:
        print(f"Store {store_p} not found")
    if yaml_p.exists():
        lint_yaml(yaml_p)
        if args.apply:
            print(f"Note: YAML {yaml_p} will be overwritten on next discover_provider(cloudflare) — no manual rewrite needed")
    else:
        print(f"YAML {yaml_p} not found")
