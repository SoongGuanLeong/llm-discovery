# Research #136 — Big-picture gap scan — what convenient existing things are we missing?

**Issue:** [#136 Big-picture gap scan](https://github.com/SoongGuanLeong/llm-discovery/issues/136) — child of Wayfinder map [#132](https://github.com/SoongGuanLeong/llm-discovery/issues/132)
**Type:** grilling (HITL) + domain-modeling
**Date:** 2026-09-06
**Participants:** user + agent (3 rounds)

---

## User decisions (grilling rounds)

- **Q1 priority = (b) build_all speed/cost** — focus is making `build_all` faster; it currently takes a lot of time.
- **Q2 Bifrost native = keep file → DB sync + drift check** (recommended). No DB-only migration.
- **Q3 catalog refresh = yes to timer + staleness gate** (recommended).
- **Q4 LiteLLM/OpenRouter aggregation = keep bespoke discovery** — user clarified: openrouter is one provider in `config/providers.yaml` (22 providers), not an aggregator alternative. Bifrost is the serving gateway; discovery stays bespoke per-provider `httpx` + `discovery_strategy` (bazaarlink, cloudflare, etc.). No LiteLLM for discovery; judge stays `agnes-2.0-flash` at `apihub.agnes-ai.com`.
- **Q5 observability = yes but speed-first** — want faster runs first; telemetry secondary.
- **Q6 Python/quadlet tooling = keep recommendation** — no extra `uv` cache mount; `setup.sh` + `uv` already fast.

Domain note: `flash`/`max`/`contributor_free` are shim aliases in `shim_map.json` (`generator.group_keeps_by_tier` / `shim.py:pick_model_for_tier`), not Bifrost provider models — separation intentional per #137.

---

## Already covered (not gaps)

Per ADRs 0005–0007 and #133–135 research:

- **Store TTL / gate / invalidation:** ADR 0005 (slim v2 persistence, atomic write + fcntl), ADR 0006 (strong-only Keeper, `is_accurate_enough` floors), ADR 0007 (ranked signals: Identity → Model-List Churn → Pricing TTL 14d re-average → Time TTL; Evidence Delta disabled). `build_all` selective reuse via `get_if_fresh` + `aggregate_pricing` re-average, not LLM.
- **Bifrost health:** `config/quadlet/bifrost.container` already has `HealthCmd=curl /health`, `HealthInterval=30s`, `Restart=on-failure`, `TimeoutStartSec=900`.
- **Secrets:** `scripts/setup.sh` 6-step reconcile (Infisical check-only, `podman secret type=env` or `--secrets=file` fallback, diff-before-copy quadlet, daemon-reload only on change) per #122–124.
- **Bottleneck reality:** #135 profile — LLM/API bound 60–80% (judge), sequential provider loop, `max_workers=4` cap; file I/O <1%, SQLite saves <1%.

---

## Prioritized gap list (effort / impact, speed-first)

Impact rated on wall-clock for a full `build_all` over 22 providers (~314 keeps today); effort = code/config risk.

### P0 — Do next (speed wins, per #135 + user Q1b/Q5)

| # | Gap | What to do | Effort | Impact | Notes |
|---|-----|------------|--------|--------|-------|
| 1 | **Sequential providers** | Parallelize `discover_provider` loop in `build_all.py` (ThreadPool or async) with bounded concurrency (e.g. 3–4 providers parallel). Store already fcntl+atomic; backfill+GC stay sequential tail. | M (concurrency guard, telemetry per-provider) | **20–30%** warm | Biggest structural win after TTL. No SQLite needed. |
| 2 | **`max_workers` 4→8** | Change default `max_workers=4` → 8 in `build_all.py:main` + `discover_provider` (per-provider ThreadPool for `evaluate_model`). | S (one-line) | **30–50%** per-provider judge | Judge is 60–80% of wall; doubling workers halves judge wait. Already proven in #135. |
| 3 | **Warm TTL hit rate** | Ensure `get_if_fresh` path is hit: keep 14d TTL via `StoreMeta.last_updated`, reuse `build_cached_keep_record` verbatim when fresh, only `_refresh_pricing_if_stale→aggregate_pricing` when stale. No LLM for pricing-only. | S (already implemented, just verify) | **40–60%** warm vs cold | This is the real win per #134/#135; SQLite does not move this. |

**Combined P0:** 3–5× warm vs cold (#135 estimate). Implement 2+3 first (minutes), then 1.

### P1 — Low effort, high convenience (operate + catalog, per Q2/Q3)

| # | Gap | What to do | Effort | Impact | Notes |
|---|-----|------------|--------|--------|-------|
| 4 | **Catalog staleness** | Add systemd timer `config/quadlet/refresh-catalogs.{service,timer}` (`OnCalendar=daily`, `ExecStart=python scripts/refresh_catalogs.py`) + `build_all` pre-check: if `fetched_at` >14d, refresh before pricing re-average (ADR 0007 rank 6). | S (2 quadlet files + 10-line check) | Medium — prevents stale pricing re-average | `refresh_catalogs.py` / `src/llm_discovery/refresh.py` already atomic+backup. |
| 5 | **Bifrost drift** | Add `scripts/generate-bifrost-config.py --check` drift check: compare `data/bifrost/config.json` mtime/providers vs live `config.db` (`/api/models` count) as in #133. Fail CI or `setup.sh --check` if stale. | S (20 lines) | Medium — prevents #133 stale DB (16 vs 20 prov, 304 vs 314 models) | Keep file→DB sync; no DB-only migration. |
| 6 | **Derived `cache.db` for DBeaver** | Already decided #137: `data/derived/cache.db` Hybrid (JSON canonical, SQLite derived at `build_all` tail). `models(key PK WITHOUT ROWID, benchmarks JSON, pricing JSON, last_updated)` + `idx_last_updated`, WAL+busy_timeout, atomic replace. Gitignore `data/derived/`. | S (60-line `derived_cache.py` + 3-line tail wire) | Low perf, high convenience | User recommendation adopted; no wholesale SQLite. |

### P2 — Nice-to-have (observability, per Q5 speed-second)

| # | Gap | What to do | Effort | Impact | Notes |
|---|-----|------------|--------|--------|-------|
| 7 | **Structured telemetry** | Extend `build_all` telemetry (already prints `discovered/reused/rebuilt/gc`) to JSONL `data/results/build_telemetry.jsonl` or append to `derived/cache.db` `telemetry` table. Keep console print; add `--json` flag. | S | Low perf, medium debug | `logs.db` stays Bifrost request logs; do not mix with build telemetry. |
| 8 | **Bifrost logs retention** | `data/bifrost/logs.db` 444K + WAL today; no rotation. Add `VACUUM` or retention policy in Bifrost docs; not llm-discovery code. | XS | Low | Host-side, not Podman `nobody` WAL contention. |

### P3 — Skip (per Q4/Q6 grilling)

- **LiteLLM / SDK aggregation for discovery:** Skip — bespoke per-provider `base_url` + `secret` + `discovery_strategy` needed for free-tier filtering; OpenRouter is just one of 22 providers (`config/providers.yaml:22`), not a replacement. Judge stays `judge_llm: agnes-2.0-flash`.
- **`uv` cache / Podman health for `build_all`:** Skip — `setup.sh` already detects `uv` + `podman secret type=env`; no image build in `build_all`; `HealthCmd` already in quadlet. Add only if CI bottleneck measured.
- **Wholesale SQLite / DuckDB / SQLite+FTS for speed:** Skip — file I/O <1% (#135); hybrid `cache.db` is convenience, not speed (see #134).
- **Managed Bifrost image vs quadlet:** Keep quadlet — already canonical with `Volume=./data/bifrost: Z` patched by `setup.sh`.

---

## Hand-off checklist (implementer order)

- [ ] **P0-2:** `build_all.py` default `max_workers=8` (one-line) — immediate.
- [ ] **P0-1:** Parallel providers in `build_all.py` (bounded pool, 3–4 parallel) — keep store fcntl+atomic, backfill+GC sequential.
- [ ] **P1-4:** Add `refresh-catalogs.service/.timer` + `build_all` catalog `fetched_at` gate (>14d → refresh).
- [ ] **P1-5:** Add drift check `generate-bifrost-config.py --check` + wire into `setup.sh --check`.
- [ ] **P1-6:** Implement `src/llm_discovery/derived_cache.py` + tail wire in `build_all.py` (warn-only on failure) per #137 spec.
- [ ] **P2-7 (optional):** JSONL telemetry sidecar.
- Verify: full `build_all` warm run time before/after (expect 3–5× warm), `sqlite3 data/derived/cache.db "SELECT count(*) FROM models"` matches `jq '.models|length' data/model_info_store.json`, `config.json` 20 prov vs `config.db` drift 0.

---

## References

- CONTEXT.md (Source of Truth, Ephemeral Report, Keeper, TTL, etc.)
- ADR 0005 (persistence), 0006 (gate), 0007 (invalidation)
- #133 bifrost visibility, #134 SQLite, #135 build_all profile, #137 seam (`cache.db` spec)
- `config/providers.yaml` (22 providers including openrouter), `config/quadlet/bifrost.container`, `scripts/setup.sh`, `scripts/refresh_catalogs.py`, `src/llm_discovery/build_all.py`, `data/bifrost/*.db`
