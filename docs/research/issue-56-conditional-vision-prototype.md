# Prototype: Conditional vision filter — EvidenceCollector vs Pipeline (issue #56)

Part of #53 Wayfinder — Vision-capable coding model keep/drop policy.
Blocked by #55 (threshold decision, now closed). Implements ADR 0003.

## Question

How should code change to make the deterministic `vision` drop non-compulsory
when the model is coding-capable + cheap, while keeping other specialized
(`tts`/`embedding`/`safety`/`audio`/`voice`/`rerank`) compulsory?

Three candidate seams:

- **A) `EvidencePacket.is_specialized(vision_exempt: bool)`** — packet method takes exemption flag
- **B) `EvidenceCollector.collect()` skips vision flag when coding evidence strong**
- **C) `Pipeline.evaluate_model` conditional bypass after packet built (chosen)**

Must also define pricing integration point (`packet.pricing` vs `resolution.aa_model.pricing`)
and show before/after for `qwen3.8-27b` (should flip) vs `Qwen-Image-Edit` (should stay dropped).

## Candidate seams

### A) EvidencePacket.is_specialized(vision_exempt?)

```python
# evidence_packet.py
def is_specialized(self, vision_exempt: bool = False) -> bool:
    if vision_exempt and self.deterministic_flags == ["specialized_model:vision"]:
        return False
    return any(f.startswith("specialized_model") for f in self.deterministic_flags)

# pipeline.py
vision_exempt = _is_coding_capable(...) and _is_cheap_or_free(...)
if packet.is_specialized(vision_exempt=vision_exempt):
    return deterministic_drop_record(...)
```

- Pro: tiny call-site change.
- Con: Packet (pure dataclass) leaks policy — needs pricing/benchmarks to decide
  exemption. Violates seam: Packet should stay data-only, not encode thresholds.
  Also makes `is_specialized` impure (caller must compute exemption).

### B) EvidenceCollector.collect() skips vision flag

```python
# evidence_collector.py — suppress flag creation
if pattern == "vision" and _is_coding_capable(...) and _is_cheap_or_free(...):
    continue  # don't append flag
```

- Pro: single site, no pipeline change.
- Con: Audit trail lost — stored `deterministic_flags` lie (vision flag silently
  absent). Observer cannot see that vision was present but exempted. Also couples
  collector to pricing/benchmarks and requires `resolution` + `cache` already in
  collector (it has them, but truthfulness is compromised).

### C) Pipeline.evaluate_model conditional bypass (chosen, ADR 0003)

```python
# pipeline.py — collector still records truthful flag
packet = EvidenceCollector(provider).collect(model, cache, models_dev, resolution)
if packet.is_specialized():
    if _is_vision_only(packet.deterministic_flags) \
       and _is_coding_capable(resolution, cache, model_id, provider) \
       and _is_cheap_or_free(resolution, model_id, models_dev):
        print("vision exception - bypass deterministic drop")
    else:
        return deterministic_drop_record(...)
# fall through to Judge -> PolicyGate
```

- Pro: Audit trail preserved; Packet stays pure; pricing lives in `resolution`
  (live `ModelResolver` AA catalog, alias-aware) not stale `packet.pricing`.
  Clear separation: evidence vs policy. Matches existing `PolicyGate` deterministic
  override thresholds.
- Con: 3-predicate check in pipeline (still <30 lines coordinator).

**Decision: C**. See `docs/adr/0003-vision-capable-coding-exception.md`.

## Pricing integration point

- Source: `resolution.aa_model["pricing"]["price_1m_blended_3_to_1"]` (blended 3:1
  from AA catalog, post-alias via `ModelResolver`). Checked in `_is_cheap_or_free`.
- `packet.pricing` mirrors the same dict for LLM reasoning/persistence, not source.
  Using packet would risk stale YAML `pricing: null` when AA miss pre-alias.
- Free proven if: `model_id` contains `free` substring (`:free`, `-free`, `_free`,
  `/free`) OR `pricing.blended == 0` OR `pricing.input == 0 && pricing.output == 0`.
- Null pricing without free proof is **not cheap** — stays dropped (grill Q3 C).

Thresholds (from ADR 0003, `pipeline.py`):

