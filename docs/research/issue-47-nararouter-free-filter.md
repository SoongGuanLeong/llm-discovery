# Research: Capture NaraRouter /models raw shape and free-category discriminator (issue #47)

Part of #46 — Wayfinder: Fix NaraRouter free-category filter + LLM model-name recognition.

## Question

What field/tag in NaraRouter `GET /v1/models` response distinguishes the website's two free categories ("free" vs "free only for paid users")? We want only real free models; drop the paid-gated group.

Capture raw response (with redacted key) and website mapping: list models shown under each website bucket, then inspect each model's raw JSON for differences (pricing, tags, metadata, limit fields, description markers). If no in-response discriminator exists, document alternative signal (second endpoint, docs page, pricing field) that allows split.

## TL;DR

- **`/v1/models` requires API key** — unauthenticated returns `401` with `{"error":{"type":"unauthorized","message":"A valid API key is required."}}`.
- **Authenticated raw shape is uniform** — no pricing/tags/metadata/quota field distinguishes free tiers. All 59 models share `{"id","object":"model","owned_by":"byNara","weight","context_window"?,"vision"?,"reasoning"?}`. The 8 `-free` models are structurally identical to paid models.
- **No in-response discriminator exists.** `"-free"` suffix alone cannot split “true free” vs “paid-gated free” — both groups use it.
- **Discriminator lives in a second endpoint:** `GET https://router.bynara.id/api/plans` (public, no auth) — the same endpoint the docs page `router.bynara.id/docs#models` loads live. The `free` plan (code `free`) lists 9 true-free aliases; the `freemium`/`freemium-max`/`freemium-ultra` plans add 6 paid-gated `-free` aliases.
- **Existing pipeline has no NaraRouter special handling** — generic `discover_models` + `_split_by_free_rule` keeps all 8 `-free` models, mixing both buckets.
- **Recommendation:** keep predicate = `id in freePlanModels` where `freePlanModels = GET /api/plans → data.find(p=>p.code=="free").models`. Fallback: hard-coded allowlist derived 2026-09-03. Implement as NaraRouter-specific filter before or replacing `_split_by_free_rule` (or as new `discovery_strategy: nararouter`).

---

## 1) Website docs for free categories

### Source pages (primary)

- **Docs → Models:** `https://router.bynara.id/docs` (section "Models") — inspected via `curl -sL` 2026-09-03.
- **Pricing + Plans API:** `https://router.bynara.id/api/plans` — JSON returned with `curl -sL` 2026-09-03 (public, no auth). This is the live source the docs reference.
- **Marketing + Pricing i18n strings:** embedded Next.js payload on both `router.bynara.id` and `bynara.id` contains bucket labels `Base`/`Lite`/`Mocin`/`Pro`/`PAYG only` and `freeBadge:"Free"`.

### Docs wording (verbatim extracts from `router.bynara.id/docs` payload)

> **Models intro:** *"Pass a model alias in the model field. The list below is loaded live from the public plans endpoint, so it always reflects the models currently offered and which plan tier grants each."*

> **Live note:** *"Live from {endpoint}. The authenticated {modelsEndpoint} returns exactly the aliases your own plan entitles."*

> **Tier note:** *"Tiers are not strict supersets: Lite and Lite Mocin are sibling sets. A plan can reach a model class only if it grants at least one model in that class."*

> **Quota model:** *"Each model belongs to a quota class with its own daily token quota. A plan gets a separate daily quota for every class it includes; the quotas are independent — spending from one never draws down another."*  (Classes: `Base`/`Lite`/`Mocin`/`Pro` shown in `docs.limits` and pricing i18n.)

> **Limits:** *"Subscription plans are governed by a per-minute request rate and a daily token quota; the free tier and per-model fair-use caps apply otherwise."* and *"A null cap means fair-use (no hard daily ceiling)."*

### Plans endpoint (public, no auth)

```
GET https://router.bynara.id/api/plans
Content-Type: application/json
No Authorization required
```

Response shape (`{"object":"list","data":[...]}`) — 8 plans as of 2026-09-03:

| code | name | tagline | price_daily_idr | price_weekly_idr | currency | token_cap_daily | rpm_limit | models count |
|------|------|---------|-----------------|------------------|----------|-----------------|-----------|-------------|
| `free` | Free | "Start free, forever." | 0 | 0 | IDR | 7000000 | 15 | 9 |
| `freemium` | Freemium | "" | 5000 | 35000 | USD | 25000000 | 50 | 15 |
| `minimax-promo` | Minimax [PROMO] | "" | 5000 | 35000 | USD | 30000000 | 60 | 1 |
| `deepseek-lite-v2` | Deepseek | "" | 10000 | 70000 | USD | 11000000 | 50 | 4 |
| `freemium-max` | FreeMium Max | "Freemium with more usage" | 10000 | 65000 | USD | 60000000 | 60 | 16 |
| `muse-1-2` | Muse 1.2 | "Limited Promo" | 10000 | 65000 | USD | 70000000 | 60 | 3 |
| `mimo-pro-v2` | Mimo | "" | 18000 | 126000 | USD | 50000000 | 70 | 3 |
| `freemium-ultra` | FreeMium Ultra | "Freemium with 5x usage" | 30000 | 0 | USD | 200000000 | 60 | 16 |

