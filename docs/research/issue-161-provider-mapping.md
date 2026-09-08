# Research #161 — Provider id mapping vs OmniRoute 352 registry

Part of #158 Wayfinder

## Method
Compared 22 config/providers.yaml ids against OmniRoute registry snapshot 2026-08-25 (PROVIDER_REFERENCE.md, open-sse/config/providers/registry/`id` folders). Checked exact id, alias, and fallback to custom OpenAI-compatible provider type.

## Mapping table

| providers.yaml id | OmniRoute id / alias | Match | Notes |
|---|---|---|---|
| agnes | agnes | exact | registry/agnes exists, OpenAI-compatible |
| ainative | ainative | exact | registry/ainative |
| bazaarlink | bazaarlink? | likely | discovery_strategy bazaarlink, registry may have bazaarlink (check) |
| cerebras | cerebras | exact |  |
| cloudflare | cloudflare / cloudflare-playground | partial | OmniRoute has cloudflare-playground no-auth; paid Cloudflare AI may need custom baseUrl with account id template https://api.cloudflare.com/client/v4/accounts/${ID}/ai/v1 |
| cohere | cohere | exact |  |
| google | google / gemini | alias | OmniRoute may use gemini id, alias google — verify alias list |
| groq | groq | exact |  |
| kilo_ai | kilo_ai? | check | registry contains kilo_ai or similar; fallback custom OpenAI compatible if not |
| llm7 | llm7 | exact? | check registry |
| mistral | mistral | exact |  |
| modelscope | modelscope | exact? |  |
| nararouter | nararouter? | check | likely exists (nara?) |
| navy_ai | navy_ai | check |  |
| nvidia_nim | nvidia_nim / nvidia | alias | registry has nvidia or nvidia_nim |
| ollama_cloud | ollama_cloud / ollama | exact/alias | registry has ollama |
| opencode_zen | opencode_zen / opencode | partial | opencode free exists as opencode id, zen variant may need custom baseUrl |
| openrouter | openrouter | exact |  |
| reka | reka | exact |  |
| requesty | requesty | exact? |  |
| sea-lion | sea-lion | exact? |  |
| zai | zai / z.ai | exact/alias |  |

## Detailed findings (spot-check via registry listing)

From recursive tree listing (64+ registry dirs seen):
- agnes, ainative, cerebras, cohere, cohere, groq, mistral, reka, sea-lion etc confirmed exact.
- cloudflare: registry has ai21 etc but cloudflare id exists as provider (need verify).
- google: OmniRoute likely uses "google" or "gemini" — both map to generativelanguage.googleapis.com. providers.yaml base_url https://generativelanguage.googleapis.com/v1beta/openai maps directly; may need to keep baseUrl override.
- kibo: kilo_ai, llm7, nararouter, navy_ai, nvidia_nim, ollama_cloud, opencode_zen, requesty, zai — need per-item verification by curling registry folder existence.

### Custom OpenAI-compatible fallback

OmniRoute supports "Custom OpenAI-compatible" provider type: when provider id not in catalog, Bulk import will fail validation (bulkImportProviderSchema checks catalog). Workaround: use generic provider id "openai-compatible" or "custom" with baseUrl override — docs: "Another OmniRoute gateway can be added as Custom OpenAI-compatible provider". For unknown ids, map to provider="openai" with baseUrl set to original base_url. This loses provider-specific header profiles (providerHeaderProfiles.ts) but retains OpenAI chat completions path.

Priority: keep original provider id when registry exists, only fallback to openai-compatible for gaps.

## Gap list / actions

- Verify each of the 22 ids via `curl -s https://raw.githubusercontent.com/diegosouzapw/OmniRoute/main/open-sse/config/providers/registry/<id>/index.ts | head` — done for ~12, remaining 10 need live check in prototype.
- cloudflare account-id templated baseUrl: providers.yaml has ${CLOUDFLARE_ACCOUNT_ID} placeholder — OmniRoute import baseUrl field can carry resolved URL or must template per-connection? Need to test — likely requires resolved baseUrl, not env template.
- opencode_zen vs opencode: registry opencode is free no-auth; zen needs api key + baseUrl https://opencode.ai/zen/v1 — test if registry has opencode_zen separate entry or requires custom.
- Secrets: Infisical env names map 1:1 to apiKey values — no provider needs separate OAuth vs apiKey handling; all are API key type except maybe cloudflare account id + key.

## Recommendation for prototype

Generate mapping JSON: { "providers_yaml_id": "omniroute_id", "baseUrl": "override or null", "requiresCustom": bool }. Emit import JSON using omniroute_id (or openai with baseUrl for gaps). Keep original name as connection name for traceability.
