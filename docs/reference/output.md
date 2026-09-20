# Output

Reference for what a discovery run produces and where it lands.

## Location

```
data/results/<provider>.yaml    # e.g. data/results/kilo_ai.yaml, data/results/groq.yaml
```

- One file per provider, overwritten idempotently on each run.
- `data/` is gitignored (`.gitignore:21`); snapshots and results stay local. Commit only `.env.example` and `config/providers.yaml`.

## Schema (per file)

```yaml
provider: kilo_ai
evaluated_at: '2026-09-02T12:54:29.590323+00:00'
keep:
  - model_id: minimax/minimax-m2.7:free
    decision: keep
    tier: flash            # max | flash
    category: flash        # mirrors tier (spec wording)
    aa_model_id: 4bbceacb-cf47-464b-b60f-e1d1fe016d67
    aa_score: 38.9
    coding_score: 58.23
    benchmarks: { scores: { swe_bench_verified: { score: 79.9, ... } }, ... }
    confidence: 0.95
    evidence_level: strong
    evidence: ["AA Intelligence Index 38.9 exceeds minimum threshold of 24.0", ...]
    coding_assessment: null
drop_llm:
  - model_id: some-model
    decision: drop
    tier: drop
    # same keys as keep
error:
  - model_id: some-model
    decision: error
    tier: error
    evidence: ["LLM evaluation failed: ..."]
```

- `keep` - coding-relevant models (downstream gateway consumes this).
- `drop_llm` - LLM-judged non-coding (free-model-rule drops are excluded from YAML entirely).
- `error` - judge/transport failures (not drops).

Tracer mode (`discover <provider>` without `--all`) writes a single-record YAML with the same keys at the top level (`provider, model_id, decision, tier, ...`) via `SingleModelWriter`.

Programmatic writers: `src/llm_discovery/results.py:ProviderBatchWriter` / `SingleModelWriter` and shims `save_provider_result()` / `save_yaml_result()`.

## Downstream handoff

`data/results/*.yaml` is the keep-list. To feed a gateway, read `keep[].model_id` per provider and map each to the gateway config (use `base_url` and `secret` from `config/providers.yaml`). Diffing `data/results/*.yaml` across runs audits what entered/left the keep-list before promotion.
