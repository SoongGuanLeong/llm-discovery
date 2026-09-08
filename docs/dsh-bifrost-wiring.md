# DSH Wiring to Bifrost Model Groups

Connects DSH (`llm-pi-ai` adapter) to Bifrost Model Groups (`flash` / `max` / `contributor_free`)
via the shim sidecar. Covers configuration, credentials, and verification.

## Architecture

```text
DSH (window.__DSH_BOOT__) --pi-ai--> Shim sidecar :8081 --proxy--> Bifrost :8080 --> provider base_url
DSH (window.__DSH_BOOT__) --pi-ai--> Bifrost :8080 (direct, explicit model pin)
```

- Bifrost gateway on `localhost:8080` serves OpenAI-compatible `/v1/chat/completions` and `/v1/models`.
  File-only config from `data/bifrost/config.json` + `data/bifrost/shim_map.json`.
- Shim sidecar (`src/llm_discovery/bifrost/sidecar.py` via `scripts/run_sidecar.sh`) on `localhost:8081`
  rewrites `model: flash|max|contributor_free` to a uniform pick from `shim_map.json` (keep-all,
  strict `contributor` substring for `contributor_free`, pools 46/84/2) and proxies to `:8080`.
  Empty pool returns HTTP 503 `tier_unavailable` with `Retry-After: 60`, no cross-group fallback.
- DSH resolves provider keys via `apiKeyEnv` indirection; Bifrost resolves real provider keys
  from `~/.config/bifrost/bifrost.env` (0600, `env.VAR` refs in `config.json`). No provider key ever
  lives in DSH config.

## Prerequisites

- Bifrost healthy on `:8080` (see `docs/bifrost-deployment.md`)
- Shim map generated: `data/bifrost/shim_map.json` with 46/84/2
- Shim sidecar runnable: `scripts/run_sidecar.sh` -> `:8081`

Verify before wiring DSH:

```bash
curl -s http://localhost:8080/health | jq .
curl -s http://localhost:8080/api/models | jq .total       # expect 132
curl -s http://localhost:8081/health | jq .                # expect tiers 46/84/2
```

If `:8081` is down, start it:

```bash
./scripts/run_sidecar.sh
# or
SHIM_MAP_PATH=data/bifrost/shim_map.json BIFROST_URL=http://localhost:8080 uv run python -m llm_discovery.bifrost.sidecar
```

## DSH Configuration

Two equivalent paths. Pick one.

### Path A: settings.yaml (hot-reloaded, no rebuild)

Merge `config/dsh/settings.yaml.example` into `$DSH_HOME/settings.yaml`:

```yaml
llm-pi-ai:
  providers:
    bifrost-shim:
      apiKeyEnv: BIFROST_API_KEY
      api: openai-completions
      baseUrl: http://localhost:8081/v1
      models:
        - id: flash
          name: "Flash (tier, 46 models)"
        - id: max
          name: "Max (tier, 84 models)"
        - id: contributor_free
          name: "Contributor Free (2 models)"
    bifrost-direct:
      apiKeyEnv: BIFROST_API_KEY
      api: openai-completions
      baseUrl: http://localhost:8080/v1
      models:
        - id: deepseek-v4-flash
          name: "deepseek-v4-flash (direct)"
```

File is hot-reloaded by DSH; no restart required.

### Path B: cordis.patch.yml overlay (profile-level, versioned)

Copy `config/dsh/cordis.patch.yml.example` to `$DSH_HOME/profiles/web/cordis.patch.yml`
or apply via `--patch`:

```yaml
- id: llm-pi-ai
  config:
    providers:
      bifrost-shim:
        apiKeyEnv: BIFROST_API_KEY
        api: openai-completions
        baseUrl: http://localhost:8081/v1
        models:
          - id: flash
            name: "Flash (tier alias → 46 models)"
          - id: max
            name: "Max (tier alias → 84 models)"
          - id: contributor_free
            name: "Contributor Free (2 models)"
      bifrost-direct:
        apiKeyEnv: BIFROST_API_KEY
        api: openai-completions
        baseUrl: http://localhost:8080/v1
        models:
          - id: deepseek-v4-flash
            name: "deepseek-v4-flash (direct pin)"
          - id: kimi-k3
            name: "kimi-k3 (direct pin)"
```

Inspect composed tree without booting:

