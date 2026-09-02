# ADR 0001: Retire evaluate.py (AA-only variant)

## Status
Accepted — T5 Deodorise (issue #6)

## Context
`src/llm_discovery/evaluate.py` implemented `evaluate_models()` — a deterministic
AA Artificial Analysis Intelligence Index filter (score >= min_score → keep, else drop).
No LLM judge, no benchmarks, no policy gate, no evidence collection. It predates the
thin-coordinator pipeline (`pipeline.evaluate_model` → `EvidenceCollector` +
`ModelResolver` + `Judge` + `PolicyGate`).

- Current pipeline (`pipeline.py: evaluate_model`, `discover_single/provider/all`) is the chosen path: LLM-judge with deterministic benchmarks + policy gate, bounded ThreadPool concurrency, isolated per-model errors.
- `evaluate.py` has zero imports outside its own file (grep confirms no caller).
- Tests exercise `pipeline.evaluate_model` with fakes, not `evaluate.evaluate_models`.
- Keeping two evaluation paths duplicates policy and risks drift.

## Decision
Delete `src/llm_discovery/evaluate.py`. The LLM-judge pipeline is the sole evaluation path.

- If an offline no-LLM fallback is later needed (e.g., AA catalog only, CI without judge key), recreate it as a dedicated CLI flag or `evaluate_aa_fallback.py` that shares `PolicyGate` thresholds, rather than resurrecting the detached AA-only module.
- `pipeline.ProviderBatchWriter` / `BenchmarkDataCache` already rebuild benchmarks locally with no network, so an AA-only mode would be trivial to add behind `config.judge_llm.enabled = false` if required.

## Consequences
- No behavior change: `pipeline` results unchanged (verified by existing tests).
- One less dead path; no dual policy interpretation.
- `git log --all -- src/llm_discovery/evaluate.py` retains history if fallback needed.

## Deviation: bounded concurrency stays ThreadPool
Original spec asked "bounded async concurrency" to replace 2s sleep throttle.
Implemented as bounded `ThreadPoolExecutor(max_workers=4)` with sync `httpx` +
`JudgeTransport` retry/backoff. Keeps existing sync evaluator seam and deterministic
sort; avoids larger `asyncio` + `httpx.AsyncClient` migration (would require async
Judge, async tests, and transport rewrite). Sleep-per-model already removed;
ThreadPool provides bounded concurrency without seq throttle. Decision confirmed 2026-09-02: keep ThreadPool, defer async migration.

## References
- Issue #6 T5 Deodorise: "delete or retire the dead `evaluate.py` (AA-only variant) if the LLM-judge pipeline is the chosen path"
- Blocked by #4 T3 (async + determinism) — now closed, so retirement is safe.
