# Before / After YAML snippet (one drop_llm record)

## Before (bug — UUID model_id)
```yaml
- model_id: 01564c52-8717-47dc-8efd-907a2ca18301
  decision: drop
  tier: drop
  aa_model_id: null
  aa_score: null
  coding_score: null
  pricing: null
  benchmarks: {}
  confidence: 0.95
  evidence_level: weak
  evidence:
  - Deepgram Aura is a text-to-speech model, not a coding model
  - No coding benchmarks or AA score available
  - Model assessed as non-coding (LLM + deterministic); forced drop
```
store_key: `01564c52-8717-47dc-8efd-907a2ca18301` → UUID, fails Accurate-Enough Gate (UUID denylist), no AA/benchmark join

## After (fix — human name canonical, UUID as auxiliary)
```yaml
- model_id: "@cf/deepgram/aura-1"
  decision: drop
  tier: drop
  aa_model_id: null
  aa_score: null
  coding_score: null
  pricing: null
  benchmarks: {}
  confidence: 0.95
  evidence_level: strong  # deterministic drop for TTS still strong, but now auditable
  evidence:
  - Deepgram Aura is a text-to-speech model, not a coding model
  source_id: 01564c52-8717-47dc-8efd-907a2ca18301
  cloudflare_id: 01564c52-8717-47dc-8efd-907a2ca18301
```
store_key: `aura-1` (via normalize_store_key) → not UUID, passes identity floor, can be looked up.

## Coding model example (now joinable)
```yaml
# Before
- model_id: f47ac10b-58cc-4372-a567-0e02b2c3d479
  coding_score: null
  benchmarks: {}
  evidence_level: weak
  evidence: ["No AA match, no benchmarks"]

# After
- model_id: "@cf/qwen/qwen2.5-coder-32b-instruct"
  coding_score: 72.1  # via BenchmarkDataCache join on qwen2.5-coder-32b-instruct
  benchmarks:
    scores:
      swe_bench_verified: {score: 68.5}
    benchmark_coverage: 0.25
  evidence_level: strong
  source_id: f47ac10b-58cc-4372-a567-0e02b2c3d479
```
store_key `qwen2.5-coder-32b-instruct` matches benchmarks.json and models_dev catalog.

## Pipeline effect
`pipeline.evaluate_model` now receives `model["id"] = "@cf/..."` →
- `resolve_model("@cf/qwen/...", aa, models_dev, cache)` can alias-match AA via `normalize_store_key` / `model_matching`
- `EvidenceCollector.collect` finds `models_dev` entry via stripped id
- `BenchmarkDataCache.get` hits on `qwen2.5-coder-32b-instruct` not UUID miss