```bash
dsh --profile web --dump-config | jq '.[] | select(.id=="llm-pi-ai")'
```

## Credentials

DSH requires a non-empty key for the OpenAI-compatible client, but Bifrost ignores
the client key and authenticates to each provider with its own `env.VAR` keys.

Add a dummy to `$DSH_HOME/.credentials.yaml`:

```yaml
BIFROST_API_KEY: sk-bifrost-dummy
```

or export it:

```bash
export BIFROST_API_KEY=sk-bifrost-dummy
```

Real provider keys stay only in `~/.config/bifrost/bifrost.env` (0600) and are
referenced as `env.VAR` in `data/bifrost/config.json`. No secret is logged
or committed.

## Verification

### 1. Config check

```bash
# settings.yaml path
grep -A2 bifrost-shim ~/.dsh/settings.yaml
# cordis.patch.yml path
cat ~/.dsh/profiles/web/cordis.patch.yml
# dump-config includes bifrost providers
dsh --profile web --dump-config | grep -q bifrost-shim && echo "ok"
```

### 2. window.__DSH_BOOT__ (browser)

DSH web composes the Cordis tree and injects `window.__DSH_BOOT__` via `/plugins/<id>/client.js`.

```bash
dsh web --port 3080 &
curl -s http://localhost:3080/ | grep -o "__DSH_BOOT__[^<]*" | head
```

In DevTools console:

```js
window.__DSH_BOOT__.plugins['llm-pi-ai']
// expect providers.bifrost-shim.baseUrl == "http://localhost:8081/v1"
```

Note: client-plugin HMR reloads without refresh only while `pnpm run dev:web` is rebuilding.
All other changes require rebuilding artifacts and a page refresh.

### 3. Curl through shim (no DSH)

```bash
export BIFROST_API_KEY=sk-bifrost-dummy
for tier in flash max contributor_free; do
  echo "=== $tier ==="
  curl -s http://localhost:8081/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $BIFROST_API_KEY" \
    -d "{\"model\":\"$tier\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"temperature\":0}" | jq .
done
# explicit pin bypasses alias
curl -s http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $BIFROST_API_KEY" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"hi"}]}' | jq .
```

Expect 200 per tier, with shim rewriting `model` to a pool member. Empty pool returns
503 `tier_unavailable` with `Retry-After: 60` and no fallback.

Streaming check: add `"stream": true` and verify `data: {"choices":[{"delta":{"content":"..."}}]}` chunks.

### 4. DSH chat end-to-end

1. Start DSH with wiring above.
2. Open `http://localhost:3080` -> Settings -> Models shows `bifrost-shim` / `bifrost-direct`.
3. New chat -> model picker shows `flash` / `max` / `contributor_free` -> send "hi".
4. Completion streams via shim -> Bifrost -> provider. Check provider in `data/bifrost/logs.db`.
5. No cross-group fallback is observable; upstream 503 surfaces as `tier_unavailable`.

## Model Mapping Reference

| DSH model id | Shim behavior | Bifrost path |
|--------------|---------------|--------------|
| `flash` | uniform pick from 46 flash pool | `POST $BIFROST_URL/v1/chat/completions` with rewritten model |
| `max` | uniform pick from 84 max pool | same |
| `contributor_free` | pick from 2 contributor pool | same |
| explicit pin e.g. `deepseek-v4-flash` | proxy as-is, no rewrite | `http://localhost:8080/v1/chat/completions` |

## Troubleshooting

- `401` from provider: check `~/.config/bifrost/bifrost.env` has valid key for that provider,
  then `uv run python scripts/generate-bifrost-config.py` + `systemctl --user restart bifrost`.
- Shim not reachable: `curl http://localhost:8081/health` should show tiers; restart via `./scripts/run_sidecar.sh`.
- DSH not seeing providers: check `dsh --profile web --dump-config` includes `bifrost-shim`,
  and `window.__DSH_BOOT__` in DevTools; rebuild web artifacts if needed.
- Port conflict: `ss -ltnp | grep -E "8080|8081|3080"`.

## Related

- `docs/bifrost-deployment.md` — Bifrost gateway setup and file layout
- `config/dsh/cordis.patch.yml.example`, `config/dsh/settings.yaml.example` — copy-paste configs
- ADR 0008 — shim+strict-groups decision and alternatives
