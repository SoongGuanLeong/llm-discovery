# Research: Bifrost config schema and tier routing for flash/max/contributor_free (issue #110)

Part of #109 — Wayfinder Map: Bifrost local gateway for 3 tier endpoints from data/results (Podman Quadlet evaluation).

## Question

How to map 126 keep records (tiers flash/max/contributor_free) in `data/results/*.yaml` to 3 logical Bifrost endpoints? Deliver: recommended config shape (file-only vs DB-backed, providers block, virtual keys or model aliases), example JSON covering 3 tiers, weight/fallback behavior, and constraints for local use.

## Method

Primary sources only:
- https://docs.getbifrost.ai (overview, quickstart/gateway/setting-up, quickstart/gateway/provider-configuration, providers/provider-routing, features/governance/virtual-keys, features/governance/routing, features/retries-and-fallbacks, features/keys-management, deployment-guides/config-json/source-of-truth, architecture/framework/model-catalog)
- https://www.getbifrost.ai/schema (config.json JSON Schema)
- https://github.com/maximhq/bifrost (README, repo structure, config.json placeholder)
- Local: `data/results/*.yaml` (23 providers, sampled agnes, nararouter, openrouter, opencode_zen, zai), `config/providers.yaml`, `src/llm_discovery/categorize.py`, `src/llm_discovery/results.py`, `docs/adr/*`

No issue-tracker comments fetched (gh auth unavailable); ticket bodies 109/110 read via web fetch 2026-09-05.

## Findings

### 1. Local data shape — what must be mapped

**Counts (2026-09-05 run, `python - << 'PY'` over `data/results/*.yaml`):**
- 126 keeps total = flash 46 + max 77 + contributor_free 3 across 23 providers.
- Per-provider keeps >0: agnes 4, ainative 30, bazaarlink 1, cerebras 1, cloudflare 8, google 6, groq 1, kilo_ai 8, llm7 22, mistral 2, modelscope 14, nararouter 6, navy_ai 14, opencode_zen 3, openrouter 6. zai and others 0 (auth/empty).

**Per-record schema (`src/llm_discovery/results.py:ProviderBatchWriter._to_record`):**
```yaml
provider: nararouter
evaluated_at: '2026-09-05T08:40:51.909956+00:00'
keep:
- model_id: minimax-m3-free          # raw, may contain /, :, version dots preserved
  decision: keep
  tier: max | flash | contributor_free  # from src/llm_discovery/categorize.py
  aa_model_id: 277f939a-...           # may be null if supplement bench >=50
  aa_score: 45.4
  coding_score: 65.53                 # null fails Accurate-Enough Gate, so all 126 have non-null
  pricing: {blended, input, output, per_provider_overrides, price_1m_...} | null
  benchmarks: {scores, raw_benchmarks, benchmark_coverage >=0.25}
  confidence: 0.33
  evidence_level: strong
  evidence: [http URLs...]
  coding_assessment: {is_coding, confidence, reason, coding_score, aa_score} | null
drop_llm: [...]
error: [...]
```

**Tier definition (`src/llm_discovery/categorize.py`):**
- `contributor_free`: `if "contributor" in model_id.lower()` — deterministic, regardless of score. All 3 contributor_free are `muse-spark-1.2/1.3-contributor-free` (nararouter, opencode_zen duplicates).
- `max` vs `flash`: derived from `coding_score` (>=65 → max, >=35 → flash), fallback to `aa_score` (>=45 → max, >=24 → flash), plus flagship boost (pro/ultra/opus in tokens) and pricing-aware demotion (cheap high-value max → flash at blended <=0.2, <=0.35 with value>=85, <=0.6 with value>=130). `value = intelligence/denom` where intelligence = coding_score or aa_score*100/63.

**Implication for Bifrost:** `model_id` is not stable across providers — same base model appears as `minimax-m3-free`, `minimax/minimax-m3:free`, `minimax-m3:free` variants. Bifrost routing must normalize or enumerate aliases. `"contributor" in id` is sufficient for tier filter.

### 2. Bifrost config.json schema — providers / keys / virtual keys

