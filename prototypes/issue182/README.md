# Prototype #182 — Single-page wireframe + interaction states + log panel contract

Part of Wayfinder map #180.

## Asset

- **Interactive mock**: `index.html` — open in browser, no backend.
- Single static HTML+JS, no npm, no build. Covers all 5 areas + guide.

## What is mocked

1. **OmniRoute API key** — masked input, Show/Hide, Save to .env toast, badge (not set / saved *** / too short? / cleared).
2. **OmniRoute export** — gateway URL (default http://localhost:20128), Dry Run (safe) + Apply (confirm) + Cancel, state dot + log panel, http(s) validation.
3. **Build all** — All checkbox (22, live from config/providers.yaml), filterable multi-select, Advanced (workers 8, catalog 28, --no-catalog-refresh), Run + Cancel, badge + log.
4. **Folder icon to config/providers.yaml** — copy path, Open (code -> xdg-open -> open fallback), file:// link. Phase 1; inline editor phase 2.
5. **Prerequisites guide** (from #181) — OmniRoute + Infisical blocks with install cmds + links + offline fallback.

## States per log panel

| State | Dot | Log panel | Buttons |
|-------|-----|-----------|---------|
| empty | gray | Placeholder No logs yet | Run enabled, Cancel disabled |
| running | pulsing blue | SSE GET /api/logs/{jobId} -- {type:'stdout'|'stderr', line, ts} | Run disabled, Cancel enabled |
| success | green | Last ~500 lines + {type:'done', exitCode:0} green | Run re-enabled |
| error/canceled | red | stderr red + killed red | Run re-enabled + toast |

Cancel = SIGTERM -> 2s -> SIGKILL, SSE {type:'killed'}. Key redacted ***.

## Demo

Bottom card 8 buttons toggle export/build empty/running/success/error visuals.
