# Research #159 — Import Providers from file seam

Part of #158 Wayfinder: Wire llm-discovery to OmniRoute with per-provider model control

## Question
Does OmniRoute "Import providers from file" support per-provider model pin, or is model control separate seam?

## Findings (primary sources)

### Parser: `src/app/(dashboard)/dashboard/providers/components/parseProviderImportFile.ts` (main@2026-09-07)

Supported inputs per file header comment:

- **CSV**: `provider,name,apiKey,baseUrl(optional),priority(optional)` — one row per provider connection. Header detection: first non-blank/non-comment line whose first col == "provider" case-insensitive skipped. Blank/`#` comment lines skipped. Split on `,` naive, trim each.
- **JSON**: array of `{ provider, name, apiKey, baseUrl?, priority? }` objects.

Validation in `pushParsedEntry`:
- provider required non-empty string else `importErrorMissingProvider`
- name required else `importErrorMissingName`
- apiKey required else `importErrorMissingApiKey`
- priority optional: parse to number 1..100 else `importErrorInvalidPriority`, absent -> undefined
- baseUrl optional string trim, omitted if empty
- Deliberately NO validation of provider id against catalog — comment: "that check belongs server-side in bulkImportProviderSchema (src/shared/validation/schemas/provider.ts) so parser stays pure"

Output type: `ParsedProviderImportEntry[]` + per-row `ProviderImportParseError[]` + skipped count. No model field.

### UI: `ImportProvidersFromFileModal.tsx`

Wizard: file picker (.csv/.json) -> parse -> table (col provider, name, baseUrl) + per-row checkbox selection + errors list -> batch submit via `useImportProvidersFromFile.ts` (POST bulk endpoint, returns {success, failed}). Mirrors `ProxyBulkImportModal` UX. No model column rendered, no model input.

### Server: bulkImportProviderSchema (`src/shared/validation/schemas/provider.ts`)

Validates provider id exists in registry (352 entries). baseUrl validated as URL when present, priority 1..100. No model field accepted — unknown keys stripped. Resulting row inserted into `provider_connections` table (id, provider, name, apiKey encrypted, baseUrl, priority, plus defaultModel/available models separate columns not populated by this endpoint).

### provider_connections row shape (from registry + provider CRUD)

Typical row: { provider: "groq" | "cerebras" | "openai" etc, name: string, apiKey: encrypted, baseUrl?: string, priority?: number, defaultModel?: string, enabled: bool }. Import endpoint only sets first 4; defaultModel stays null until user configures via separate UI (Available Models -> Default Model dropdown) or via provider-models API.

## Answer

**Import alone cannot pin model per provider.** Wire requires two steps:

1. Bulk import = connections only. Answers "which provider accounts exist" + auth + optional baseUrl/priority.
2. Second seam for model pin (separate endpoint/UI): set per-connection `defaultModel` via `/api/provider-models` or create persisted combo targeting `provider/model`. See #160.

For llm-discovery, generate CSV/JSON from `config/providers.yaml` (name=provider, baseUrl, secret env value -> apiKey) to create connections, then emit combo/defaultModel payload from keep lists as step 2. Import file cannot carry model intent today; adding model field would require parser + schema + modal change (not recommended — keep single-purpose).

## Implications for #162/#163

Prototype #163 should emit two artifacts: (1) import JSON for modal, (2) combo JSON or provider-models PATCH. Priority field can encode tier if desired (e.g. max=10 flash=50) but does not affect model selection — priority affects routing order only.
