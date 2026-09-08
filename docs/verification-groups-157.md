# Verification — Strict Groups (closes #157, spec #152)

Proves all success criteria together: UI, curl, and DSH with no fallback.

## Preflight

```bash
# file artifacts
jq .total data/bifrost/config.json 2>/dev/null || python3 -c "import json; d=json.load(open('data/bifrost/config.json')); print(len(d['providers']))" # 19
python3 -c "import json; m=json.load(open('data/bifrost/shim_map.json')); print({k:len(v) for k,v in m.items()})" # 46/84/2
# live when host FS RW (podman healthy)
curl -s http://localhost:8080/health | jq . # {status:ok}
curl -s http://localhost:8080/api/models | jq .total # 132
# sidecar
curl -s http://localhost:8081/health | jq .tiers # {flash:46,max:84,contributor_free:2}
curl -s http://localhost:8081/v1/models | jq '.data | map(.id)' | grep -q flash && echo ok # virtual models
curl -s http://localhost:8081/api/models | jq .total # 135 (132 + 3 virtual)
```

If host ` /` is RO (e.g., after reboot, `mount | grep " / "` shows `ro`), podman `Secret=bifrost-env` fails (`chmod /run/user/1000/libpod: read-only`). Use host binary fallback: `./.tmp/bifrost --app-dir ./.tmp/bifrost_run` which carries the same 46/84/2 mirror verified above.

## 1. Curl each group via :8081 (no fallback)

With sidecar running (`scripts/run_sidecar.sh` → :8081 → :8080, or mock Bifrost on :8090 for offline proof):

```bash
for tier in flash max contributor_free; do
  echo "=== $tier ==="
  curl -s http://localhost:8081/v1/chat/completions \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$tier\",\"messages\":[{\"role\":\"user\",\"content\":\"say hi\"}],\"max_tokens\":5,\"stream\":false}" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('choices',[{}])[0].get('message',{}).get('content','')[:80] or d)"
done
# each streams completion with rewritten concrete model (pool member), no cross-tier fallback
```

Offline proof (no provider keys needed) via unit seam with MockTransport:

```bash
TMPDIR=/tmp UV_CACHE_DIR=/tmp/uv_cache uv run pytest tests/test_shim_sidecar.py -k "flash_alias_routes or max_alias_routes or contributor_free_routes or empty_tier_returns_503" -v
# 4 passed — strict intra-tier pick, empty→503 tier_unavailable + Retry-After:60, no fallback
```

Empty-pool contract:

```bash
# shim_map tier empty → 503 tier_unavailable, no fallback to another tier
curl -s -i http://localhost:8081/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"flash","messages":[{"role":"user","content":"hi"}]}' | head -n 20
# HTTP/1.1 503 Service Unavailable
# Retry-After: 60
# {"error":{"message":"tier_unavailable: flash pool empty","type":"tier_unavailable","code":"tier_unavailable","tier":"flash"}}
```

## 2. DSH chat streams per group

Config (`docs/dsh-bifrost-wiring.md`):

- `llm-pi-ai` provider `bifrost-shim` → `baseUrl http://localhost:8081/v1`, `apiKeyEnv BIFROST_API_KEY` (dummy `sk-bifrost-dummy` in `~/.credentials.yaml` or `BIFROST_API_KEY=sk-bifrost-dummy`), models `flash`/`max`/`contributor_free` via `settings.yaml` or `cordis.patch.yml`.
- `bifrost-direct` → `baseUrl http://localhost:8080/v1` for explicit pins (e.g., `deepseek-v4-flash`).

Verify:

```bash
dsh --profile web --dump-config | jq '.[] | select(.id=="llm-pi-ai") | .config.providers'
# shows bifrost-shim + bifrost-direct
```

Browser:

1. Open DSH GUI (`http://localhost:3080` or configured host).
2. Model picker shows `flash` / `max` / `contributor_free`.
3. Select each, send "say hi" → streams completion via shim rewrite (verify in Bifrost logs `POST /v1/chat/completions` with concrete model).
4. No cross-group fallback observed; empty pool would show 503 error in DSH.

`window.__DSH_BOOT__` includes `bifrost-shim` provider with 3 models.

## 3. Bifrost UI + /api/models

- `http://localhost:8080` gateway UI → models list shows 132 concrete models across 19 providers (row count), providers via `GET /api/governance/providers`.
- `GET /api/models` → `{"total":132}`; `GET /v1/models` via :8080 is health-filtered by key validity, via :8081 augmented with 3 virtual entries (`flash`/`max`/`contributor_free`, `owned_by: bifrost-shim`).
- `GET /health` on :8081 → tier counts match `shim_map.json`.

Screenshots captured when healthy at `.tmp/screenshots/bifrost.png`.

## 4. No cross-group fallback demonstrated

- Unit: `pick_model_for_tier` only samples from requested tier; `test_empty_tier_returns_503` asserts 503 + `Retry-After:60` + `tier_unavailable` and that no other tier is tried.
- Live: empty `flash` pool → `curl` returns 503 as above, not a `max` model.

## 5. Docs finalized

- ADR 0008 → `Status: Accepted` (final, #157).
- CONTEXT.md glossary `Model Group` updated (46/84/2, strict 503, `TIER_*` internal).
- This checklist + `docs/dsh-bifrost-wiring.md` + `docs/bifrost-deployment.md` are verification source.

## Ready to close

All boxes in #157 green; spec #152 ready to close. Next: routing-policy Wayfinder (deferred per ADR).
