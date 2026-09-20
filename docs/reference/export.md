# OmniRoute export

Reference for `export dry-run|apply`: pinning every keep to OmniRoute tier combos.

Single command pins every keep to an OmniRoute tier combo (`flash`/`max`/`contributor_free`, keep-all, `auto` strategy). Only tiers with at least one keep get a combo; an empty tier's stale gateway combo is deleted on apply. Source of truth stays `config/providers.yaml` + `data/results/*.yaml`; secrets never inline.

```bash
# Dry-run - writes files only, no network
llm-discovery export dry-run
cat data/derived/omniroute_import.json        # [{provider,name,apiKey:"env:SECRET",baseUrl}]
cat data/derived/omniroute_combos.json        # [{name,models:[{provider,model}],strategy:"auto"}]
# Example fixture committed for shape reference
cat data/derived/examples/omniroute_combos.example.json

# Apply - resolves env secrets and POSTs to gateway (idempotent)
# Requires OmniRoute reachable at localhost:20128 and env keys (GROQ_API_KEY, etc.)
# Auth via env OMNIROUTE_API_KEY or --api-key, fallback to unauthenticated local gateway
llm-discovery export apply --gateway-url http://localhost:20128
# Verify
curl -s http://localhost:20128/api/combos | jq    # shows only non-empty tier combos, strategy "auto"
curl -s http://localhost:20128/v1/chat/completions -H "Content-Type: application/json" \
  -d '{"model":"flash","messages":[{"role":"user","content":"hi"}]}' | jq
```

- Import: one row per `config/providers.yaml`, `baseUrl` verbatim, `apiKey` resolved from env only at apply.
- Combos: pure tier partition keep-all (e.g. 100 keeps → 100 targets), `contributor_special` normalized, strict `contributor` filter, sorted deterministic.
- CLI: `dry-run` (local only), `apply` (bulk import + `GET`/`POST`/`PUT /api/combos` upsert), `--gateway-url`, `--api-key` (prefer env: argv leaks into `ps` and shell history); exit 0 success, non-zero on validation; no secrets logged.
