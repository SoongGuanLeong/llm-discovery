"""OmniRoute export generator â tickets 166 + 167 + 168 + 176.

Local-first generator: reads config/providers.yaml + data/results/*.yaml
and emits OmniRoute import file + 3 tier combos (keep-all, reset-aware).
Supports --dry-run (local only) and --apply (POST to OmniRoute gateway).

Ticket 176: connection provisioning â custom OpenAI-compatible nodes for
nararouter/zai/agnes, opencode-zen registry id, retirement of frozen
registry connections + free opencode, providerSpecificData freshness patches.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_PROVIDERS = Path("config/providers.yaml")
DEFAULT_RESULTS_DIR = Path("data/results")
DEFAULT_OUTPUT_DIR = Path("data/derived")
IMPORT_FILENAME = "omniroute_import.json"
COMBOS_FILENAME = "omniroute_combos.json"
DEFAULT_OMNIROUTE_URL = "http://localhost:20128"

TIER_FLASH = "flash"
TIER_MAX = "max"
TIER_CONTRIBUTOR_FREE = "contributor_free"
ALL_TIERS = [TIER_FLASH, TIER_MAX, TIER_CONTRIBUTOR_FREE]

SCAFFOLD_COMBOS = [
    {"name": "flash", "models": [], "strategy": "reset-aware", "config": {}},
    {"name": "max", "models": [], "strategy": "reset-aware", "config": {}},
    {"name": "contributor_free", "models": [], "strategy": "reset-aware", "config": {}},
]

BULK_IMPORT_CANDIDATES = [
    "/api/providers/bulk-import",
    "/api/providers/import",
    "/api/provider/import",
    "/api/providers/bulk_import",
]

COMBOS_LIST_PATH = "/api/combos"
COMBO_CREATE_PATH = "/api/combos"

# Ticket 176: providers that map to custom OpenAI-compatible node ids
# (registry entries lack modelsUrl; baseUrl honored only by compatible nodes)
CUSTOM_NODE_MAP = {
    "nararouter": "openai-compatible-nara",
    "zai": "openai-compatible-zai",
    "agnes": "openai-compatible-agnes",
}

# Ticket 176: opencode_zen maps to opencode-zen registry id (not free opencode)
OPENCOD_ZEN_MAP = {"opencode_zen": "opencode-zen"}

# Registry alias map for apply-time fallback (import file keeps original names for determinism)
_PROVIDER_ALIAS = {
    "google": "gemini",
    "nvidia_nim": "nvidia",
    "cloudflare": "cloudflare-ai",
    "kilo_ai": "kilo-gateway",
    "navy_ai": "navy",
    "ollama_cloud": "ollama-cloud",
    "sea-lion": "sealion",
}

# Ticket 176: retired provider ids â frozen registry + free opencode
RETIRED_PROVIDER_IDS = frozenset({
    "nara",        # frozen static catalog (3 models, live=47)
    "zai",         # frozen static catalog (7 models, live=10)
    "agnes",       # frozen static catalog (3 models, live=12)
    "opencode",    # free no-auth (70 models, zen=13)
})


def generate_scaffold_payload() -> dict[str, Any]:
    return {"import": [], "combos": [dict(c) for c in SCAFFOLD_COMBOS], "meta": {"version": 0, "scaffold": True}}


def write_scaffold_files(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = generate_scaffold_payload()
    import_payload: list[Any] = payload["import"]
    combos_payload = payload["combos"]
    import_path = output_dir / IMPORT_FILENAME
    combos_path = output_dir / COMBOS_FILENAME
    import_path.write_text(json.dumps(import_payload, indent=2, sort_keys=True) + "\n")
    combos_path.write_text(json.dumps(combos_payload, indent=2, sort_keys=True) + "\n")
    return {"import": import_path, "combos": combos_path}


def _load_raw_providers(path: Path) -> list[dict[str, Any]]:
    try:
        data = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        return []
    if not isinstance(data, dict):
        return []
    providers = data.get("providers")
    if providers is None:
        return []
    if not isinstance(providers, list):
        return []
    return [p for p in providers if isinstance(p, dict)]


def build_import_entries(providers_path: Path = DEFAULT_PROVIDERS) -> list[dict[str, Any]]:
    """Build import rows from providers.yaml.

    Ticket 176: nararouter/zai/agnes map to custom OpenAI-compatible node ids;
    opencode_zen maps to opencode-zen registry id. Connection names keep yaml
    names for traceability.
    """
    providers_path = Path(providers_path)
    raw = _load_raw_providers(providers_path)
    rows: list[dict[str, Any]] = []
    for p in raw:
        name = str(p.get("name") or p.get("provider") or "").strip()
        if not name:
            continue
        secret = str(p.get("secret") or "").strip()
        base = p.get("base_url")
        if base is None:
            base = p.get("baseUrl")
        # Ticket 176: map to custom node / zen registry id
        provider_id = CUSTOM_NODE_MAP.get(name, OPENCOD_ZEN_MAP.get(name, name))
        row: dict[str, Any] = {"provider": provider_id, "name": name, "apiKey": f"env:{secret}" if secret else ""}
        if base is not None and str(base).strip() != "":
            row["baseUrl"] = str(base)
        if "priority" in p and p["priority"] is not None:
            try:
                prio = int(p["priority"])
                row["priority"] = prio
            except (ValueError, TypeError):
                row["priority"] = p["priority"]
        rows.append(row)
    rows.sort(key=lambda r: r["provider"])
    return rows


def resolve_import_secrets(rows: list[dict[str, Any]], env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    if env is None:
        env = dict(os.environ)
    resolved: list[dict[str, Any]] = []
    for r in rows:
        nr = dict(r)
        ak = str(nr.get("apiKey", ""))
        if ak.startswith("env:"):
            var = ak[4:]
            val = env.get(var)
            if val:
                nr["apiKey"] = val
            else:
                nr["apiKey"] = ak
        # resolve ${VAR} in baseUrl (cloudflare)
        if "baseUrl" in nr and isinstance(nr["baseUrl"], str) and "${" in nr["baseUrl"]:
            import re
            nr["baseUrl"] = re.sub(r"\$\{([^}]+)\}", lambda m: env.get(m.group(1), m.group(0)), str(nr["baseUrl"]))
        resolved.append(nr)
    return resolved


def redact_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        nr = dict(r)
        if "apiKey" in nr and nr["apiKey"]:
            ak = str(nr["apiKey"])
            if ak.startswith("env:"):
                nr["apiKey"] = ak
            else:
                nr["apiKey"] = "***"
        out.append(nr)
    return out


def get_auth_headers(explicit_key: str | None = None) -> dict[str, str]:
    key = explicit_key or os.environ.get("OMNIROUTE_API_KEY") or os.environ.get("OMNIROUTE_MANAGE_KEY") or os.environ.get("OMNIROUTE_TOKEN") or os.environ.get("OMNIROUTE_AUTH_TOKEN")
    if key:
        return {"Authorization": f"Bearer {key}"}
    return {}


def _normalize_tier(tier: str | None) -> str | None:
    if tier == "contributor_special":
        return TIER_CONTRIBUTOR_FREE
    return tier


def _is_contributor_model(model_id: str) -> bool:
    return "contributor" in model_id.lower()


def _load_keep_records(results_dir: Path = DEFAULT_RESULTS_DIR) -> list[dict[str, Any]]:
    results_dir = Path(results_dir)
    keeps: list[dict[str, Any]] = []
    if not results_dir.exists():
        return keeps
    for yf in sorted(results_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(yf.read_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        provider = str(data.get("provider") or yf.stem)
        for rec in data.get("keep", []) or []:
            if not isinstance(rec, dict):
                continue
            keeps.append({"provider": provider, "model_id": str(rec.get("model_id", "")), "tier": _normalize_tier(rec.get("tier"))})
    return keeps


def group_keeps_by_tier(keeps: list[dict[str, Any]], *, strict_contributor_free: bool = True) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {t: [] for t in ALL_TIERS}
    for rec in keeps:
        tier = rec.get("tier")
        if tier not in ALL_TIERS:
            continue
        if tier == TIER_CONTRIBUTOR_FREE and strict_contributor_free:
            mid = str(rec.get("model_id", ""))
            if not _is_contributor_model(mid):
                continue
        grouped[tier].append(rec)
    return grouped


def build_combo_entries(results_dir: Path = DEFAULT_RESULTS_DIR, *, strict_contributor_free: bool = True) -> list[dict[str, Any]]:
    keeps = _load_keep_records(results_dir)
    grouped = group_keeps_by_tier(keeps, strict_contributor_free=strict_contributor_free)
    combos: list[dict[str, Any]] = []
    for tier in ALL_TIERS:
        recs = grouped[tier]
        if not recs:
            print(f"warning: tier {tier} has 0 targets, skipping combo", file=sys.stderr)
            continue
        recs_sorted = sorted(recs, key=lambda r: (str(r.get("provider", "")), str(r.get("model_id", ""))))
        models = [{"provider": str(r["provider"]), "model": str(r["model_id"])} for r in recs_sorted]
        combos.append({"name": tier, "models": models, "strategy": "reset-aware", "config": {}})
    combos.sort(key=lambda c: c.get("name", ""))
    return combos


def generate_payload(providers_path: Path = DEFAULT_PROVIDERS, results_dir: Path = DEFAULT_RESULTS_DIR) -> dict[str, Any]:
    providers_path = Path(providers_path)
    results_dir = Path(results_dir)
    import_rows = build_import_entries(providers_path) if providers_path.exists() else []
    combos: list[dict[str, Any]]
    if results_dir.exists():
        combos = build_combo_entries(results_dir)
        if not combos:
            combos = [dict(c) for c in SCAFFOLD_COMBOS]
            combos.sort(key=lambda c: c.get("name", ""))
    else:
        combos = [dict(c) for c in SCAFFOLD_COMBOS]
    if import_rows or any(c.get("models") for c in combos):
        meta = {"version": 1, "scaffold": False}
    else:
        meta = {"version": 0, "scaffold": True}
    return {"import": import_rows, "combos": combos, "meta": meta}


def write_payload_files(payload: dict[str, Any], output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Path]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    import_path = output_dir / IMPORT_FILENAME
    combos_path = output_dir / COMBOS_FILENAME
    import_payload = payload.get("import", [])
    combos_payload = payload.get("combos", [])
    if isinstance(combos_payload, list):
        combos_payload = sorted(combos_payload, key=lambda c: c.get("name", ""))
        for c in combos_payload:
            if isinstance(c.get("models"), list):
                c["models"] = sorted(c["models"], key=lambda m: (m.get("provider",""), m.get("model","")))
    import_path.write_text(json.dumps(import_payload, indent=2, sort_keys=True) + "\n")
    combos_path.write_text(json.dumps(combos_payload, indent=2, sort_keys=True) + "\n")
    return {"import": import_path, "combos": combos_path}

def _httpx_client():
    try:
        import httpx  # type: ignore
    except ImportError as e:
        print(f"httpx required for --apply: {e}", file=sys.stderr)
        raise SystemExit(2)
    return httpx


def _map_provider(p: str) -> str:
    return _PROVIDER_ALIAS.get(p, p)


def apply_import_entries(base_url: str, rows: list[dict[str, Any]], auth_headers: dict[str, str] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    httpx = _httpx_client()
    headers = {"Content-Type": "application/json"}
    if auth_headers:
        headers.update(auth_headers)
    base = base_url.rstrip("/")
    last_err: str | None = None
    for path in BULK_IMPORT_CANDIDATES:
        url = base + path
        for body in [rows, {"providers": rows}]:
            try:
                resp = httpx.post(url, json=body, headers=headers, timeout=timeout)
            except Exception as e:
                last_err = f"{url} -> {e}"
                continue
            if resp.status_code in (200, 201, 204):
                try:
                    data = resp.json() if resp.content else {}
                except Exception:
                    data = {"raw": resp.text[:500]}
                return {"url": url, "status": resp.status_code, "response": data, "sent": len(rows)}
            if resp.status_code == 404:
                last_err = f"{url} 404"
                break
            if resp.status_code == 400:
                last_err = f"{url} 400: {resp.text[:300]}"
                continue
            last_err = f"{url} {resp.status_code}: {resp.text[:300]}"
        if last_err and "404" not in last_err:
            pass
    print(f"bulk endpoints unavailable ({last_err}), falling back to per-provider POST /api/providers", file=sys.stderr)
    single_url = base + "/api/providers"
    results: list[dict[str, Any]] = []
    fallback_err: str | None = None
    for row in rows:
        ak = str(row.get("apiKey", ""))
        if ak.startswith("env:"):
            print(f"skip {row.get('provider')}: apiKey still placeholder {ak} (missing env)", file=sys.stderr)
            results.append({"provider": row.get("provider"), "skipped": True, "reason": "missing env"})
            continue
        # map provider for registry gaps, keep original name as connection name
        orig = row.get("provider")
        mapped = _map_provider(str(orig))
        send_row = dict(row)
        send_row["provider"] = mapped
        # name stays original for traceability
        send_row["name"] = row.get("name", orig)
        if mapped != orig:
            print(f"map {orig} -> {mapped} (registry alias)", file=sys.stderr)
        try:
            resp = httpx.post(single_url, json=send_row, headers=headers, timeout=timeout)
        except Exception as e:
            fallback_err = f"{orig} -> {e}"
            results.append({"provider": orig, "mapped": mapped, "error": fallback_err})
            continue
        if resp.status_code in (200, 201, 204):
            try:
                data = resp.json() if resp.content else {}
            except Exception:
                data = {"raw": resp.text[:200]}
            results.append({"provider": orig, "mapped": mapped, "status": resp.status_code, "id": (data.get("connection") or {}).get("id")})
        else:
            # if openai fallback still fails, try again without mapping (last resort)
            fallback_err = f"{orig}({mapped}) {resp.status_code}: {resp.text[:300]}"
            results.append({"provider": orig, "mapped": mapped, "error": fallback_err})
    successes = [r for r in results if "status" in r]
    if not successes and any("error" in r for r in results):
        raise RuntimeError(f"per-provider fallback failed, last error: {fallback_err} (bulk last: {last_err})")
    return {"url": single_url + " (per-row fallback)", "status": 201 if successes else 200, "response": {"results": results, "fallback": True}, "sent": len(rows), "fallback": True}


def fetch_combos(base_url: str, auth_headers: dict[str, str] | None = None, timeout: float = 15.0) -> list[dict[str, Any]]:
    httpx = _httpx_client()
    headers: dict[str, str] = {}
    if auth_headers:
        headers.update(auth_headers)
    url = base_url.rstrip("/") + COMBOS_LIST_PATH
    resp = httpx.get(url, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"GET {url} failed {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    if isinstance(data, dict) and "combos" in data:
        return data["combos"] if isinstance(data["combos"], list) else []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
        return data["data"]
    return []


def upsert_combos(base_url: str, combos: list[dict[str, Any]], auth_headers: dict[str, str] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    httpx = _httpx_client()
    headers = {"Content-Type": "application/json"}
    if auth_headers:
        headers.update(auth_headers)
    base = base_url.rstrip("/")
    combos_sorted = sorted(combos, key=lambda c: c.get("name", ""))
    for c in combos_sorted:
        c["models"] = sorted(c.get("models", []), key=lambda m: (m.get("provider", ""), m.get("model","")))
    existing = fetch_combos(base_url, auth_headers, timeout=timeout)
    by_name: dict[str, dict[str, Any]] = {}
    for e in existing:
        n = e.get("name") or e.get("combo_name") or e.get("id")
        if isinstance(n, str):
            by_name[n] = e
    results: list[dict[str, Any]] = []
    for combo in combos_sorted:
        name = combo["name"]
        existing_entry = by_name.get(name)
        if existing_entry is not None:
            cid = existing_entry.get("id") or existing_entry.get("_id") or name
            put_urls = [f"{base}/api/combos/{cid}", f"{base}{COMBO_CREATE_PATH}/{cid}"]
            put_ok = False
            last_err = None
            for url in put_urls:
                try:
                    resp = httpx.put(url, json=combo, headers=headers, timeout=timeout)
                except Exception as e:
                    last_err = str(e)
                    continue
                if resp.status_code in (200, 201, 204):
                    results.append({"name": name, "method": "PUT", "url": url, "status": resp.status_code})
                    put_ok = True
                    break
                last_err = f"{url} {resp.status_code}: {resp.text[:300]}"
            if not put_ok:
                try:
                    resp = httpx.post(base + COMBO_CREATE_PATH, json=combo, headers=headers, timeout=timeout)
                    if resp.status_code in (200, 201, 204):
                        results.append({"name": name, "method": "POST(upsert)", "url": base + COMBO_CREATE_PATH, "status": resp.status_code})
                        continue
                    raise RuntimeError(last_err or f"POST fallback failed {resp.status_code}: {resp.text[:300]}")
                except Exception as e:
                    raise RuntimeError(f"upsert combo {name} failed: {last_err} | fallback: {e}")
        else:
            url = base + COMBO_CREATE_PATH
            resp = httpx.post(url, json=combo, headers=headers, timeout=timeout)
            if resp.status_code not in (200, 201, 204):
                raise RuntimeError(f"POST {url} combo {name} failed {resp.status_code}: {resp.text[:500]}")
            results.append({"name": name, "method": "POST", "url": url, "status": resp.status_code})
    return {"upserted": results, "existing_count": len(existing)}

def _fetch_existing_connections(base_url: str, auth_headers: dict[str, str] | None = None, timeout: float = 15.0) -> list[dict[str, Any]]:
    """Fetch all existing connections from the gateway."""
    httpx = _httpx_client()
    headers: dict[str, str] = {}
    if auth_headers:
        headers.update(auth_headers)
    url = base_url.rstrip("/") + "/api/providers"
    resp = httpx.get(url, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        return []
    data = resp.json()
    return data.get("connections", [])


def retire_connections(base_url: str, retired_ids: frozenset[str], auth_headers: dict[str, str] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    """Ticket 176: retire (deactivate) connections whose provider ids are in retired_ids."""
    httpx = _httpx_client()
    headers = {"Content-Type": "application/json"}
    if auth_headers:
        headers.update(auth_headers)
    base = base_url.rstrip("/")
    existing = _fetch_existing_connections(base_url, auth_headers, timeout)
    to_retire = [c for c in existing if c.get("provider") in retired_ids]
    if not to_retire:
        return {"retired": [], "count": 0}
    results: list[dict[str, Any]] = []
    for conn in to_retire:
        cid = conn.get("id")
        if not cid:
            continue
        # Try PUT isActive=false first, then DELETE
        put_url = f"{base}/api/providers/{cid}"
        try:
            resp = httpx.put(put_url, json={"isActive": False}, headers=headers, timeout=timeout)
            if resp.status_code in (200, 201, 204):
                results.append({"id": cid, "provider": conn.get("provider"), "method": "PUT", "status": resp.status_code})
                continue
        except Exception:
            pass
        # Fallback: DELETE
        del_url = f"{base}/api/providers/{cid}"
        try:
            resp = httpx.delete(del_url, headers=headers, timeout=timeout)
            if resp.status_code in (200, 201, 204):
                results.append({"id": cid, "provider": conn.get("provider"), "method": "DELETE", "status": resp.status_code})
                continue
        except Exception as e:
            results.append({"id": cid, "provider": conn.get("provider"), "error": str(e)})
    return {"retired": results, "count": len(results)}


def patch_provider_specific_data(base_url: str, import_rows: list[dict[str, Any]], auth_headers: dict[str, str] | None = None, timeout: float = 15.0, resolve_env: dict[str, str] | None = None) -> dict[str, Any]:
    """Ticket 176: patch providerSpecificData on live-discoverable connections.

    - cloudflare: accountId from env CLOUDFLARE_ACCOUNT_ID (warn+skip if missing)
    - every live-discoverable connection: autoSync=true + autoFetchModels=true
    """
    if resolve_env is None:
        resolve_env = dict(os.environ)
    httpx = _httpx_client()
    headers = {"Content-Type": "application/json"}
    if auth_headers:
        headers.update(auth_headers)
    base = base_url.rstrip("/")
    existing = _fetch_existing_connections(base_url, auth_headers, timeout)
    # Build set of live-discoverable provider ids from import rows
    live_ids = {r["provider"] for r in import_rows}
    # Also include cloudflare-ai (mapped from cloudflare)
    live_ids.add("cloudflare-ai")
    # Filter existing connections to live-discoverable ones
    to_patch = [c for c in existing if c.get("provider") in live_ids]
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for conn in to_patch:
        cid = conn.get("id")
        provider = conn.get("provider")
        if not cid:
            continue
        psd: dict[str, Any] = {}
        # cloudflare accountId from env
        if provider == "cloudflare-ai":
            account_id = resolve_env.get("CLOUDFLARE_ACCOUNT_ID")
            if account_id:
                psd["accountId"] = account_id
            else:
                warnings.append("cloudflare accountId missing: CLOUDFLARE_ACCOUNT_ID not set, skipping")
        # autoSync + autoFetchModels on every live-discoverable connection
        psd["autoSync"] = True
        psd["autoFetchModels"] = True
        put_url = f"{base}/api/providers/{cid}"
        try:
            resp = httpx.put(put_url, json={"providerSpecificData": psd}, headers=headers, timeout=timeout)
            if resp.status_code in (200, 201, 204):
                results.append({"id": cid, "provider": provider, "patched": list(psd.keys()), "status": resp.status_code})
            else:
                results.append({"id": cid, "provider": provider, "error": f"{resp.status_code}: {resp.text[:200]}"})
        except Exception as e:
            results.append({"id": cid, "provider": provider, "error": str(e)})
    return {"patched": results, "count": len(results), "warnings": warnings}


def apply_payload(payload: dict[str, Any], base_url: str = DEFAULT_OMNIROUTE_URL, auth_headers: dict[str, str] | None = None, resolve_env: dict[str, str] | None = None) -> dict[str, Any]:
    if resolve_env is None:
        resolve_env = dict(os.environ)
    import_rows = payload.get("import", [])
    combos = payload.get("combos", [])
    resolved_import = resolve_import_secrets(import_rows, env=resolve_env)
    missing = [r["provider"] for r in resolved_import if str(r.get("apiKey", "")).startswith("env:")]
    if missing:
        print(f"warning: {len(missing)} providers missing env vars (kept placeholder): {missing[:5]}", file=sys.stderr)
    summary: dict[str, Any] = {"base_url": base_url}
    # Ticket 176: retire frozen registry connections + free opencode
    if RETIRED_PROVIDER_IDS:
        print(f"retiring {len(RETIRED_PROVIDER_IDS)} frozen provider connections ...", file=sys.stderr)
        retire_res = retire_connections(base_url, RETIRED_PROVIDER_IDS, auth_headers=auth_headers)
        summary["retire"] = retire_res
        print(f"retired: {retire_res['count']}", file=sys.stderr)
    if resolved_import:
        print(f"applying {len(resolved_import)} provider imports to {base_url} ...", file=sys.stderr)
        res = apply_import_entries(base_url, resolved_import, auth_headers=auth_headers)
        summary["import"] = res
        print(f"import ok: {res['url']} status {res['status']} sent {res['sent']}", file=sys.stderr)
    else:
        summary["import"] = {"sent": 0, "skipped": True}
    # Ticket 176: patch providerSpecificData (cloudflare accountId + autoSync/autoFetchModels)
    print(f"patching providerSpecificData on live-discoverable connections ...", file=sys.stderr)
    patch_res = patch_provider_specific_data(base_url, resolved_import, auth_headers=auth_headers, resolve_env=resolve_env)
    summary["psd_patches"] = patch_res
    if patch_res.get("warnings"):
        for w in patch_res["warnings"]:
            print(f"warning: {w}", file=sys.stderr)
    print(f"psd patched: {patch_res['count']}", file=sys.stderr)
    if combos:
        combos_nonempty = [c for c in combos if c.get("models")]
        if combos_nonempty:
            print(f"upserting {len(combos_nonempty)} combos to {base_url} ...", file=sys.stderr)
            res2 = upsert_combos(base_url, combos_nonempty, auth_headers=auth_headers)
            summary["combos"] = res2
            print(f"combos ok: {len(res2['upserted'])} upserted", file=sys.stderr)
        else:
            summary["combos"] = {"upserted": [], "skipped_empty": True}
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="omniroute_export", description="OmniRoute export generator â emits import + combos")
    p.add_argument("--dry-run", action="store_true", help="Write files without network (placeholder apiKey)")
    p.add_argument("--apply", action="store_true", help="POST import + combos to OmniRoute gateway (requires --omniroute-url reachable)")
    p.add_argument("--omniroute-url", type=str, default=DEFAULT_OMNIROUTE_URL, help="OmniRoute base URL (default http://localhost:20128)")
    p.add_argument("--api-key", type=str, default=None, help="Management API key / dashboard token (or env OMNIROUTE_API_KEY)")
    p.add_argument("--providers", type=Path, default=DEFAULT_PROVIDERS, help="providers.yaml path")
    p.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR, help="data/results dir")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="output dir (default data/derived)")
    p.add_argument("--check", action="store_true", help="Alias for --dry-run validation")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.apply:
        payload = generate_payload(Path(args.providers), Path(args.results_dir))
        write_payload_files(payload, Path(args.output_dir))
        if not payload.get("import") and not any(c.get("models") for c in payload.get("combos", [])):
            print("warning: payload empty (no providers/combos), nothing to apply", file=sys.stderr)
        redacted = {"import": redact_rows(payload.get("import", [])), "combos": payload.get("combos", []), "meta": payload.get("meta", {})}
        sys.stdout.write(json.dumps(redacted, indent=2, sort_keys=True) + "\n")
        auth_headers = get_auth_headers(args.api_key)
        try:
            summary = apply_payload(payload, base_url=args.omniroute_url, auth_headers=auth_headers or None)
        except Exception as e:
            msg = str(e)
            print(f"apply failed: {msg}", file=sys.stderr)
            return 1
        print(json.dumps({"applied": True, "summary": summary}, indent=2, sort_keys=True), file=sys.stderr)
        return 0
    if args.dry_run or args.check:
        payload = generate_payload(Path(args.providers), Path(args.results_dir))
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        write_payload_files(payload, Path(args.output_dir))
        return 0
    providers_path = Path(args.providers)
    results_dir = Path(args.results_dir)
    missing: list[str] = []
    if not providers_path.exists():
        missing.append(f"providers not found: {providers_path}")
    if not results_dir.exists():
        missing.append(f"results dir not found: {results_dir}")
    if missing:
        for m in missing:
            print(m, file=sys.stderr)
        print("hint: use --dry-run for scaffold without inputs or --apply to push to gateway", file=sys.stderr)
        return 2
    payload = generate_payload(providers_path, results_dir)
    sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    write_payload_files(payload, Path(args.output_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