| constant | value | source |
|---|---|---|
| `VISION_CHEAP_THRESHOLD` | `1.2` | `pricing.price_1m_blended_3_to_1 <= 1.2` |
| `VISION_AA_CODING_MIN` | `45.0` | `aa_model.evaluations.artificial_analysis_coding_index` |
| `VISION_AA_INTEL_MIN` | `55.0` | `aa_model.evaluations.artificial_analysis_intelligence_index` |
| `VISION_CODING_SCORE_MIN` | `35.0` | `benchmarks.compute_coding_score` |
| `VISION_BENCH_MIN` | `50.0` | per-bench `swe_bench_verified`/`pro`/`terminal_bench` |

Coding-capable is OR: any single signal suffices (checked from `resolution` AA evals
or `BenchmarkDataCache` via `build_benchmark_profile`).

## Before/after

"Before" = compulsory `is_specialized()` drop (any specialized flag => deterministic
`drop`). "After" = conditional bypass when `vision-only` + `coding-capable` + `cheap_or_free`.

Run: `.venv/bin/python scripts/issue56_conditional_vision_prototype.py`

Artifact: `prototypes/issue56/before_after.json` (machine-readable).

| case | model_id | vision_only | coding_capable | cheap_or_free | before | after | live evaluate_model |
|---|---|---|---|---|---|---|---|
| qwen3.8-27b (vision-language coding, cheap) | `Qwen/Qwen3.8-27B` | true (vision) | true (aa_coding 68.1) | true (blended 1.13 <=1.2) | drop | **keep-evaluated** (bypass) | `keep` `llm` |
| Qwen-Image-Edit (pure vision, no coding) | `Qwen/Qwen-Image-Edit` | true (simulated vision) | false (aa_coding 14.4) | true (0.8) | drop | **drop** (not coding) | `drop` `deterministic` |
| Qwen3-VL-235B (vision, weak coding) | `Qwen/Qwen3-VL-235B-A22B-Instruct` | true | false (aa_coding 20, aa_intel 14) | true | drop | **drop** | `drop` `deterministic` |
| tts model (non-vision specialized, must stay compulsory) | `Qwen/Qwen3-TTS-8B` | false (tts) | true (aa_coding 70) | true | drop | **drop** (tts not vision-only) | `drop` `deterministic` |
| null pricing, free-id + SWE 61.7 | `my-model-free` | true | true (SWE 61.7) | true (free id) | drop | **keep-evaluated** | `keep` `llm` |
| null pricing, not free, SWE 61.7 | `Qwen/Qwen3.8-27B` | true | true (SWE 61.7) | false (null not free) | drop | **drop** (proven cheap required) | `drop` `deterministic` |

Notes:

- Real `Qwen/Qwen-Image-Edit` has no `models_dev` catalog entry (catalog has
  `alibaba/qwen3-vl-235b-*` but not image-edit), so its `model_id` does not contain
  `vision` and its deterministic flag is empty in the current snapshot — it bypasses
  the deterministic gate entirely and falls to LLM/policy (which drops it as non-coding).
  The prototype forces a vision flag to demonstrate the conditional would NOT bypass
  because `coding_capable` is false.
- Real `nararouter/qwen3.8-27b` and `Qwen/Qwen3.8-27B` share `alibaba/qwen3.8-27b`
  description "Dense 27B vision-language model for coding, agent tasks, and image
  and video understanding" (models_dev vision flag) — before it was dropped despite
  `coding_score 61.7` / `aa_coding 68.1` / pricing $1.13.
- Other specialized (`embedding`, `tts`, `safety`, `audio`, `voice`, `rerank`) remain
  compulsory: `_is_vision_only` requires every flag == `specialized_model:vision`, so
  any non-vision flag or mixed flags stay dropped even if coding+cheap.

## Verification

- `tests/test_vision_exception.py` — 12 tests (thresholds, predicates, live
  `evaluate_model` for coding+cheap bypass, expensive stays dropped, non-coding
  stays dropped, embedding stays dropped, null pricing free vs not free). All pass.
- Prototype script above exercises all 6 cases end-to-end with real `evaluate_model`
  + `PolicyGate` (no mock policy), confirming pipeline conditional.
- ADR 0003 documents scope (vision-only), condition (OR coding + proven cheap),
  seam (pipeline), and consequences (4 unique keep, 1 VL-only + 1 borderline stay
  dropped, no regression for other specialized).

## Out of scope

- NaraRouter true-free allowlist, general tier (flash/max) tuning beyond vision case.
- Live discovery re-run still pending (issue #57).