**Website's two free categories** (as named in issue) map to:

- **"free" (true free, no payment)** = `code == "free"` — no card required, `price_daily_idr: 0`, `token_cap_daily: 7M`.
- **"free only for paid users" (paid-gated)** = models that appear in `freemium`/`freemium-max`/`freemium-ultra` (and their `code != "free"` siblings) but **not** in `free`. These require purchasing a Freemium tier (5000 IDR/day) to unlock.

`free` plan models (true free):

```json
["agnes-2.0-flash","agnes-2.5-flash","laguna-s-2.1","minimax-m3-free","mistral-large","mistral-medium-3-5","muse-spark-1.2-contributor-free","qwen3.8-27b","stepfun-3.7-flash"]
```

`freemium` plan models (freemium = free + paid-gated free):

```json
["agnes-2.0-flash","agnes-2.5-flash","deepseek-v4-flash-free","glm-5.3-flash-free","glm-5.3-free","laguna-s-2.1","mimo-v2.5-free","minimax-m3-free","mistral-large","mistral-medium-3-5","muse-spark-1.2-contributor-free","muse-spark-1.3-contributor-free","qwen3.8-27b","qwen3.8-flash-free","stepfun-3.7-flash"]
```

Paid-gated free-only delta (present in freemium, absent in free):

```
deepseek-v4-flash-free
mimo-v2.5-free
glm-5.3-flash-free
glm-5.3-free
muse-spark-1.3-contributor-free
qwen3.8-flash-free
```

`freemium-max` / `freemium-ultra` extend freemium with `deepseek-v4-pro-0813-bynara` (paid, not `-free`) but keep same 6 paid-gated `-free` entries. Other paid plans (`minimax-promo`, `deepseek-lite-v2`, `muse-1-2`, `mimo-pro-v2`) contain no `-free` models.

This bucket structure is also rendered on the pricing page (live fetch; failure string: `"We could not load pricing."`). The class-to-plan matrix on docs (`"Model availability by plan tier. Each row is a model alias; the Bucket column shows which daily quota bucket the model draws from"`) is derived from this same endpoint.

---

## 2) Try to fetch /v1/models raw response

### Discovery reference (`src/llm_discovery/discovery.py`)

```python
def discover_models(base_url: str, api_key: str) -> list[dict]:
    url = f"{base_url.rstrip('/')}/models"
    response = httpx.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30)
    response.raise_for_status()
    data = response.json()
    if "data" in data: models = data["data"]
    elif "models" in data: models = data["models"]
    else: raise RuntimeError(...)
    return _normalize_models(models)
```

Normalizer keeps only `{id, name, object}` from raw, discarding any extra fields — but raw was captured before normalization for this research.

`config/providers.yaml` entry:

```yaml
- name: nararouter
  base_url: https://router.bynara.id/v1
  secret: NARAROUTER_API_KEY
```

**API key needed:** Yes. Key prefix `sk-nry-`, obtained via Infisical (`LLM_SHARED_PROJECT_ID=7686072c-85c7-4b7e-96e5-5bad8086cf44`, `NARAROUTER_API_KEY` env). All fetches below used `Authorization: Bearer <redacted>`.

### Unauthenticated fetch (error shape)

```bash
curl -s https://router.bynara.id/v1/models
curl -s -i https://router.bynara.id/v1/models  # headers
```

Result — HTTP 401, consistent across both probes:

```http
HTTP/2 401
content-type: application/json; charset=utf-8
x-request-id: d4ab5048-23fc-4f2b-9ba9-40689c1f3e6b
```

```json
{
  "error": {
    "type": "unauthorized",
    "message": "A valid API key is required.",
    "request_id": "d4ab5048-23fc-4f2b-9ba9-40689c1f3e6b"
  }
}
```

No `data` field, no models list. Same envelope as other NaraRouter 401s (e.g., bad key `sk-test` returns identical shape with different `request_id`). This confirms the `raise_for_status()` path in `discovery.py` would raise `httpx.HTTPStatusError` before JSON-shape handling.

### Authenticated fetch (raw shape, redacted key)

```bash
NARAROUTER_API_KEY=$(infisical export --projectId $LLM_SHARED_PROJECT_ID --env dev --format json | jq -r '.[] | select(.key=="NARAROUTER_API_KEY") | .value')
curl -s https://router.bynara.id/v1/models -H "Authorization: Bearer $NARAROUTER_API_KEY" | python3 -m json.tool
```

Captured 2026-09-03 `09:36 UTC`, 59 models.

Top-level envelope:

```json
{
  "object": "list",
  "data": [ /* 59 entries */ ]
}
```

No `models` alternative key; always `data` (OpenAI-compatible). No pagination fields.

Per-model fields — union across all 59 (counts):

