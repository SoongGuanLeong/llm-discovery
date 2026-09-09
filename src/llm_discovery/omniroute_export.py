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

try:
    from .secrets import load_all_secrets
except ImportError:  # pragma: no cover - optional dependency
    load_all_secrets = None  # type: ignore

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

PROVIDER_MODELS_PATH = "/api/provider-models"

# Registry alias map for apply-time fallback (import file keeps original names for determinism)
_PROVIDER_ALIAS = {
    "google": "gemini",
    "nvidia_nim": "nvidia",
    "cloudflare": "cloudflare-ai",
    "kilo_ai": "kilo-gateway",
    "navy_ai": "navy",
    "ollama_cloud": "ollama-cloud",
    "sea-lion": "sealion",
    "opencode_zen": "opencode-zen",
    "modelscope": "modelscope-custom",
}

# Ticket 176: providers that map to custom OpenAI-compatible node ids
# Stable custom ids — model names after import are provider/model like nararouter-custom/muse-spark-...
CUSTOM_NODE_MAP = {
    "nararouter": "nararouter-custom",
    "zai": "zai-custom",
    "agnes": "agnes-custom",
}

# Ticket 176: opencode_zen maps to opencode-zen registry id (not free opencode)
OPENCOD_ZEN_MAP = {"opencode_zen": "opencode-zen"}


# Ticket 177: model provisioning provider mapping (same as T1)
_MODEL_PROVIDER_MAP = {
    **CUSTOM_NODE_MAP,
    **OPENCOD_ZEN_MAP,
    **_PROVIDER_ALIAS,
}