**Source:** https://www.getbifrost.ai/schema + docs.getbifrost.ai/quickstart/gateway/*

Top-level keys relevant to local gateway:
```json
{
  "$schema": "https://www.getbifrost.ai/schema",
  "version": 2,
  "source_of_truth": "split",
  "client": {"drop_excess_requests": false, "enforce_auth_on_inference": false},
  "providers": {
    "<provider>": {
      "keys": [{"name": "...", "value": "env.X_API_KEY", "models": ["*"], "weight": 1.0, "blacklisted_models": []}],
      "network_config": {"base_url": "https://api.openai.com/v1", "max_retries": 3, "retry_backoff_initial": 500, "retry_backoff_max": 5000, "allow_private_network": false},
      "custom_provider_config": {"base_provider_type": "openai", "allowed_requests": {"chat_completion": true}}
    }
  },
  "governance": {"virtual_keys": [{"id": "vk-..", "name": "...", "value": "sk-bf-...", "provider_configs": [{"provider": "openai", "weight": 0.5, "allowed_models": ["gpt-4o"], "key_ids": ["*"]}]}]},
  "config_store": {"enabled": true, "type": "sqlite", "config": {"path": "./config.db"}},
  "mcp": {},
  "plugins": []
}
```

**Providers block detail (per provider):**
- `keys[].models`: allowlist, `["*"]` = catalog-driven (Bifrost calls `GetModelsForProvider`), explicit list = strict, `[]` = deny all (v1.5+ semantics; `version: 2` makes empty = deny). `blacklisted_models` denylist wins over allowlist.
- `keys[].weight`: weighted random selection per request (keys-management: total weight sum, random 0..total). Higher weight → more traffic, useful for rate-limit tiers.
- `keys[].value`: `env.VAR` dereference or literal; UI redacts.
- `network_config.base_url`: override for OpenAI-compatible providers (vLLM, Ollama, local). Required for self-hosted; optional for standard providers. `allow_private_network: true` needed for 192.168/10.x.
- `custom_provider_config.base_provider_type: "openai"`: for any OpenAI-compatible endpoint not in built-in list (covers all llm-discovery providers: agnes, ainative, bazaarlink etc.). One custom provider per llm-discovery provider name.

**Governance / Virtual Keys (features/governance/virtual-keys):**
- Primary governance entity, auth via `x-bf-vk: sk-bf-*` (also Authorization Bearer, x-api-key, x-goog-api-key aliases). Scopes: model/provider filtering, budgets, rate limits, team/customer attachment.
- `provider_configs[]`: per-VK routing — array of `{provider, weight, allowed_models, key_ids}`. `allowed_models: ["*"]` = all from catalog; explicit list = restrict. `key_ids: ["*"]` = any key for that provider; `[]` = deny; named IDs restrict to specific keys.
- `budgets`/`rate_limits`: separate objects referenced by VK, hierarchical (VK, team, customer). Not needed for local unauthenticated use; can omit or set high limits.
- `team_id`/`customer_id`: mutually exclusive; omit for local.

**Model Catalog (providers/provider-routing, architecture/framework/model-catalog):**
- Two sources: pricing datasheet (`https://getbifrost.ai/datasheet`, 24h refresh) + provider `/v1/models` (startup + on provider add/update + manual refetch). In-memory `modelPool[provider]` and `pricingData[model|provider|mode]`.
- Routing helpers: `GetModelsForProvider(provider)` and `GetProvidersForModel(model)` with cross-provider rules (OpenRouter `provider/model`, Vertex, Groq `openai/model`, Bedrock `anthropic.` prefix).
- Pricing lookup fallback chain: `model|provider|chat` → Gemini→Vertex → strip provider/model prefix → Bedrock prefix → Responses→Chat.