| field | frequency | type | notes |
|-------|-----------|------|-------|
| `id` | 59/59 | string | e.g. `"deepseek-v4-flash-free"` |
| `object` | 59/59 | string | always `"model"` |
| `owned_by` | 59/59 | string | always `"byNara"` |
| `weight` | 59/59 | number | 0.1–4.5, e.g. 1.0; not pricing |
| `context_window` | 58/59 | integer | 128000–1000000; one model (`agnes-video-v2.0`) omits it |
| `vision` | 41/59 | boolean | present only when true; absent otherwise (not `false`) |
| `reasoning` | 31/59 | boolean | present only when true; absent otherwise |

**Absent fields (checked):** `pricing`, `price`, `cost`, `tags`, `metadata`, `description`, `limit`, `quota`, `tier`, `class`, `bucket`, `plan`, `category`, `free`, `entitlement`, `created`, `display_name`. No free-category marker.

Sample entries (three of the 8 free-id models, verbatim):

```json
{
  "id": "minimax-m3-free",
  "object": "model",
  "owned_by": "byNara",
  "context_window": 1000000,
  "weight": 1,
  "vision": true
}
{
  "id": "mimo-v2.5-free",
  "object": "model",
  "owned_by": "byNara",
  "context_window": 1000000,
  "weight": 1,
  "vision": true,
  "reasoning": true
}
{
  "id": "glm-5.3-flash-free",
  "object": "model",
  "owned_by": "byNara",
  "context_window": 128000,
  "weight": 1,
  "vision": true
}
```

Paid model for contrast:

```json
{
  "id": "muse-spark-1.2",
  "object": "model",
  "owned_by": "byNara",
  "context_window": 1000000,
  "weight": 1,
  "vision": true
}
```

True-free non-`free`-suffixed model (for contrast, also in `free` plan):

```json
{
  "id": "qwen3.8-27b",
  "object": "model",
  "owned_by": "byNara",
  "context_window": 1000000,
  "weight": 1,
  "reasoning": true
}
```

Observation: `weight` is not pricing (free model `minimax-m3-free` weight 1.0 equals paid `minimax-m3` weight 1.0; `agnes-2.0-flash` free weight 0.1 vs `agnes-2.5-flash` free weight 0.2 differs within true-free bucket). `context_window` also not discriminative (`glm-5.3-flash-free` 128k vs `mimo-v2.5-free` 1000k vs `laguna-s-2.1` 262k). `vision`/`reasoning` flags are capability markers, not entitlement.

**Expected-vs-actual for free tier from code perspective:** `discover_models` expects either `data["data"]` or `data["models"]` — NaraRouter matches the first. `_normalize_models` maps `id`/`name`/`display_name` fallbacks, so both response and error shapes would have been handled by existing code only after auth.

### Authenticated but wrong entitlement (inferred)

Docs state: *"The authenticated {modelsEndpoint} returns exactly the aliases your own plan entitles."* The key used here is a shared dev key with freemium-tier entitlement, so it returns all 8 `-free` models. A true-free key (no Freemium purchase) would, per docs, return only the 9 `free`-plan aliases — this is testable but not probed here because only one key is available. The research does **not** assume `weight` or other fields change with entitlement; the list length/filter is the signal.

---

## 3) Existing discovery/provider code for nararouter special handling

### Provider config

- `config/providers.yaml:66-68` — single entry, no overrides:
  ```yaml
  - name: nararouter
    base_url: https://router.bynara.id/v1
    secret: NARAROUTER_API_KEY
  ```
  No `discovery:` field (defaults to `openai` in `src/llm_discovery/config.py:ProviderConfig(discovery="openai")`).
  No `discovery_strategy:` field (defaults `None`; special-cased values are `"bazaarlink"` → auto:free and `"cloudflare"` → `discover_cloudflare_models`).

### Discovery layer

- `src/llm_discovery/discovery.py` — `discover_models()` is generic OpenAI path; `_normalize_models` + `discover_cloudflare_models` are the only provider quirks. **No `discover_nararouter_*` function** and **no `grep -rn nararouter` hit in `src/`** (verified 2026-09-03).
- `src/llm_discovery/provider.py:resolve_provider` — if `config.base_url` is set, returns `ResolvedProvider` verbatim; otherwise looks up `models.dev` catalog. For nararouter it takes the first branch (explicit `base_url`), so `models.dev` is not consulted for routing, only for later evidence/AA matching.
- `src/llm_discovery/pipeline.py:discover_provider` / `discover_single` — branch on `provider.discovery == "cloudflare"` and `provider.discovery_strategy == "bazaarlink"`; nararouter falls through to generic `discover_models`.

### Free-model filtering (the current “special handling” that applies to nararouter)

- `src/llm_discovery/pipeline.py:456-482` —
  ```python
  FREE_MARKERS = (":free", "-free", "_free", "/free")
  def _is_free_model(model_id): return any(marker in model_id for marker in FREE_MARKERS)
  def _has_free_name(models): return any(_is_free_model(m["id"]) for m in models)
  def _split_by_free_rule(models):
      if not _has_free_name(models): return models, []
      free_models = [m for m in models if _is_free_model(m["id"])]
      non_free = [m for m in models if m not in free_models]
      return free_models, non_free
  ```
  Applied in both `discover_single` and `discover_provider` **before LLM evaluation** — dropped models never reach YAML or judge.

  For nararouter's 59-model response, `_has_free_name == True` (8 hits), so `eval_models = 8 -free models`, `dropped_models = 51`. This is why `data/results/nararouter.yaml` contains exactly those 8.

