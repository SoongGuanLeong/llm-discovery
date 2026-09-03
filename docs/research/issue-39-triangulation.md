# Triangulation for no-AA / claim-only models (issue #39)

Part of #34 — Map: Raise evidence levels and fix version-dot normalization. Blocked by #35 (closed).

## Question

For models with no AA match and no benchmarks (gpt-oss, mistral-Nemo-Instruct-2407, mistral-Small-24B, seed-2.0-mini, chroma-v.46-flash), what evidence can lift them above weak without hallucinating? Decide the triangulation policy:

- provider-native exception in llm.py SYSTEM_PROMPT ("may be kept without AA when reliable first-party docs establish coding") — does it apply and how to verify?
- web search fallback: how many searches, which queries, and how to record source URLs in evidence vs raw_benchmarks?
- when to accept "provider model card states support for multiple coding languages" as moderate vs requiring a benchmark number?

Blocked by: Audit evidence_level assignment for weak entries in llm7.yaml

## Snapshot

- 5 target models at audit time: all `evidence_level: weak` or `moderate` with no AA, no coding benchmarks, empty `benchmarks: {}`.
- Current SYSTEM_PROMPT already contains provider-native exception but without verification checklist or URL recording rule → LLM hedging / hallucination risk.
- Current deterministic `PolicyGate._deterministic_evidence_level` returns `weak` for claim-only (no AA, no benchmark >=30) → final = max(LLM moderate, weak) = moderate if LLM claims it. No guard against hallucinated moderate.
- `EvidenceCollector` captures `provider_claims` from `models_dev` descriptions but not from web search results.

## Decision

### 1. Provider-native exception — applies, but narrowly and verifiably

**Applies when ALL hold:**

1. **Provider-native**: `provider_name` matches the model creator's organisation (not a third-party re-host). Example: `mistral/*` from provider `mistral`, `gpt-oss` from `openai`, `seed-*` from `bytedance/seed`. Re-hosts like `openrouter/mistral-nemo` do not qualify.
2. **First-party domain**: the doc URL domain belongs to the provider. Allowlist examples:
   - openai: `openai.com`, `platform.openai.com`
   - mistral: `mistral.ai`, `docs.mistral.ai`
   - bytedance/seed: `seed.bytedance.com`, `bytedance.com`
   - chroma: `docs.trychroma.com`, `trychroma.com` (but see specialization)
   - xiaomi/mimo: `xiaomi.com`, `mimo` docs domain when provider matches
   Any URL outside provider domain is supporting only, not exception-qualifying.
3. **Doc content**: snippet explicitly mentions coding capability — contains at least one of `coding`, `code generation`, `software engineering`, `programming` AND either lists >=2 programming languages (Python, JavaScript, etc.) or states agentic coding.
4. **Evidence recording**: evidence item must include the source URL as `(source: <url>)`. No URL → unverified → exception does not apply.

**Does NOT apply to:**

- Compound systems, tool wrappers, safety models, speech/audio, embedding, vision, reranker — flagged by `EvidenceCollector.deterministic_flags` (`specialized_model:*`). `chroma-v.46-flash` is vector-DB / vision-adjacent → stays weak regardless of claim.
- Mini / small / lite / nano variants — per existing SYSTEM_PROMPT rule: `seed-2.0-mini`, `mistral-Small-*` require verified AA >= min_score (24) plus reliable coding evidence; provider claim alone never lifts them to moderate. They stay weak/drop deterministically.

**Applies to (if verified):**

- `gpt-oss` — OpenAI open-weight model; first-party card at `openai.com` stating coding + multi-language support → moderate if verified, strong only with benchmark number.
- `mistral-Nemo-Instruct-2407` — Mistral AI; first-party card at `mistral.ai` stating coding → moderate if verified.
- `mistral-Small-24B-Instruct-2501` — blocked by mini/small rule → stays weak even if card lists languages; needs AA or benchmark >=30.
- `seed-2.0-mini` — blocked by mini rule → stays weak.
- `chroma-v.46-flash` — specialized → stays weak.

### 2. Web search fallback — 2 searches, fixed queries, strict URL recording

**Budget:** at most 2 `search_web` calls per model (already enforced in `llm.py:LocalLLMEvaluator.max_searches=2` and SYSTEM_PROMPT "perform at most 2 web searches").

**Queries (in order):**

1. `\`{model_id} model card coding programming languages\`` — targets provider card / HF model card stating language support. Prefer adding `site:<provider-domain>` when provider domain known (e.g., `mistral-nemo site:mistral.ai`).
2. `\`{model_id} coding benchmark HumanEval SWE-bench LiveCodeBench\`` — targets numeric benchmark. Only if first query does not yield benchmark number, or when verifying benchmark-backed strong.

Do not use generic queries like `\`{model} capabilities\``; they waste the budget.

**Recording:**

