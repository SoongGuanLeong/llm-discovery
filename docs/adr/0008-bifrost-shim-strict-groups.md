# 0008 Bifrost + Shim Strict Groups (flash/max/contributor_free)

Date: 2026-09-08
Status: Accepted — Issue #155 (Bifrost UI discoverability)

## Context

Pipeline verified end-to-end ( #148-#150): Infisical → providers.yaml → 132 keeps → Bifrost :8080 (19 providers) → DSH via llm-pi-ai. Previously omniroute provided single-config virtual model pools. Bifrost has no native alias groups; grouping done via generator + shim sidecar.

Need groups user can select in DSH (flash / max / contributor_free) with intra-group routing only. Auto-routing policy deferred.

## Decision

- **Placement:** Keep Bifrost + shim sidecar (`data/bifrost/config.json` + `shim_map.json` + provider env refs + `prototypes/dsh-bifrost-wiring/run_sidecar.sh` on :8081 → :8080). DSH `llm-pi-ai` baseUrl :8081 for alias, :8080 for direct pin. No DSH internal router.
- **Group source:** `categorize.py` tier field stays source of truth. `group_keeps_by_tier` pure transform, keep-all, strict `contributor` substring for `contributor_free`. Current pools 46/84/2.
- **Isolation:** Strict — no cross-group fallback. Shim uniform weighted pick within pool only. User switches group by changing model id. Empty pool → 503.

## Alternatives

- DSH internal router: requires fork, rebuild, HMR complexity — rejected.
- Native Bifrost alias: not supported today, would need upstream feature — deferred.
- Single-file omniroute-like config: would need Bifrost extension — out of scope.

## Consequences

- Extra process :8081, two artifacts to keep in sync (`config.json` + `shim_map.json`).
- `setup.sh --check` must verify both + sidecar health.
- Secrets stay 0600 env refs (`env.VAR`), never inline.
- DSH UX parity with omniroute preserved (3 models).

## Deferred

Policy ranking (cost/quality/latency), dynamic per-request routing, observability/metrics, pricing TTL re-rank — next Wayfinder map after groups verified live.

## Verification

curl each alias via :8081 + DSH chat each group streams completion, no fallback. Bifrost /api/models =132, 19 providers.

## Discoverability (added in #155)

- Bifrost gateway UI at `http://localhost:8080` shows 132 concrete models across 19 providers; groups are the tier-derived pools (46/84/2) materialized as those models.
- Shim sidecar on `:8081` augments `GET /v1/models` (OpenAI) and `GET /api/models` (Bifrost) with virtual entries `flash`/`max`/`contributor_free` (owned_by `bifrost-shim`) so model pickers discover groups via standard API.
- `GET /health` on `:8081` reports `{tiers: {flash:46, max:84, contributor_free:2}}` matching `shim_map.json`.
- Browser smoke: open `http://localhost:8080` (gateway UI) and `http://localhost:8081/health` + `/v1/models` show groups; no cross-group fallback verified via 503 `tier_unavailable` + curl per tier.