- `src/llm_discovery/model_matching.py:30` — `normalize_model_id` strips `[:/_-]free$` before AA matching (so `deepseek-v4-flash-free` → `deepseek-v4-flash`). This is unrelated to filtering but affects evidence.
- `src/llm_discovery/evidence_utils.py` — strips `free-model-rule` evidence noise; does not filter by bucket.
- No NaraRouter-specific allowlist, deny-list, quota check, or second-endpoint call exists.

### What this means for issue #46

- The pipeline **cannot** currently distinguish true-free vs paid-gated free because the only predicate is `FREE_MARKERS` on `id`. Both buckets share `"-free"`, so they are kept together and 51 paid non-free aliases (including true-free aliases without `-free`, e.g., `qwen3.8-27b`, `mistral-large`) are **dropped** by the free rule even though they are entitled on the free tier.
- The generic free-rule also causes a second problem: `qwen3.8-27b`, `agnes-2.0-flash`, `laguna-s-2.1`, etc., are true-free but lack `-free`, so they are excluded from `data/results/nararouter.yaml` even though the website shows them as free.

---

## 4) data/results/nararouter.yaml model_ids vs website buckets

### Snapshot (evaluated_at 2026-09-03T09:23:36.099398+00:00)

8 models were evaluated (all `-free` after `_split_by_free_rule`; 51 non-free dropped before judge):

| # | model_id | decision | tier | aa_model_id | aa_score | note |
|---|----------|----------|------|-------------|----------|------|
| 1 | `deepseek-v4-flash-free` | keep | max | fe4c0848-e284-4e52-a79d-cdc28392f1a9 | 51.8 | via `deepseek-v4-flash` strip |
| 2 | `glm-5.3-flash-free` | keep | flash | 19496b81-9f41-4214-a77a-1df803b3c5ae | 57.5 | via `glm-5.3-flash` |
| 3 | `mimo-v2.5-free` | drop | drop | null | null | LLM: no AA/coding evidence |
| 4 | `minimax-m3-free` | drop | drop | 277f939a-985b-4b37-859d-b3eabc7c0b26 | 45.4 | AA 45.4 but no coding benchmarks → drop |
| 5 | `muse-spark-1.2-contributor-free` | drop | drop | null | null | no AA |
| 6 | `qwen3.8-flash-free` | drop | drop | null | null | no AA |
| 7 | `glm-5.3-free` | error | error | null | null | judge invalid JSON after retries |
| 8 | `muse-spark-1.3-contributor-free` | error | error | null | null | judge invalid JSON |