# Ticket 176: retired provider ids â frozen registry + free opencode
RETIRED_PROVIDER_IDS = frozenset({
    "nara",        # frozen static catalog (3 models, live=47 via custom node)
    "zai",         # frozen static catalog (7 models, live=10 via custom node)
    "agnes",       # frozen static catalog (3 models, live=12 via custom node)
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
        # Ticket 176 + 177: map via full alias table so import provider ids match combo/model ids
        # (custom nodes + gemini/cloudflare-ai/kilo-gateway/navy/nvidia/ollama-cloud etc)
        provider_id = _MODEL_PROVIDER_MAP.get(name, name)
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


def _map_provider_for_model(p: str) -> str:
    return _MODEL_PROVIDER_MAP.get(p, p)


def build_model_entries(results_dir: Path = DEFAULT_RESULTS_DIR) -> list[dict[str, Any]]:
    """Ticket 177: build one POST body per keep from the keep lists."""
    keeps = _load_keep_records(results_dir)
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for rec in keeps:
        provider = str(rec.get("provider", ""))
        model_id = str(rec.get("model_id", ""))
        if not provider or not model_id:
            continue
        mapped = _map_provider_for_model(provider)
        key = (mapped, model_id)
        if key in seen:
            continue
        seen.add(key)
        entries.append({"provider": mapped, "modelId": model_id, "source": "manual"})
    entries.sort(key=lambda e: (e["provider"], e["modelId"]))
    return entries


def build_gc_plan(results_dir: Path = DEFAULT_RESULTS_DIR) -> dict[str, set[str]]:
    """Ticket 177: per-provider keep model_ids for guarded GC.

    Only providers whose results file exists and parses are included.
    Providers with missing or unparseable results files are skipped entirely.
    """
    keeps = _load_keep_records(results_dir)
    plan: dict[str, set[str]] = {}
    for rec in keeps:
        provider = str(rec.get("provider", ""))
        model_id = str(rec.get("model_id", ""))
        if not provider or not model_id:
            continue
        mapped = _map_provider_for_model(provider)
        plan.setdefault(mapped, set()).add(model_id)
    return plan


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
        models = []
        for r in recs_sorted:
            provider = _map_provider_for_model(str(r["provider"]))
            model_id = str(r["model_id"])
            models.append({"provider": provider, "model": model_id})
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
    model_entries = build_model_entries(results_dir) if results_dir.exists() else []
    gc_plan = build_gc_plan(results_dir) if results_dir.exists() else {}
    if import_rows or any(c.get("models") for c in combos) or model_entries:
        meta = {"version": 1, "scaffold": False}
    else:
        meta = {"version": 0, "scaffold": True}
    return {"import": import_rows, "models": model_entries, "gc": {k: sorted(v) for k, v in gc_plan.items()}, "combos": combos, "meta": meta}


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
    # Build set of live-discoverable provider ids from import rows (mapped via custom + alias)
    live_ids = {CUSTOM_NODE_MAP.get(str(r["provider"]), OPENCOD_ZEN_MAP.get(str(r["provider"]), _map_provider(str(r["provider"])))) for r in import_rows}
    # Also include cloudflare-ai (mapped from cloudflare)
    live_ids.add("cloudflare-ai")
    # Build desired baseUrl map from import rows (match by connection name first, then provider id)
    desired_base_urls: dict[str, str] = {}
    desired_base_urls_by_provider: dict[str, str] = {}
    import_names = {str(r.get("name") or "").strip() for r in import_rows}
    for r in import_rows:
        name = str(r.get("name") or "").strip()
        base_url_val = r.get("baseUrl")
        if name and base_url_val:
            desired_base_urls[name] = str(base_url_val)
        provider_id = str(r.get("provider") or "").strip()
        if provider_id and base_url_val:
            desired_base_urls_by_provider[provider_id] = str(base_url_val)
            # also index by alias/custom mapped ids
            for m in [_map_provider(provider_id), CUSTOM_NODE_MAP.get(provider_id, ""), OPENCOD_ZEN_MAP.get(provider_id, "")]:
                if m and m != provider_id:
                    desired_base_urls_by_provider[m] = str(base_url_val)
    # Filter existing connections to live-discoverable ones (by provider or by name fallback for aliases like nararouter->nara)
    to_patch = [c for c in existing if c.get("provider") in live_ids or str(c.get("name") or "").strip() in import_names]
    results: list[dict[str, Any]] = []
    warnings: list[str] = []
    for conn in to_patch:
        cid = conn.get("id")
        provider = conn.get("provider")
        name = conn.get("name")
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
        # Ticket 178: patch baseUrl for providers whose registry baseUrl is stale/missing
        # (UI does not expose baseUrl editing, so we fix it via API)
        desired_base = desired_base_urls.get(str(name or "")) or desired_base_urls_by_provider.get(str(provider or ""))
        if desired_base:
            current_base = (conn.get("providerSpecificData") or {}).get("baseUrl", "")
            if current_base != desired_base:
                psd["baseUrl"] = desired_base
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




def apply_model_upserts(base_url: str, entries: list[dict[str, Any]], auth_headers: dict[str, str] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    """Ticket 177: POST each keep as a custom model row."""
    httpx = _httpx_client()
    headers = {"Content-Type": "application/json"}
    if auth_headers:
        headers.update(auth_headers)
    base = base_url.rstrip("/")
    results: list[dict[str, Any]] = []
    for entry in entries:
        url = base + PROVIDER_MODELS_PATH
        try:
            resp = httpx.post(url, json=entry, headers=headers, timeout=timeout)
        except Exception as e:
            results.append({"provider": entry.get("provider"), "modelId": entry.get("modelId"), "error": str(e)})
            continue
        if resp.status_code in (200, 201, 204):
            try:
                data = resp.json() if resp.content else {}
            except Exception:
                data = {"raw": resp.text[:200]}
            results.append({"provider": entry.get("provider"), "modelId": entry.get("modelId"), "status": resp.status_code, "id": data.get("id")})
        else:
            results.append({"provider": entry.get("provider"), "modelId": entry.get("modelId"), "error": f"{resp.status_code}: {resp.text[:300]}"})
    successes = [r for r in results if "status" in r]
    return {"upserted": successes, "sent": len(entries), "results": results}


def fetch_existing_models(base_url: str, provider: str, auth_headers: dict[str, str] | None = None, timeout: float = 15.0) -> list[dict[str, Any]]:
    """Ticket 177: fetch existing custom models for a provider."""
    httpx = _httpx_client()
    headers: dict[str, str] = {}
    if auth_headers:
        headers.update(auth_headers)
    url = base_url.rstrip("/") + PROVIDER_MODELS_PATH + "?provider=" + str(provider)
    resp = httpx.get(url, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        return []
    data = resp.json()
    if isinstance(data, dict):
        return data.get("models", [])
    if isinstance(data, list):
        return data
    return []


def apply_gc(base_url: str, provider: str, keep_model_ids: set[str], auth_headers: dict[str, str] | None = None, timeout: float = 15.0) -> dict[str, Any]:
    """Ticket 177: guarded GC - delete custom rows not in keep set."""
    existing = fetch_existing_models(base_url, provider, auth_headers, timeout)
    to_delete = [m for m in existing if (m.get("modelId") or m.get("id")) not in keep_model_ids]
    if not to_delete:
        return {"provider": provider, "deleted": [], "count": 0, "skipped_empty": True}
    httpx = _httpx_client()
    headers = {"Content-Type": "application/json"}
    if auth_headers:
        headers.update(auth_headers)
    base = base_url.rstrip("/")
    results: list[dict[str, Any]] = []
    for model in to_delete:
        mid = model.get("modelId") or model.get("id")
        # Gateway requires model= not modelId= (see manual test: model param succeeds)
        del_url = base + PROVIDER_MODELS_PATH + "?provider=" + str(provider) + "&model=" + str(mid)
        try:
            resp = httpx.delete(del_url, headers=headers, timeout=timeout)
            if resp.status_code in (200, 201, 204):
                results.append({"provider": provider, "modelId": mid, "status": resp.status_code})
                continue
        except Exception:
            pass
        model_id = model.get("id") or model.get("_id")
        if model_id:
            del_url2 = base + PROVIDER_MODELS_PATH + "/" + str(model_id)
            try:
                resp = httpx.delete(del_url2, headers=headers, timeout=timeout)
                if resp.status_code in (200, 201, 204):
                    results.append({"provider": provider, "modelId": mid, "status": resp.status_code})
                    continue
            except Exception:
                pass
        results.append({"provider": provider, "modelId": mid, "error": "delete_failed"})
    return {"provider": provider, "deleted": results, "count": len(results)}
def apply_payload(payload: dict[str, Any], base_url: str = DEFAULT_OMNIROUTE_URL, auth_headers: dict[str, str] | None = None, resolve_env: dict[str, str] | None = None) -> dict[str, Any]:
    if resolve_env is None:
        resolve_env = dict(os.environ)
    import_rows = payload.get("import", [])
    combos = payload.get("combos", [])
    model_entries = payload.get("models", [])
    gc_plan = payload.get("gc", {})
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
    # Ticket 177: model provisioning
    if model_entries:
        print(f"upserting {len(model_entries)} custom models to {base_url} ...", file=sys.stderr)
        model_res = apply_model_upserts(base_url, model_entries, auth_headers=auth_headers)
        summary["models"] = model_res
        print(f"models ok: {model_res['sent']} sent, {len(model_res.get('upserted', []))} upserted", file=sys.stderr)
    else:
        summary["models"] = {"sent": 0, "skipped": True}
    # Ticket 177: guarded GC
    if gc_plan:
        gc_results: list[dict[str, Any]] = []
        for provider, keep_ids in gc_plan.items():
            if not keep_ids:
                continue
            gc_res = apply_gc(base_url, provider, keep_ids, auth_headers=auth_headers)
            gc_results.append(gc_res)
            print(f"gc {provider}: {gc_res['count']} deleted", file=sys.stderr)
        summary["gc"] = {"providers": gc_results, "total_deleted": sum(r["count"] for r in gc_results)}
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


def snapshot_gateway_state(
    base_url: str,
    auth_headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Ticket 178: capture pre-apply gateway state for verification + revert."""
    connections = _fetch_existing_connections(base_url, auth_headers, timeout)
    combos = fetch_combos(base_url, auth_headers, timeout)
    models: dict[str, list[dict[str, Any]]] = {}
    for conn in connections:
        provider = conn.get("provider")
        if provider:
            models[provider] = fetch_existing_models(base_url, provider, auth_headers, timeout)
    return {"connections": connections, "combos": combos, "models": models}


def revert_payload(
    base_url: str,
    summary: dict[str, Any],
    snapshot_before: dict[str, Any],
    auth_headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Ticket 178: undo every mutation made by apply_payload using a pre-apply snapshot."""
    httpx = _httpx_client()
    headers = {"Content-Type": "application/json"}
    if auth_headers:
        headers.update(auth_headers)
    base = base_url.rstrip("/")
    revert_summary: dict[str, Any] = {"reverted": [], "errors": []}

    # 1. Reactivate retired connections (PUT isActive=true)
    for retired in summary.get("retire", {}).get("retired", []):
        cid = retired.get("id")
        provider = retired.get("provider")
        if not cid or retired.get("method") != "PUT":
            continue
        put_url = f"{base}/api/providers/{cid}"
        try:
            resp = httpx.put(put_url, json={"isActive": True}, headers=headers, timeout=timeout)
            if resp.status_code in (200, 201, 204):
                revert_summary["reverted"].append({"id": cid, "provider": provider, "action": "reactivate"})
        except Exception as e:
            revert_summary["errors"].append({"id": cid, "error": str(e)})

    # 2. Delete newly created connections (import created them)
    for result in summary.get("import", {}).get("response", {}).get("results", []):
        cid = result.get("id")
        provider = result.get("provider")
        if cid and "status" in result:
            existed = any(c.get("id") == cid for c in snapshot_before.get("connections", []))
            if not existed:
                del_url = f"{base}/api/providers/{cid}"
                try:
                    resp = httpx.delete(del_url, headers=headers, timeout=timeout)
                    if resp.status_code in (200, 201, 204):
                        revert_summary["reverted"].append({"id": cid, "provider": provider, "action": "delete_connection"})
                except Exception as e:
                    revert_summary["errors"].append({"id": cid, "error": str(e)})

    # 3. Restore providerSpecificData patches by comparing snapshots
    snap_conns = {c.get("id"): c for c in snapshot_before.get("connections", [])}
    current_conns = _fetch_existing_connections(base_url, auth_headers, timeout)
    for conn in current_conns:
        cid = conn.get("id")
        old = snap_conns.get(cid)
        if old:
            old_psd = old.get("providerSpecificData", {})
            curr_psd = conn.get("providerSpecificData", {})
            if old_psd != curr_psd:
                put_url = f"{base}/api/providers/{cid}"
                try:
                    resp = httpx.put(put_url, json={"providerSpecificData": old_psd}, headers=headers, timeout=timeout)
                    if resp.status_code in (200, 201, 204):
                        revert_summary["reverted"].append({"id": cid, "provider": conn.get("provider"), "action": "restore_psd"})
                except Exception as e:
                    revert_summary["errors"].append({"id": cid, "error": str(e)})

    # 4. Delete newly created models + restore GC'd models
    snap_models = snapshot_before.get("models", {})
    curr_models: dict[str, list[dict[str, Any]]] = {}
    for conn in _fetch_existing_connections(base_url, auth_headers, timeout):
        provider = conn.get("provider")
        if provider:
            curr_models[provider] = fetch_existing_models(base_url, provider, auth_headers, timeout)

    for provider, curr_list in curr_models.items():
        snap_list = snap_models.get(provider, [])
        snap_ids = {m.get("modelId"): m for m in snap_list}
        curr_ids = {m.get("modelId"): m for m in curr_list}

        for mid, model in curr_ids.items():
            if mid not in snap_ids:
                model_id = model.get("id") or model.get("_id")
                if model_id:
                    del_url = f"{base}/api/provider-models/{model_id}"
                    try:
                        resp = httpx.delete(del_url, headers=headers, timeout=timeout)
                        if resp.status_code in (200, 201, 204):
                            revert_summary["reverted"].append({"provider": provider, "modelId": mid, "action": "delete_model"})
                    except Exception as e:
                        revert_summary["errors"].append({"provider": provider, "modelId": mid, "error": str(e)})

        for mid, model in snap_ids.items():
            if mid not in curr_ids:
                post_url = f"{base}/api/provider-models"
                try:
                    resp = httpx.post(post_url, json=model, headers=headers, timeout=timeout)
                    if resp.status_code in (200, 201, 204):
                        revert_summary["reverted"].append({"provider": provider, "modelId": mid, "action": "restore_model"})
                except Exception as e:
                    revert_summary["errors"].append({"provider": provider, "modelId": mid, "error": str(e)})

    # 5. Delete newly created combos
    snap_combo_names = {c.get("name") for c in snapshot_before.get("combos", [])}
    try:
        current_combos = fetch_combos(base_url, auth_headers, timeout)
        for combo in current_combos:
            name = combo.get("name")
            if name and name not in snap_combo_names:
                cid = combo.get("id") or combo.get("_id")
                if cid:
                    del_url = f"{base}/api/combos/{cid}"
                    try:
                        resp = httpx.delete(del_url, headers=headers, timeout=timeout)
                        if resp.status_code in (200, 201, 204):
                            revert_summary["reverted"].append({"name": name, "action": "delete_combo"})
                    except Exception as e:
                        revert_summary["errors"].append({"name": name, "error": str(e)})
    except Exception as e:
        revert_summary["errors"].append({"scope": "combos", "error": str(e)})

    # 5a. Restore snapshot combos that were overwritten or deleted by apply
    for combo in snapshot_before.get("combos", []):
        post_url = f"{base}/api/combos"
        try:
            resp = httpx.post(post_url, json=combo, headers=headers, timeout=timeout)
            if resp.status_code in (200, 201, 204):
                revert_summary["reverted"].append({"name": combo.get("name"), "action": "restore_combo"})
        except Exception as e:
            revert_summary["errors"].append({"name": combo.get("name"), "error": str(e)})

    revert_summary["total_reverted"] = len(revert_summary["reverted"])
    revert_summary["total_errors"] = len(revert_summary["errors"])
    return revert_summary


def verify_gateway_unchanged(
    base_url: str,
    snapshot: dict[str, Any],
    auth_headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Ticket 178: verify gateway state matches a pre-apply snapshot."""
    current = snapshot_gateway_state(base_url, auth_headers, timeout)

    snap_conns = sorted(snapshot.get("connections", []), key=lambda c: c.get("id", ""))
    curr_conns = sorted(current.get("connections", []), key=lambda c: c.get("id", ""))
    connections_match = snap_conns == curr_conns

    snap_combos = sorted(snapshot.get("combos", []), key=lambda c: c.get("name", ""))
    curr_combos = sorted(current.get("combos", []), key=lambda c: c.get("name", ""))
    combos_match = snap_combos == curr_combos

    models_match = True
    model_diff: dict[str, Any] = {}
    all_model_providers = set(snapshot.get("models", {}).keys()) | set(current.get("models", {}).keys())
    for provider in all_model_providers:
        snap_list = sorted(snapshot.get("models", {}).get(provider, []), key=lambda m: m.get("modelId", ""))
        curr_list = sorted(current.get("models", {}).get(provider, []), key=lambda m: m.get("modelId", ""))
        if snap_list != curr_list:
            models_match = False
            model_diff[provider] = {"before": snap_list, "after": curr_list}

    unchanged = connections_match and combos_match and models_match
    return {
        "unchanged": unchanged,
        "connections_match": connections_match,
        "combos_match": combos_match,
        "models_match": models_match,
        "diff": {
            "connections": {"before": snap_conns, "after": curr_conns} if not connections_match else None,
            "combos": {"before": snap_combos, "after": curr_combos} if not combos_match else None,
            "models": model_diff if not models_match else None,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="omniroute_export", description="OmniRoute export generator â tickets 166 + 167 + 168 + 176 + 177")
    p.add_argument("--dry-run", action="store_true", help="Write files without network (placeholder apiKey)")
    p.add_argument("--apply", action="store_true", help="POST import + models + combos to OmniRoute gateway (requires --omniroute-url reachable)")
    p.add_argument("--omniroute-url", type=str, default=DEFAULT_OMNIROUTE_URL, help="OmniRoute base URL (default http://localhost:20128)")
    p.add_argument("--api-key", type=str, default=None, help="Management API key / dashboard token (or env OMNIROUTE_API_KEY)")
    p.add_argument("--providers", type=Path, default=DEFAULT_PROVIDERS, help="providers.yaml path")
    p.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR, help="data/results dir")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="output dir (default data/derived)")
    p.add_argument("--check", action="store_true", help="Alias for --dry-run validation")
    p.add_argument("--snapshot", type=Path, default=None, help="Capture gateway state to JSON file for later revert/verify")
    p.add_argument("--revert-summary", type=Path, default=None, help="Revert gateway to pre-apply state using saved summary JSON")
    p.add_argument("--verify", type=Path, default=None, help="Verify gateway unchanged against saved snapshot JSON")
    p.add_argument("--revert-snapshot", type=Path, default=None, help="Revert gateway to exact state captured in snapshot JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if load_all_secrets is not None:
        try:
            load_all_secrets()
        except Exception as e:
            print(f"warning: failed to load infisical secrets: {e}", file=sys.stderr)
    if args.apply:
        payload = generate_payload(Path(args.providers), Path(args.results_dir))
        write_payload_files(payload, Path(args.output_dir))
        if not payload.get("import") and not payload.get("models") and not any(c.get("models") for c in payload.get("combos", [])):
            print("warning: payload empty (no providers/combos), nothing to apply", file=sys.stderr)
        redacted = {"import": redact_rows(payload.get("import", [])), "models": payload.get("models", []), "combos": payload.get("combos", []), "gc": payload.get("gc", {}), "meta": payload.get("meta", {})}
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
    if args.snapshot:
        snapshot_path = Path(args.snapshot)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        auth_headers = get_auth_headers(args.api_key)
        snapshot = snapshot_gateway_state(args.omniroute_url, auth_headers=auth_headers or None)
        snapshot_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        print(f"snapshot saved to {snapshot_path}", file=sys.stderr)
        return 0
    if args.revert_summary:
        summary_path = Path(args.revert_summary)
        if not summary_path.exists():
            print(f"summary file not found: {summary_path}", file=sys.stderr)
            return 2
        summary = json.loads(summary_path.read_text())
        if isinstance(summary, dict) and "summary" in summary:
            summary = summary["summary"]
        snapshot_path = summary_path if isinstance(summary_path, Path) else Path(str(summary_path))
        # try to find adjacent snapshot file
        snapshot_file = snapshot_path.with_suffix(".snapshot.json")
        snapshot_before = {}
        if snapshot_file.exists():
            snapshot_before = json.loads(snapshot_file.read_text())
        auth_headers = get_auth_headers(args.api_key)
        revert_summary = revert_payload(args.omniroute_url, summary, snapshot_before, auth_headers=auth_headers or None)
        print(json.dumps(revert_summary, indent=2, sort_keys=True), file=sys.stderr)
        return 0
    if args.revert_snapshot:
        snapshot_path = Path(args.revert_snapshot)
        if not snapshot_path.exists():
            print(f"snapshot file not found: {snapshot_path}", file=sys.stderr)
            return 2
        snapshot_before = json.loads(snapshot_path.read_text())
        # Build a synthetic summary from snapshot (revert to exact snapshot)
        summary = {
            "retire": {"retired": []},
            "import": {"response": {"results": []}},
            "psd_patches": {"patched": []},
            "models": {"upserted": [], "sent": 0},
            "gc": {"providers": [], "total_deleted": 0},
            "combos": {"upserted": []},
        }
        auth_headers = get_auth_headers(args.api_key)
        revert_summary = revert_payload(args.omniroute_url, summary, snapshot_before, auth_headers=auth_headers or None)
        print(json.dumps(revert_summary, indent=2, sort_keys=True), file=sys.stderr)
        return 0
    if args.verify:
        snapshot_path = Path(args.verify)
        if not snapshot_path.exists():
            print(f"snapshot file not found: {snapshot_path}", file=sys.stderr)
            return 2
        snapshot = json.loads(snapshot_path.read_text())
        auth_headers = get_auth_headers(args.api_key)
        result = verify_gateway_unchanged(args.omniroute_url, snapshot, auth_headers=auth_headers or None)
        print(json.dumps(result, indent=2, sort_keys=True), file=sys.stderr)
        if not result.get("unchanged"):
            return 1
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