- **evidence (LLM output)**: each evidence item is one short sentence plus source URL in parentheses: e.g., `"Supports Python, JS, Rust per model card (source: https://mistral.ai/news/mistral-nemo)"`. Max 2 items, each under ~120 chars + URL. Never invent URL; copy exact `url` field from `search_web` result.
- **raw_benchmarks**: if a numeric benchmark is found (e.g., HumanEval 72.4), record it in the benchmark pipeline as `raw_benchmarks` entry with `source` = the same URL. `evidence` remains the human-readable summary; `raw_benchmarks` is the structured number. If only a claim (no number), record only in `evidence`, not in `raw_benchmarks`.
- **benchmarks / coding_score**: only numeric entries mapped via `BENCHMARK_NAME_MAP` contribute to `BenchmarkProfile` and `coding_score`. A claim-only moderate does not create a synthetic benchmark score.

**Hallucination guard (deterministic):**

Added in `PolicyGate.apply`: if `aa_score is None` and no benchmark scores and `llm_result.evidence_level == "moderate"` and none of the evidence strings contains `http`, the level is demoted to `weak` with note "Unverified claim-only moderate without source URL demoted to weak". LLM strong is never demoted; only unverified moderate is guarded.

### 3. Claim-only "supports multiple coding languages" — moderate vs weak

| Evidence | Verdict | Rationale |
|---|---|---|
| First-party URL verified, snippet lists >=2 languages (e.g., "Supports Python, JavaScript, Java, Rust"), provider-native, not mini/specialized | **moderate** | Meets provider-native exception with multi-language coding capability; no benchmark number needed for moderate. |
| First-party URL verified but lists only 1 language or vague "supports coding" without language list | **weak** | Insufficient specificity; keep weak, choose drop unless other signal. |
| Third-party URL (HF mirror, blog, leaderboard) lists languages | **weak** | Not first-party; supporting only. Needs benchmark number to reach moderate. |
| No URL in evidence | **weak** | Unverified → hallucination risk. Guard demotes moderate → weak. |
| Any single benchmark number >=30 (HumanEval, SWE-bench, LiveCodeBench, etc.) with source URL, even without language list | **moderate** (deterministic: `Any benchmark >=30 → moderate`) | Numeric signal alone suffices per threshold table. |
| Benchmark number >=50 or coding_score >=45 | **strong** | Per threshold table. |

**Examples:**

- Mistral Nemo card: "Nemo is a 12B model ... supports function calling, code generation in Python, Java, JavaScript (source: https://mistral.ai/news/mistral-nemo)" → moderate (if provider-native and not blocked by variant rule).
- gpt-oss card: "GPT-OSS ... excels at coding tasks across Python, JS, Go (source: https://openai.com/index/gpt-oss)" → moderate.
- Seed-2.0-mini card: same language list but mini rule → weak (stays drop) even if verified.
- Chroma same → weak (specialized).

## Implementation

### Code changes

1. **src/llm_discovery/llm.py:SYSTEM_PROMPT** — replace vague provider-native paragraph with check-list version above, add triangulation queries and URL recording rule, and tighten moderate definition to require verified source URL + >=2 languages.
2. **src/llm_discovery/policy_gate.py** — add unverified-moderate guard after hybrid promotion: demote claim-only moderate without http URL to weak (never demote strong). Keeps deterministic `weak` as base but prevents hallucinated LLM moderate.
3. **No change to `EvidenceCollector`** — provider_claims remain models_dev-only; web-verified claims flow via LLM evidence URLs, not via collector. `BenchmarkDataCache` mapping unchanged.

### Tests

- Unit: `test_policy_gate_triangulation_unverified_moderate_demoted` — no-AA, no benchmarks, LLM moderate without URL → final weak.
- Unit: `test_policy_gate_triangulation_verified_moderate_kept` — same but evidence contains `https://mistral.ai/...` → final moderate.
- Unit: `test_policy_gate_mini_variant_stays_weak` — seed-2.0-mini with verified URL but mini rule via deterministic_flags → weak/drop (tier drop already; evidence stays weak).
- Prompt test: SYSTEM_PROMPT contains "first-party", "source: <url>", "at most 2", and the two query templates.

### Rollout

- Forward-only via PolicyGate and LLM prompt; no YAML backfill until next `discover_provider` run.
- Existing weak entries without URLs remain weak (no promotion). Verified claim-only models promote to moderate on next evaluation.

## Alternatives considered

- **Deterministic provider_claims → moderate**: rejected — provider_claims from models_dev alone are not verified first-party; would hallucinate moderate for unvetted claims.
- **Add first-party domain allowlist to EvidenceCollector**: deferred — verification is LLM-side via web search; deterministic side only guards unverified moderate, does not parse domains. Allowlist lives in prompt, not code.
- **Increase search budget to 3-4**: rejected — cost and latency; 2 queries cover card + benchmark. EvidencePacket already carries AA + pricing, so 2 searches suffice for claim-only.

## Consequences

- gpt-oss / mistral-Nemo can reach moderate on next run if first-party card verified; otherwise stay weak/drop — no hallucination.
- Mini/small/lite and specialized models never reach moderate via claim alone — consistent with existing drop-unless-verified-AA rule.
- Benchmark-backed strong still requires numeric threshold; claim-only never reaches strong.