(`keep:2, drop_llm:4, error:2` — matches #46 notes.)

### Comparison to website buckets

`_split_by_free_rule` result vs `GET /api/plans` truth:

| model_id | `id` contains `-free` | `free` plan (true free) | `freemium`+ plans (paid-gated) | `discover_models` raw | In `nararouter.yaml` (post free-rule) | Website bucket mapping | Mismatch |
|----------|----------------------|------------------------|-------------------------------|----------------------|--------------------------------------|----------------------|----------|
| `agnes-2.0-flash` | no | yes | yes | yes (present) | **no** (dropped as non-free) | **free — true free** | **missing: pipeline drops true-free without `-free` suffix** |
| `agnes-2.5-flash` | no | yes | yes | yes | no | free — true free | same |
| `laguna-s-2.1` | no | yes | yes | yes | no | free — true free | same |
| `qwen3.8-27b` | no | yes | yes | yes | no | free — true free | same |
| `mistral-large` | no | yes | yes | yes | no | free — true free | same |
| `mistral-medium-3-5` | no | yes | yes | yes | no | free — true free | same |
| `stepfun-3.7-flash` | no | yes | yes | yes | no | free — true free | same |
| `minimax-m3-free` | yes | yes | yes | yes | yes (drop) | **both: true free + paid-gated** | **ambiguous: appears in both; truly free, so should be kept** |
| `muse-spark-1.2-contributor-free` | yes | yes | yes | yes | yes (drop) | both | same |
| `deepseek-v4-flash-free` | yes | **no** | yes | yes | yes (keep) | **paid-gated only** | **should be dropped per #47** |
| `glm-5.3-flash-free` | yes | no | yes | yes | yes (keep) | paid-gated only | should be dropped |
| `glm-5.3-free` | yes | no | yes | yes | yes (error) | paid-gated only | should be dropped |
| `mimo-v2.5-free` | yes | no | yes | yes | yes (drop) | paid-gated only | should be dropped (already drop, but for right reason) |
| `muse-spark-1.3-contributor-free` | yes | no | yes | yes | yes (error) | paid-gated only | should be dropped |
| `qwen3.8-flash-free` | yes | no | yes | yes | yes (drop) | paid-gated only | should be dropped |
| (51 others e.g. `deepseek-v4-flash`, `glm-5.3`, `mimo-v2.5`, `claude-opus-4.8`) | no | no | no (or promo-specific) | yes | no | paid / promo | correctly excluded by free-rule *if* goal is free-only, but misaligned if goal is true-free (which includes non-`-free` true-free) |

Legend: `yes` = member; `no` = not member.

Key observations:

1. **Pipeline's `-free` predicate is both over-inclusive and under-inclusive.** Over-inclusive: keeps 6 paid-gated `-free` models that #47 wants dropped. Under-inclusive: drops 7 true-free models that lack `-free` suffix (`agnes-*`, `laguna-*`, `qwen3.8-27b`, `mistral-*`, `stepfun-*`), so `nararouter.yaml` never evaluates them.
2. **Website bucket vs raw field gap:** No raw field (`weight`, `context_window`, `vision`, `reasoning`, `owned_by`) correlates with bucket — verified by comparing per-model JSON across buckets (all `weight:1`, `vision`/`reasoning` scattered). The bucket mapping is **not encoded in /v1/models at all**; it is entitlement state on the plans endpoint.
3. **Evaluation consequences:** The two `keep` models in current YAML (`deepseek-v4-flash-free`, `glm-5.3-flash-free`) are both paid-gated, so under #47's desired split the keep list would be empty unless true-free models are added and name-recognition is fixed (see #46 notes: `minimax-m3-free` and `mimo-v2.5-free` drop due to AA/matching misses, `muse-spark`/`qwen3.8` due to no catalog entry).
4. **Auth-dependent list:** Docs confirm `GET /v1/models` is entitlement-filtered per key. Re-running with a true-free key would return only the 9 true-free aliases; with the current freemium key it returns 59. This is an alternative verification path but not the recommended filter — static allowlist + `GET /api/plans` check is more robust for offline/batch runs.

---

## Raw-shape table (model_id → website bucket → distinguishing field/value)

No field distinguishes; table proves absence.

| model_id (from `GET /v1/models` `data[].id`) | website bucket(s) | raw JSON distinguishing field | field value | distinguishes? |
|---|---|---|---|---|
| `minimax-m3-free` | `free` (true free) + `freemium*` (all freemium tiers) | — | `{owned_by:"byNara", weight:1, context_window:1000000, vision:true}` | **no** — same as paid-gated |
| `muse-spark-1.2-contributor-free` | `free` + `freemium*` | — | same shape | no |
| `deepseek-v4-flash-free` | `freemium*` only (paid-gated) | — | `{owned_by:"byNara", weight:1, context_window:1000000, reasoning:true}` | no — no free-bucket marker |
| `glm-5.3-flash-free` | `freemium*` only | — | `{owned_by:"byNara", weight:1, context_window:128000, vision:true}` | no |
| `glm-5.3-free` | `freemium*` only | — | `{owned_by:"byNara", weight:1, context_window:1000000, reasoning:true}` | no |
| `mimo-v2.5-free` | `freemium*` only | — | `{owned_by:"byNara", weight:1, context_window:1000000, vision:true, reasoning:true}` | no |
| `muse-spark-1.3-contributor-free` | `freemium*` only | — | `{owned_by:"byNara", weight:1, context_window:1000000, vision:true}` | no |
| `qwen3.8-flash-free` | `freemium*` only | — | `{owned_by:"byNara", weight:1, context_window:1000000, vision:true}` | no |
| `agnes-2.0-flash` | `free` true free | — | `{owned_by:"byNara", weight:0.1, context_window:512000, vision:true, reasoning:true}` | no — not even `-free` |
| `qwen3.8-27b` | `free` true free | — | `{owned_by:"byNara", weight:1, context_window:1000000, reasoning:true}` | no |
| *51 non-free* (e.g. `claude-opus-4.8`, `deepseek-v4-flash`) | promo/paid (`minimax-promo`, `deepseek-lite-v2`, etc.) | — | same envelope | no |

Checked fields: `pricing`/`price`/`tags`/`metadata`/`description`/`limit`/`quota`/`tier`/`class`/`bucket`/`plan`/`category`/`free`/`entitlement` — none present. Error shape table also included below for completeness.

| request | status | body `error.type` | body `error.message` |
|---------|--------|-------------------|---------------------|
| `GET /v1/models` no header | 401 | `unauthorized` | `A valid API key is required.` |
| `GET /v1/models` bad key `sk-test` | 401 | `unauthorized` | `A valid API key is required.` |
| `GET /v1/models` valid `sk-nry-...` | 200 | — | — |

---

## Recommendation for filter predicate

### Why `_split_by_free_rule` is insufficient for NaraRouter

- Assumes `"free" in id ⇒ free, otherwise paid` globally, which matches providers like OpenRouter (`:free`) but not NaraRouter where true-free includes non-`-free` aliases and paid-gated includes `-free` aliases.
- No auxiliary signal (`weight`/`context_window`/`vision`/`reasoning`) substitutes — analysis proves no correlation.

### Alternative signals that *do* split

Ranked by reliability:

1. **`GET https://router.bynara.id/api/plans` (recommended primary).** Public, no auth, stable JSON, same origin as website and docs live load. Predicate:
   ```python
   # fetch once per run, cache for pipeline
   import httpx
   def get_nararouter_free_allowlist(timeout=10) -> set[str]:
       resp = httpx.get("https://router.bynara.id/api/plans", timeout=timeout)
       resp.raise_for_status()
       data = resp.json()["data"]
       free_entry = next(p for p in data if p["code"] == "free")
       return set(free_entry["models"])  # 9 aliases as of 2026-09-03
   def is_true_free_nararouter(model_id: str, allowlist: set[str]) -> bool:
       return model_id in allowlist
   ```
   Pros: canonical, website-authoritative, covers non-`-free` true-free (`agnes-*`, `qwen3.8-27b`, `mistral-large`, etc.). Handles renames without code change (fetch live). Cons: adds one extra HTTP call; allowlist snapshot should be logged for reproducibility.

2. **Entitlement-filtered `GET /v1/models` with a true-free key (secondary verification).** Docs: *"authenticated {modelsEndpoint} returns exactly the aliases your own plan entitles"*. A key on the `free` plan returns only 9 true-free aliases — that list itself is the discriminator. Not recommended as primary filter because it requires a separate `free`-tier key and is non-deterministic for batch runs with a freemium key (current shared key sees 59).

3. **Hard-coded allowlist fallback (offline-safe).** For `DISABLE_WEB_SEARCH`/`--offline` or if `/api/plans` fetch fails, fall back to a vendored snapshot:
   ```python
   NARAROUTER_TRUE_FREE = {
       "agnes-2.0-flash", "agnes-2.5-flash", "laguna-s-2.1",
       "minimax-m3-free", "mistral-large", "mistral-medium-3-5",
       "muse-spark-1.2-contributor-free", "qwen3.8-27b", "stepfun-3.7-flash",
   }
   ```
   Snapshot date: 2026-09-03; refresh via `scripts/refresh_catalogs.py`-style or periodic fetch.

### Proposed pipeline change (choice left to #46 implementer)

Options, without prescribing which ticket owns it:

- **A. New `discovery_strategy: nararouter`** — in `config/providers.yaml` set `discovery_strategy: nararouter` for nararouter entry; in `pipeline.py:discover_provider` branch to a new `discover_nararouter_models(base_url, api_key) -> (models, plans_allowlist)` that fetches `/api/plans` + `/v1/models` and returns only allowlisted models (bypassing `_split_by_free_rule`). Most explicit, parallels `bazaarlink`/`cloudflare`.
- **B. NaraRouter-aware `_split_by_free_rule`** — in `_split_by_free_rule`, if `provider_name == "nararouter"`, delegate to allowlist check instead of `FREE_MARKERS`. Minimal change, keeps one function.
- **C. Post-discovery drop** — keep generic `_split_by_free_rule` but after discovery filter `eval_models = [m for m in eval_models if m["id"] in true_free_allowlist]` and add dropped paid-gated models to `dropped_models` with reason `nararouter: paid-gated free per /api/plans`. Visible in logs without changing strategy.

All three must also **include true-free non-`-free` aliases** — current `_split_by_free_rule` would need to be disabled for NaraRouter or allowlist would be applied *before* it.

### Edge cases to cover in implementation

- **Fallback on fetch failure:** if `/api/plans` times out or returns non-200, fall back to vendored snapshot + warning log; do not fall back to `FREE_MARKERS` (would reintroduce bug).
- **Log the allowlist source:** `print("[nararouter] true-free allowlist from /api/plans (9)" | "fallback snapshot (9)")` and list IDs for audit.
- **Pricing interaction:** all true-free models have effective price 0; downstream `categorize_model` already handles `price == 0 → 0.05` avoidance. No change needed.
- **Future renames:** website may rename `minimax-m3-free` → `minimax-m3` or add new true-free entries; live fetch handles it, snapshot must be reviewed periodically (e.g., on catalog refresh).
- **Weight/quota not a proxy:** do not filter on `weight < 0.5` or `context_window` or `vision` — proven non-discriminative.

---

## Appendix: Full raw capture (redacted)

### Request

```http
GET /v1/models HTTP/1.1
Host: router.bynara.id
Authorization: Bearer sk-nry-****REDACTED****
Accept: application/json
```

### Response (200, truncated — 59 entries; `weight`/`context_window` shown)

```json
{
  "object": "list",
  "data": [
    {"id":"agnes-2.0-flash","object":"model","owned_by":"byNara","context_window":512000,"weight":0.1,"vision":true,"reasoning":true},
    {"id":"agnes-2.5-flash","object":"model","owned_by":"byNara","context_window":512000,"weight":0.2,"vision":true,"reasoning":true},
    {"id":"agnes-video-v2.0","object":"model","owned_by":"byNara","weight":1},
    {"id":"claude-fable-5","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"claude-fable-5.1","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"claude-opus-4.7","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"claude-opus-4.8","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"claude-opus-5","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"claude-sonnet-5","object":"model","owned_by":"byNara","context_window":1000000,"weight":1.3,"vision":true},
    {"id":"deepseek-v4-flash-alibaba","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true,"reasoning":true},
    {"id":"deepseek-v4-flash-free","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"reasoning":true},
    {"id":"deepseek-v4-flash-vision-exp","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true,"reasoning":true},
    {"id":"deepseek-v4-pro-0813-bynara","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"reasoning":true},
    {"id":"deepseek-v4-pro-alibaba","object":"model","owned_by":"byNara","context_window":1000000,"weight":4.5,"reasoning":true},
    {"id":"glm-5.2","object":"model","owned_by":"byNara","context_window":1000000,"weight":2,"reasoning":true},
    {"id":"glm-5.2-alibaba","object":"model","owned_by":"byNara","context_window":1000000,"weight":1.1,"reasoning":true},
    {"id":"glm-5.2-promo","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"reasoning":true},
    {"id":"glm-5.3","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"reasoning":true},
    {"id":"glm-5.3-flash","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"glm-5.3-flash-free","object":"model","owned_by":"byNara","context_window":128000,"weight":1,"vision":true},
    {"id":"glm-5.3-free","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"reasoning":true},
    {"id":"gpt-5.4","object":"model","owned_by":"byNara","context_window":1000000,"weight":1.5,"vision":true,"reasoning":true},
    {"id":"gpt-5.5","object":"model","owned_by":"byNara","context_window":1000000,"weight":3,"vision":true},
    {"id":"gpt-5.6-luna","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"gpt-5.6-sol","object":"model","owned_by":"byNara","context_window":1000000,"weight":4,"vision":true},
    {"id":"gpt-5.6-terra","object":"model","owned_by":"byNara","context_window":1000000,"weight":2.5,"vision":true,"reasoning":true},
    {"id":"grok-4.6","object":"model","owned_by":"byNara","context_window":500000,"weight":1,"vision":true,"reasoning":true},
    {"id":"kimi-k2.7-code","object":"model","owned_by":"byNara","context_window":262000,"weight":1,"vision":true},
    {"id":"kimi-k2.7-code-alibaba","object":"model","owned_by":"byNara","context_window":1000000,"weight":0.75,"vision":true,"reasoning":true},
    {"id":"kimi-k3","object":"model","owned_by":"byNara","context_window":1000000,"weight":4,"vision":true,"reasoning":true},
    {"id":"laguna-s-2.1","object":"model","owned_by":"byNara","context_window":262000,"weight":0.5,"reasoning":true},
    {"id":"mimo-v2.5","object":"model","owned_by":"byNara","context_window":1000000,"weight":1.5,"vision":true},
    {"id":"mimo-v2.5-free","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true,"reasoning":true},
    {"id":"mimo-v2.5-pro-ultraspeed","object":"model","owned_by":"byNara","context_window":1000000,"weight":3.5},
    {"id":"minimax-m3","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"minimax-m3-free","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"mistral-large","object":"model","owned_by":"byNara","context_window":252000,"weight":1},
    {"id":"mistral-medium-3-5","object":"model","owned_by":"byNara","context_window":256000,"weight":1,"vision":true},
    {"id":"muse-spark-1.2","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"muse-spark-1.2-contributor","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"muse-spark-1.2-contributor-free","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"muse-spark-1.3","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"muse-spark-1.3-contributor","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"muse-spark-1.3-contributor-free","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"qwen3.7-flash","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true,"reasoning":true},
    {"id":"qwen3.7-flash-alibaba","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true,"reasoning":true},
    {"id":"qwen3.7-max-alibaba","object":"model","owned_by":"byNara","context_window":1000000,"weight":1.25,"reasoning":true},
    {"id":"qwen3.7-plus","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true,"reasoning":true},
    {"id":"qwen3.7-plus-alibaba","object":"model","owned_by":"byNara","context_window":1000000,"weight":1.2,"vision":true,"reasoning":true},
    {"id":"qwen3.8-27b","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"reasoning":true},
    {"id":"qwen3.8-flash","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"qwen3.8-flash-free","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true},
    {"id":"qwen3.8-max","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true,"reasoning":true},
    {"id":"qwen3.8-max-alibaba","object":"model","owned_by":"byNara","context_window":1000000,"weight":1,"vision":true,"reasoning":true},
    {"id":"stepfun-3.7-flash","object":"model","owned_by":"byNara","context_window":262000,"weight":1,"vision":true,"reasoning":true},
    {"id":"deepseek-v4-flash","object":"model","owned_by":"byNara","context_window":1000000,"weight":1.5,"reasoning":true},
    {"id":"deepseek-v4-pro","object":"model","owned_by":"byNara","context_window":1000000,"weight":3,"reasoning":true},
    {"id":"qwen3.7-max","object":"model","owned_by":"byNara","context_window":1000000,"weight":1.2,"reasoning":true},
    {"id":"mimo-v2.5-pro","object":"model","owned_by":"byNara","context_window":1000000,"weight":2.9}
  ]
}
```

Stored verbatim (curl) at research time; `GET /api/plans` response cached alongside. Full `curl -s | python3 -m json.tool` output available in issue artifact (redacted key). No key was committed — all reproductions use `NARAROUTER_API_KEY` env.

---

## Verification steps (reproducible)

```bash
# 1. Unauthenticated error shape
curl -s https://router.bynara.id/v1/models | python3 -m json.tool
curl -s -i https://router.bynara.id/v1/models | head -n 20

# 2. Authenticated raw shape (requires NARAROUTER_API_KEY from Infisical)
infisical export --projectId $LLM_SHARED_PROJECT_ID --env dev --format json | jq
NARAROUTER_API_KEY=$(infisical export --projectId 7686072c-85c7-4b7e-96e5-5bad8086cf44 --env dev --format json | python3 -c "import json,sys; d=json.load(sys.stdin); print(next(s['value'] for s in d if s['key']=='NARAROUTER_API_KEY'))")
curl -s https://router.bynara.id/v1/models -H "Authorization: Bearer $NARAROUTER_API_KEY" | python3 -m json.tool
curl -s https://router.bynara.id/v1/models -H "Authorization: Bearer $NARAROUTER_API_KEY" | python3 -c "import json,sys; j=json.load(sys.stdin); print(len(j['data'])); print(sorted([m['id'] for m in j['data'] if 'free' in m['id']]))"

# 3. Website bucket source (public)
curl -s https://router.bynara.id/api/plans | python3 -m json.tool
curl -s https://router.bynara.id/api/plans | python3 -c "import json; d=json.load(open('/dev/stdin')); print([ (p['code'], p['models']) for p in d['data'] if p['code'] in ('free','freemium')])"

# 4. Compare YAML vs buckets
cat data/results/nararouter.yaml
curl -s https://router.bynara.id/api/plans | python3 -c "import json; d=json.load(open('/dev/stdin')); free=set(next(p for p in d['data'] if p['code']=='free')['models']); print('free plan:', sorted(free))"

# 5. Docs wording
curl -sL https://router.bynara.id/docs | grep -o "Live from[^<]*" | head
```

---

## Sources

- `GET https://router.bynara.id/v1/models` — unauthenticated (401) and authenticated (200) via `curl`, 2026-09-03.
- `GET https://router.bynara.id/api/plans` — public JSON, 2026-09-03 (8 plans, `free` vs `freemium` buckets).
- `https://router.bynara.id/docs` — Models section (live-from-plans note, quota-class description), pricing i18n strings (Base/Lite/Mocin/Pro), `curl -sL` payload 2026-09-03.
- `config/providers.yaml:66-68` — nararouter base_url/secret.
- `src/llm_discovery/discovery.py` — `discover_models` + `_normalize_models` (generic OpenAI path, no nararouter special case).
- `src/llm_discovery/pipeline.py:456-502` — `FREE_MARKERS` / `_split_by_free_rule` (current filter).
- `src/llm_discovery/provider.py` — `resolve_provider` (no strategy override for nararouter).
- `src/llm_discovery/model_matching.py:30` — `normalize_model_id` strips `-free` suffix for AA matching.
- `data/results/nararouter.yaml` — evaluated_at 2026-09-03T09:23:36, 8 models (keep 2, drop 4, error 2).

---

## Comment draft (for gh issue #47 — do not close)

> **Research complete — no close.**
>
> Captured `GET /v1/models` raw shape (200 with `sk-nry-****`, 59 models) and error shape (401 `{"error":{"type":"unauthorized","message":"A valid API key is required."}}`). Raw has **no discriminator**: `GET /v1/models` returns `{"object":"list","data":[{"id","object":"model","owned_by":"byNara","weight","context_window"?,"vision"?,"reasoning"?}]}` — no pricing/tags/quota/tier field. Paid-gated free and true free share `"-free"` suffix and same shape; `weight`/`context_window` not correlated.
>
> **Website buckets via `GET /api/plans` (public, same endpoint docs loads live):**
> - `free` (true free, 0 IDR, 7M cap): `agnes-2.0-flash, agnes-2.5-flash, laguna-s-2.1, minimax-m3-free, mistral-large, mistral-medium-3-5, muse-spark-1.2-contributor-free, qwen3.8-27b, stepfun-3.7-flash` (9)
> - `freemium*` adds paid-gated free: `deepseek-v4-flash-free, glm-5.3-flash-free, glm-5.3-free, mimo-v2.5-free, muse-spark-1.3-contributor-free, qwen3.8-flash-free` (6 delta)
>
> **Pipeline gap:** `config/providers.yaml` nararouter uses generic `openai` discovery; no special handling. `_split_by_free_rule` keeps all 8 `-free` models (mixing buckets) and drops 7 true-free models without `-free` suffix, so `nararouter.yaml` (8 evaluated) is both over- and under-inclusive. Table `model_id → bucket → field/value` proves no in-response field distinguishes — needs second-endpoint allowlist.
>
> **Recommendation:** predicate `id in GET /api/plans → data.find(p=>p.code=="free").models` (live fetch, `free` plan), fallback vendored snapshot (2026-09-03). Implement as `discovery_strategy: nararouter` branch or nararouter-aware `_split_by_free_rule`. Details + full raw capture + repro steps in `docs/research/issue-47-nararouter-free-filter.md`.

---

*Generated 2026-09-03 from live probes. Key redacted (`sk-nry-****`). No auth token committed; repro via `NARAROUTER_API_KEY` env / Infisical `LLM_SHARED_PROJECT_ID`.*
