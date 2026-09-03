<!-- Comment draft for gh issue 47 — paste with `gh issue comment 47 --body-file docs/research/issue-47-comment-draft.md` — do NOT close issue -->

**Research complete — no close.**

Captured `GET /v1/models` raw shape (200 with `sk-nry-****`, 59 models) and error shape (401 `{"error":{"type":"unauthorized","message":"A valid API key is required."}}`). Raw has **no discriminator**: `GET /v1/models` returns `{"object":"list","data":[{"id","object":"model","owned_by":"byNara","weight","context_window"?,"vision"?,"reasoning"?}]}` — no pricing/tags/quota/tier field. Paid-gated free and true free share `"-free"` suffix and same shape; `weight`/`context_window` not correlated.

**Website buckets via `GET /api/plans` (public, same endpoint docs loads live):**
- `free` (true free, 0 IDR, 7M cap): `agnes-2.0-flash, agnes-2.5-flash, laguna-s-2.1, minimax-m3-free, mistral-large, mistral-medium-3-5, muse-spark-1.2-contributor-free, qwen3.8-27b, stepfun-3.7-flash` (9)
- `freemium*` adds paid-gated free: `deepseek-v4-flash-free, glm-5.3-flash-free, glm-5.3-free, mimo-v2.5-free, muse-spark-1.3-contributor-free, qwen3.8-flash-free` (6 delta)

**Pipeline gap:** `config/providers.yaml` nararouter uses generic `openai` discovery; no special handling. `_split_by_free_rule` keeps all 8 `-free` models (mixing buckets) and drops 7 true-free models without `-free` suffix, so `nararouter.yaml` (8 evaluated) is both over- and under-inclusive. Table `model_id → bucket → field/value` proves no in-response field distinguishes — needs second-endpoint allowlist.

**Recommendation:** predicate `id in GET /api/plans → data.find(p=>p.code=="free").models` (live fetch, `free` plan), fallback vendored snapshot (2026-09-03). Implement as `discovery_strategy: nararouter` branch or nararouter-aware `_split_by_free_rule`. Details + full raw capture + repro steps in `docs/research/issue-47-nararouter-free-filter.md`.
