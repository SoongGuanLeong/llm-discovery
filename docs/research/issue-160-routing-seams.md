# Research #160 — OmniRoute model routing seams

Part of #158 Wayfinder

## Question
How to deterministically control "which model we pick for each provider" in OmniRoute?

## Findings (sources: autoCombo docs, provider routing code, modal wiring, raw registry)

### 1. Direct provider/model syntax (primary pin)

At `POST /v1/chat/completions` with `model: "groq/llama-3.3-70b"` or `"provider/model:tag"`. Router splits on `/`, looks up provider connection by provider id, forwards to that provider's baseUrl with given model id. No combo needed. Deterministic single-target. Works for any provider, including custom OpenAI-compatible (baseUrl override).

### 2. Per-connection defaultModel / Available Models

Each provider_connections row has `defaultModel` (string) + available models list (`provider_models` table). UI: Providers -> [provider] -> Available Models -> Import from /models, Auto-Sync toggle, Custom Models. API: `POST /api/provider-models {provider, modelId, modelName}`, `GET /api/provider-models?provider=`, `DELETE`. Also `GET /api/combos/auto` not relevant.

When request uses bare model id without provider prefix, router resolves via getModelProviderMapping; when uses `auto` pool, it pulls defaultModel or first available model per connection (open-sse/services/autoCombo/virtualFactory.ts: "Determines the model per connection (connection.defaultModel or provider's first model)").

Import file does NOT set defaultModel — separate PATCH required: `PUT /api/providers/[id] {defaultModel: "model_id"}` (or provider-models default flag). Without setting, auto pool picks first model arbitrarily.

### 3. Persisted combos (deterministic multi-target + shadowing)

Combos are named collections stored in `combos` table, managed at Settings -> Combos and via `POST /api/combos {name, targets: [{provider, model}]}` etc.

Resolution order in `src/sse/services/model.ts#getComboForModel`:
1. exact combo-name match (`model: "my-combo"`)
2. `combo/<name>` prefix
3. model->combo glob mappings (/api/model-combo-mappings)

Combo-before-rewrite precedence (built for #3227/#3233) + regression tests `tests/unit/responses-combo-resolution-3227.test.ts`.

Shadowing pattern (#6940 documented in AUTO-COMBO.md): combo whose name equals a bare model id (e.g. combo named "gpt-5.5") intentionally shadows that model id — request for bare "gpt-5.5" routes through combo targets (e.g. acme-responses/gpt-5.5) instead of single provider. Creating such combo returns {warning: {code: "COMBO_NAME_SHADOWS_MODEL"}} non-blocking, and boot logs scanComboModelNameCollisionsAtBoot. Supported, not bug.

Combo targets carry provider + model + optional priority/weights. Strategies: priority, weighted, LKGP, etc.

### 4. Auto virtual combos (zero-config pool)

Model prefix `auto` detected in `src/sse/handlers/chat.ts` -> `createVirtualAutoCombo(channel)` builds virtual AutoComboConfig in-memory (not DB persisted) from all active provider connections with valid credentials (virtualFactory.ts). Variants:

- `auto` balanced (LKGP), `auto/coding` quality-first, `auto/fast`, `auto/cheap`, `auto/offline`, `auto/smart`, `auto/lkgp`
- Category x tier composition: `auto/<category>:<tier>` where category = coding/reasoning/vision/chat/multimodal, tier = fast/cheap/reliable/free/pro. Example `auto/coding:fast` etc. Fail-open: if constraint matches no models, full pool used.

Scoring: 15-factor weighted scoring (scoring.ts DEFAULT_WEIGHTS sum 1.0): quota 0.1429, health 0.1605, costInv 0.1429, latencyInv 0.1143, taskFit 0.0762, stability/tierPriority/tierAffinity/specificityMatch/contextAffinity/sessionAvailability/connectionDensity 0.0476 each, quality 0.03, cacheAffinity/resetWindow 0 disabled.

Per-key candidate control (#7819): GET /v1/auto-combo/{channel}/candidates lists pool decorated with circuit breaker / cooldown / lockout; exclusions per-apiKey stored in auto_candidate_overrides table, filterExcludedCandidates fail-open.

Auto does NOT give deterministic per-provider pin — it chooses among pool via scoring. To pin, exclude all but one candidate per channel (per-key overrides) or avoid auto and use combo/provider/model instead. Recommendation: avoid auto for deterministic pin.

### 5. Programmatic control surface

- Provider create/update: `POST /api/providers {provider, apiKey, name, baseUrl}`, `PUT /api/providers/[id]`
- Provider-models: `POST /api/provider-models`, management via UI Available Models
- Combos: `POST /api/combos`, `PUT /api/combos/[id]`, `GET /api/combos`, `DELETE`
- Auto candidates: `GET /v1/auto-combo/{channel}/candidates` (read-only)

All require management API key (OMNIROUTE_MANAGE_KEY or Bearer dashboard session). Not via import file.

## Answer

Deterministic per-provider pin has two good seams:

- **Simplest**: call `model: "provider/model_id"` directly (e.g. "groq/llama-3.3-70b-versatile", "kilo_ai/minimax-m2.7:free"). No extra config, maps 1:1 from keep list. Works for both registry and custom baseUrl providers.
- **Combo pin** (when need fallback or friendly alias): create persisted combo per-tier or per-provider, e.g. combo "flash" with targets [{provider:"groq", model:"llama-3.3-70b"}, {provider:"kilo_ai", model:"minimax-m2.7:free"} priority order], then call `model: "flash"` or `model: "combo/flash"`. Also enables shadowing if want bare model id to route via combo.

Set per-connection defaultModel to keep model if want auto pool to default to that model, but still not deterministic under auto scoring — prefer explicit provider/model or combo.

Prototype #163 should emit combo definitions (targets = provider + keep model_id) as primary artifact; direct provider/model is fallback without combos.