**Config modes (quickstart/gateway/setting-up#configuration-modes, deployment-guides/config-json/source-of-truth):**

| Setup | config_store | UI/API | Startup behavior |
|-------|-------------|--------|-------------------|
| No config.json | SQLite config.db default | Enabled | Defaults + DB |
| config.json, config_store omitted | SQLite default | Enabled | File reconciled into DB via content hash (split) |
| config.json + config_store.enabled:true | SQLite/Postgres explicit | Enabled | File reconciled into explicit store |
| config.json + config_store.enabled:false | Disabled | Unavailable | File loaded into memory, restart required, read-only |

`source_of_truth` only matters DB-backed:
- `"split"` (default, recommended for interactive): file bootstraps DB on first run; later, unchanged file entities preserve DB edits (UI/API); changed file entity overwrites DB for that entity only. Missing file sections leave DB rows untouched (no prune).
- `"config.json"`: present file sections authoritative, overwrite DB even if hash matches; missing vs empty distinction — missing leaves DB, empty `[]` prunes matching DB rows. For strict GitOps.

### 3. Tier routing — 3 logical endpoints design

**Requirement:** Expose `flash`, `max`, `contributor_free` as OpenAI-compatible model names that fan out to many backing providers/models (46/77/3 records, many duplicates across providers).

**Bifrost capability gap:** No native "model alias" type. `allowed_models` is a filter, not an alias map. Bifrost model name is the literal `"model"` string in `/v1/chat/completions`. Routing resolves via `GetProvidersForModel` against catalog + keys[].models.

**Three viable patterns evaluated:**

#### Pattern A — Custom provider per tier with explicit allowed_models (RECOMMENDED for local file-only)

Register three logical providers (or three custom provider entries) that are themselves the tiers, OR register all 15 real providers but expose tiers as virtual-model names via provider model allowlists. Simplest file-only implementation:

- Create three "tier providers" as OpenAI-compatible shims? Not ideal — would require a local aggregator.

Instead, RECOMMENDED is: **single Bifrost deployment with 15 real providers + 3 virtual keys (or direct model allowlisting), consumers request `model: "flash"` (or `model: "max"`) and Bifrost weighted-routes via virtual-key provider_configs.**

But since Bifrost requires model names to exist in catalog or explicit allowlist, bare `"flash"` would be rejected unless explicitly allowlisted. The fix: **declare tiers as first-class models via explicit allowlists on keys and virtual keys, bypassing catalog lookup.**

Flow:
1. Client POSTs to `http://localhost:8080/v1/chat/completions` with `model: "flash"` (or "max", "contributor_free") and header `x-bf-vk: sk-bf-flash` (if using VKs) or no auth if `enforce_auth_on_inference: false`.
2. Bifrost checks VK's `provider_configs[].allowed_models` — if `"flash"` in list, allowed.
3. Then resolves providers that support "flash" — but no provider natively advertises "flash". Workaround: use **catch-all key with `models: ["flash", "max", "contributor_free"]` on each real provider via custom_provider_config**. Bifrost skips catalog when explicit allowlist present. So any key with explicit list allows that model regardless of catalog.

**Therefore the tier-to-backing mapping must be implemented outside Bifrost's catalog:** a generation script translates `data/results/*.yaml keep` into explicit `models` arrays per key, where "model id" = tier name, not original keep id. Each real provider key's `models` array contains only the tiers it participates in, but the actual upstream model to call must be selected per request.

Bifrost does NOT auto-select among backing keep model_ids for a tier — it selects among **keys/providers**, not models. The request's `model` field chooses one model, not a pool. To achieve "any model in tier", we need one of:

- **(A1) Round-robin via weight:** Register one key per backing model_id, each key's `models` = that single model_id, and virtual key's `provider_configs` weight distributes. Client still must request specific model_id, not tier. Not tier abstraction.
- **(A2) Fallback chain per tier (CLIENT-DRIVEN):** Client sets `model: "tier-flash-primary"` + `fallbacks: ["provider/model2", ...]` in request body (features/retries-and-fallbacks). Bifrost sequentially tries fallbacks with full retry budget. Good for resiliency but requires client to know fallback list; can be injected by a thin proxy.
- **(A3) Server-side weighted provider pool behind a logical model name (IDEAL but needs plugin/custom routing):** Requires a Bifrost plugin or an intermediate OpenAI-compatible shim that rewrites `model: "flash"` to a weighted choice among actual backing model_ids before Bifrost routing. Bifrost core does not do this alone.

**Practical recommendation for ticket acceptance without plugin:**

- **Phase 1 (no plugin, file-only, minimal):** Expose providers directly plus 3 **virtual keys** that restrict to tier members via explicit `allowed_models` lists of *original keep model_ids*. Consumers request a real model_id (e.g. `model: "openai/gpt-4o-mini"` or `model: "minimax-m3-free"`) but must use tier-specific VK. The tier is enforced by auth header, not model name. Endpoint count = 3 VKs, not 3 model names.

- **Phase 2 (ergonomic tier aliases, requires small shim):** Add a lightweight local proxy (Python sidecar or Bifrost WASM plugin) that maps `model: "flash"` → weighted random pick from that tier's backing model_ids, then forwards to Bifrost with real model. Bifrost still handles weight/key-level load balancing + retries.

Or, if strictly 3 OpenAI model names without shim, use **custom provider per tier** where `base_url` points to a local aggregator (e.g., litellm or a second Bifrost) that does the weighted pick. But simplest decision-ready answer: **deliver 3 virtual keys + explicit allowlists as Phase 1; document shim as Phase 2 for true alias semantics.**

**Virtual Keys vs Model Aliases trade-off:**
- Virtual Keys: governance primitive, already supports weight + allowed_models + key_ids; fits Bifrost's design (docs explicitly route via VK). Downside: client must send x-bf-vk header, not just model name. But Bifrost allows `Authorization: Bearer sk-bf-...` so OpenAI SDK's `api_key` = VK value works.
- Model aliases: not native; would require either catalog spoofing (price datasheet override) or custom provider that declares `models: ["flash"]` but still needs to know which upstream model to call — Bifrost forwards the same model string upstream, so upstream would receive `"flash"` which no provider understands.

**Conclusion:** Recommend **virtual keys as tier endpoints** for immediate implementation. Document why true model-alias tiers need a pre-Bifrost rewrite layer.

### 4. Weight / fallback / retry semantics within a tier

**Weight (providers/provider-configuration#weighted-load-balancing, features/keys-management):**
- Per-key `weight`: floating, weighted random per request. Example: two OpenAI keys weight 0.7/0.3 → 70%/30%. Sum = total. Same for VK `provider_configs[].weight`.
- Recommendation: uniform weight (1.0) per backing provider initially; later tune by `intelligence per dollar` or rate-limit capacity (premium keys higher weight). For flash vs max, pricing-aware intelligence/value already biased tiering; weighting can reinforce it.

**Retries (features/retries-and-fallbacks):**
- Per-provider `network_config.max_retries` (default 0, recommend 3), `retry_backoff_initial` 500ms, `retry_backoff_max` 5000ms, exponential with jitter 0.8–1.2.
- Per-key rotation: on 429/401/402/403 Bifrost rotates to next key in pool (429 still backs off, 401/402/403 marked permanently dead, no backoff). Needs `max_retries>0` + >1 key.
- Within-tier implication: configure at least 2 keys per heavily used provider (e.g., nararouter, ainative) to benefit from rotation.

**Fallbacks (features/retries-and-fallbacks#fallbacks):**
- Client-driven array: `"fallbacks": ["anthropic/claude-3-5-sonnet-20241022", "bedrock/anthropic.claude-3-sonnet-20240229-v1:0"]` in request body. Each fallback gets its own retry budget. Sequential, first success wins, plugins re-run.
- For tier abstraction, fallbacks provide cross-provider failover: a flash request listing 46 backing models as fallbacks would be too large for header/body but Bifrost supports array of any length. Shim can inject tier fallback list server-side.
- Alternative: governance routing order respects VK `provider_configs` weights; fallback via request body is explicit and observable via `extra_fields.provider`.

**Proposed behavior per tier:**
- flash: 46 models, prefer cheap high-throughput providers (nararouter, openrouter, ainative). Weight uniform, max_retries 3, retry fallback chain of 3–5 cheapest strong models.
- max: 77 models, weighted slightly toward flagship + higher value (ainative, modelscope, navy_ai). Same retry config.
- contributor_free: 3 records but only 2 unique model_ids (muse-spark-1.2 duplicate). No LB needed; weight 1.0, fallback between nararouter and opencode_zen duplicates.

### 5. Recommended config shape — decision-ready

**RECOMMENDED for local Podman Quadlet use: file-only (`config_store.enabled: false`) with generated `config.json`, declarative, gitignored artifact, restart-on-change.** Rationale versus DB-backed:

| Dimension | File-only (RECOMMENDED) | DB-backed (alternative) |
|-----------|------------------------|------------------------|
| Startup | Memory-only, no SQLite, simplest container volume: `-v ./bifrost-data:/app/data` with app-dir = /app/data, single file `config.json` | Requires SQLite config.db + logs.db in volume, reconciliation logic |
| Edit flow | Regenerate `config.json` from `data/results/*.yaml` via script (`scripts/generate-bifrost-config.py`), then `systemctl --user restart bifrost` or Quadlet auto-reload (watch) | Allows live UI/API edits without restart, but hash drift risk; `source_of_truth: split` preserves edits unless file changes |
| GitOps | Single artifact, no DB to back up; diffable; `config.json` gitignored, regenerated on build | DB not committed; rebuilding after DB edit requires export |
| Local constraints | No Postgres, no encryption_key required, no migration | Needs encryption_key if secrets encrypted |
| Observability | Logs via stdout + logs.db optional | Same, but UI shows governance state |

DB-backed is useful if operator wants Web UI tuning without regenerating file. For this map (decision before prototype), recommend **file-only for determinism, with option to flip to DB-backed `split` later by toggling `config_store.enabled: true` without changing generated providers/governance.**

**Providers block shape:** One provider entry per llm-discovery provider name (15 active + stubs for empty). Each with:
- `keys: [{name, value: env.PROVIDER_API_KEY, models: ["*"] or explicit tier lists, weight:1.0}]`
- `network_config: {base_url: "https://api.<provider>/v1", default_request_timeout_in_seconds: 60, max_retries:3, retry_backoff_initial:500, retry_backoff_max:5000}`
- `custom_provider_config: {base_provider_type: "openai"}` for all except native openai/anthropic-style if ever added.

Note: llm-discovery `config/providers.yaml` already enumerates base_url and secret env var per provider. Bifrost generation must translate `secret: AGNES_AI_API_KEY` → `value: "env.AGNES_AI_API_KEY"`, and pass through base_url.

**Virtual keys (tier routing) — if allowed (requires DB-backed for VK storage) vs alternative direct-model tier aliases:**

- **If DB-backed:** Create 3 VKs: `vk-flash`, `vk-max`, `vk-contributor_free` each with `provider_configs` enumerating allowed providers/models for that tier (see example JSON below). Consumers call with `x-bf-vk: <tier-vk>` + `model: <any model in allowed list>`.

- **If file-only without VK support in config.json schema (governance via file):** `config.json` schema does support `"governance": {"virtual_keys": [...]}` in file-only mode (per schema docs). So file-only can still declare VKs declaratively. However, file-only disables UI/API edits to VKs. Either is valid; recommend declaring VKs in file for declarative tiers.

If true model alias (`model: "flash"`) desired without VK header, add shim: a tiny OpenAI-compatible proxy at `:8081` that rewrites `model` field to a sampled backing id before forwarding to Bifrost `:8080`. This satisfies "3 endpoints" as 3 URLs or 3 model strings without client sending VK.

**Example JSON (Phase 1 — file-only + 3 virtual keys, truncated to 3 tiers with real sample keep ids):**

```json
{
  "$schema": "https://www.getbifrost.ai/schema",
  "version": 2,
  "source_of_truth": "split",
  "client": {
    "enforce_auth_on_inference": false,
    "drop_excess_requests": false,
    "enable_logging": true
  },
  "providers": {
    "agnes": {
      "keys": [{"name": "agnes-key-1", "value": "env.AGNES_AI_API_KEY", "models": ["agnes-2.5-flash", "agnes-2.5-pro", "agnes-2.5-pro-alpha", "agnes-2.5-pro-beta"], "weight": 1.0}],
      "network_config": {"base_url": "https://apihub.agnes-ai.com/v1", "max_retries": 3, "retry_backoff_initial": 500, "retry_backoff_max": 5000},
      "custom_provider_config": {"base_provider_type": "openai"}
    },
    "ainative": {
      "keys": [{"name": "ainative-key-1", "value": "env.AINATIVE_API_KEY", "models": ["*"], "weight": 1.0}],
      "network_config": {"base_url": "https://api.ainative.studio/api/v1", "max_retries": 3},
      "custom_provider_config": {"base_provider_type": "openai"}
    },
    "nararouter": {
      "keys": [{"name": "nararouter-key-1", "value": "env.NARAROUTER_API_KEY", "models": ["laguna-s-2.1", "longcat-2.0-free", "minimax-m3-free", "muse-spark-1.2-contributor-free", "qwen3.8-27b", "step-3.7-flash"], "weight": 1.0}],
      "network_config": {"base_url": "https://router.bynara.id/v1", "max_retries": 3},
      "custom_provider_config": {"base_provider_type": "openai"}
    },
    "openrouter": {
      "keys": [{"name": "openrouter-key-1", "value": "env.OPENROUTER_API_KEY", "models": ["minimax/minimax-m2.7:free", "minimax/minimax-m3:free", "nvidia/nemotron-3-ultra-550b-a55b:free", "poolside/laguna-s-2.1:free"], "weight": 1.0}],
      "network_config": {"base_url": "https://openrouter.ai/api/v1", "max_retries": 3},
      "custom_provider_config": {"base_provider_type": "openai"}
    },
    "opencode_zen": {
      "keys": [{"name": "opencode_zen-key-1", "value": "env.OPENCODE_ZEN_API_KEY", "models": ["deepseek-v4-flash-free", "muse-spark-1.2-contributor-free", "muse-spark-1.3-contributor-free"], "weight": 1.0}],
      "network_config": {"base_url": "https://opencode.ai/zen/v1", "max_retries": 3},
      "custom_provider_config": {"base_provider_type": "openai"}
    },
    "zai": {
      "keys": [{"name": "zai-key-1", "value": "env.ZAI_API_KEY", "models": ["*"], "weight": 1.0}],
      "network_config": {"base_url": "https://api.z.ai/api/paas/v4", "max_retries": 3},
      "custom_provider_config": {"base_provider_type": "openai"}
    }
  },
  "config_store": {"enabled": false},
  "governance": {
    "virtual_keys": [
      {
        "id": "vk-flash",
        "name": "tier-flash",
        "value": "sk-bf-flash-local",
        "is_active": true,
        "provider_configs": [
          {"provider": "agnes", "weight": 1.0, "allowed_models": ["agnes-2.5-flash", "agnes-2.5-pro-beta"], "key_ids": ["*"]},
          {"provider": "nararouter", "weight": 1.0, "allowed_models": ["laguna-s-2.1", "step-3.7-flash"], "key_ids": ["*"]},
          {"provider": "opencode_zen", "weight": 1.0, "allowed_models": ["deepseek-v4-flash-free"], "key_ids": ["*"]},
          {"provider": "openrouter", "weight": 1.0, "allowed_models": ["poolside/laguna-s-2.1:free"], "key_ids": ["*"]}
        ]
      },
      {
        "id": "vk-max",
        "name": "tier-max",
        "value": "sk-bf-max-local",
        "is_active": true,
        "provider_configs": [
          {"provider": "agnes", "weight": 1.0, "allowed_models": ["agnes-2.5-pro", "agnes-2.5-pro-alpha"], "key_ids": ["*"]},
          {"provider": "nararouter", "weight": 1.0, "allowed_models": ["longcat-2.0-free", "minimax-m3-free", "qwen3.8-27b"], "key_ids": ["*"]},
          {"provider": "openrouter", "weight": 1.0, "allowed_models": ["minimax/minimax-m2.7:free", "minimax/minimax-m3:free", "nvidia/nemotron-3-ultra-550b-a55b:free"], "key_ids": ["*"]}
        ]
      },
      {
        "id": "vk-contributor_free",
        "name": "tier-contributor_free",
        "value": "sk-bf-contributor-free-local",
        "is_active": true,
        "provider_configs": [
          {"provider": "nararouter", "weight": 1.0, "allowed_models": ["muse-spark-1.2-contributor-free"], "key_ids": ["*"]},
          {"provider": "opencode_zen", "weight": 1.0, "allowed_models": ["muse-spark-1.2-contributor-free", "muse-spark-1.3-contributor-free"], "key_ids": ["*"]}
        ]
      }
    ],
    "budgets": [],
    "rate_limits": []
  }
}
```

*Truncated: full generation script would enumerate all 126 keeps (46 flash + 77 max + 3 contributor_free) across 15 providers; above shows sampling for 6 providers. Use `["*"]` only when provider's keep count equals model discovery count and catalog already lists them; explicit lists are safer for tier enforcement.*

**Minimal file-only without VKs (alternative, direct model allowlist per provider — consumers specify real model):**

```json
{
  "$schema": "https://www.getbifrost.ai/schema",
  "providers": {
    "nararouter": {
      "keys": [{"name": "nararouter-flash", "value": "env.NARAROUTER_API_KEY", "models": ["laguna-s-2.1", "step-3.7-flash"], "weight": 1.0}, {"name": "nararouter-max", "value": "env.NARAROUTER_API_KEY", "models": ["longcat-2.0-free", "minimax-m3-free", "qwen3.8-27b"], "weight": 1.0}, {"name": "nararouter-contributor", "value": "env.NARAROUTER_API_KEY", "models": ["muse-spark-1.2-contributor-free"], "weight": 1.0}]
    }
  },
  "config_store": {"enabled": false},
  "client": {"enforce_auth_on_inference": false}
}
```

**Consumer call examples:**

```bash
# Phase 1 via virtual keys (tier enforced by header, real model):
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "x-bf-vk: sk-bf-flash-local" \
  -d '{"model": "deepseek-v4-flash-free", "messages": [{"role":"user","content":"write python"}]}'

# Phase 1 with fallback chain (client-driven):
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "x-bf-vk: sk-bf-flash-local" \
  -d '{"model": "deepseek-v4-flash-free", "fallbacks": ["nararouter/laguna-s-2.1", "openrouter/poolside/laguna-s-2.1:free"], "messages": [...]}'

# Phase 2 shim alias (if added): model is tier itself
curl -X POST http://localhost:8081/v1/chat/completions \
  -d '{"model": "flash", "messages": [...]}'
# shim rewrites to one of 46 flash ids then forwards to http://localhost:8080
```

### 6. Constraints for local use

- **No Postgres required.** SQLite default (`config_store.type: "sqlite", path: "./config.db"`) sufficient for single-node local. If file-only, no DB files at all — only `config.json` in app-dir.
- **App-dir / volume (Docker vs Podman):** Bifrost stores `config.json`, `config.db`, `logs.db` in app-dir. Default OS config dir is `~/.config/bifrost`; override with `-app-dir /app/data`. For containers: `docker run -v $(pwd)/bifrost-data:/app/data maximhq/bifrost` or Podman Quadlet `Volume=bifrost-data:/app/data`. Must be writable; file-only still reads from app-dir at startup.
- **Restart vs hot-reload:** File-only (`config_store.enabled: false`) requires restart on `config.json` change. DB-backed split mode does not hot-reload either — it reconciles only at startup, but UI/API edits apply live without restart. For YAML churn (new keeps every build), schedule regeneration + restart (systemd/Quadlet `ExecReload`) or add a watcher sidecar that regenerates and SIGHUPs.
- **Secrets:** `value: "env.VAR"` pattern is enforced. Do not commit keys. For local, load from `~/.local/share/llm-discovery/.env` or Quadlet `EnvironmentFile=`. `allow_direct_keys` flag removed in v1.5 — direct `Authorization` bypass no longer works; all keys must be registered.
- **Network / TLS:** `allow_private_network: true` if any provider points to 192.168/10.x (local vLLM/Ollama). For self-signed TLS, add `network_config.insecure_skip_verify: true` or `ca_cert_pem`.
- **Auth:** Locally set `client.enforce_auth_on_inference: false` to allow unauthenticated dev calls; set true when VK gating needed (tier  enforcement). `dual_credential_conflict_behavior` defaults to prefer_idp; not relevant locally.
- **Versioned allowlist semantics:** Schema `version: 2` makes empty `models: []` = deny all. Always use `["*"]` or explicit list; never leave empty expecting allow-all.
- **Model Catalog pollution:** Default pricing URL `https://getbifrost.ai/datasheet` may not list custom providers (agnes, bazaarlink etc.) — catalog will rely on `/v1/models` enrichment. If that endpoint requires auth (it does, via stored keys), Bifrost fetches at startup using configured keys. Startup logs warning if list fails; provider remains usable via explicit allowlists. To avoid confusion, prefer explicit `models` arrays over wildcard for custom providers.
- **Observability:** `client.enable_logging: true` keeps request logs in logs.db (shown in UI at http://localhost:8080). Prometheus metrics at `/metrics`, OTel optional. For file-only ephemeral runs, logs.db still written if logs store not disabled.
- **Version mismatch:** Config JSON with absent `config_store` field does NOT mean file-only — it defaults to DB-backed. Must explicitly set `"config_store": {"enabled": false}` for file-only. Likewise, `source_of_truth` defaults to `"split"`; only set `"config.json"` when strict GitOps prune desired. Empty arrays prune only in authoritative mode, not split.

## Recommendations

1. **Config generation:** Write `scripts/generate-bifrost-config.py` that reads all `data/results/*.yaml` keep entries + `config/providers.yaml` base_urls, groups by tier, and emits Bifrost `config.json` per shape above. Input: 126 keeps, output: providers block + 3 virtual_keys. Handle model_id normalization (version dots, :free/:free suffix, provider prefix slash) — reuse `src/llm_discovery/model_info_store.py:normalize_store_key` if needed for dedup.

2. **Adopt file-only + 3 VKs as Phase 1.** Verify with `npx -y @maximhq/bifrost -app-dir ./bifrost-data` pointing at generated config.json; smoke-test each tier via VK header + real model. Document weight uniform.

3. **Evaluate Podman Quadlet in sibling ticket #111 using this shape as fixture** (volume mount, EnvironmentFile, restart policy).

4. **Do not block on true model-alias tiers** — capture as Phase 2 shim/plugin design. If product requires `"model": "flash"` without VK header, prototype a 30-line FastAPI sidecar at `:8081` that samples tier pool then proxies to `:8080`.

## Open questions / fog

- Whether Bifrost allows `governance.virtual_keys` in pure file-only mode without any DB driver compiled in — schema suggests yes, but integration test needed (file-only + VKs may still require minimal SQLite for VK lookup; docs example under governance shows file JSON too, but confirm at runtime).
- Whether upstream providers tolerate `"model"` being the original keep id verbatim (e.g. `"minimax/minimax-m3:free"` with colon/slash) when forwarded via custom_provider base_provider_type openai — likely yes (OpenAI spec allows any string), but confirm via nararouter/openrouter live probe.

## Sources

- Bifrost docs: https://docs.getbifrost.ai/overview, /quickstart/gateway/setting-up#configuration-modes, /quickstart/gateway/provider-configuration, /providers/provider-routing, /features/governance/virtual-keys, /features/governance/routing, /features/retries-and-fallbacks, /features/keys-management, /deployment-guides/config-json/source-of-truth, /architecture/framework/model-catalog
- Schema: https://www.getbifrost.ai/schema
- GitHub: https://github.com/maximhq/bifrost (README, npx/docker quickstart, transport bifrost-http)
- Local: data/results/*.yaml (sampled 4 files + counts via python), data/model_info_store.json sample, config/providers.yaml, src/llm_discovery/categorize.py, src/llm_discovery/results.py, docs/adr/0005-0007, CONTEXT.md (Keeper, Ephemeral Report)
- Ticket map: https://github.com/SoongGuanLeong/llm-discovery/issues/109, target: https://github.com/SoongGuanLeong/llm-discovery/issues/110

## Deliverable disposition

- This file: `docs/research/issue-110-bifrost-config.md`
- Recommended next: create branch `research/bifrost-config` from this commit + open PR to wayfinder #109, or comment gist link in #110 (gh auth required; manual paste if token unavailable).
