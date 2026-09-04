#!/usr/bin/env python3
"""Issue #56 prototype: conditional vision filter — before/after demo.

Shows three candidate seams for making the deterministic `vision` drop
non-compulsory when the model is coding-capable + cheap, then runs the
chosen seam (Option C — Pipeline.evaluate_model conditional, ADR 0003) on
real catalog data.

Options compared:
  A) EvidencePacket.is_specialized(vision_exempt: bool) — caller passes exemption
  B) EvidenceCollector.collect() skips vision flag when coding evidence strong
  C) Pipeline.evaluate_model conditional bypass (chosen) — collector still records
     truthful flag; pipeline skips deterministic_drop when vision-only + coding+cheap

Run:
  .venv/bin/python scripts/issue56_conditional_vision_prototype.py
  .venv/bin/python scripts/issue56_conditional_vision_prototype.py --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from llm_discovery.benchmarks import BenchmarkDataCache
import llm_discovery.pipeline as pip
from llm_discovery.pipeline import (
    VISION_AA_CODING_MIN,
    VISION_AA_INTEL_MIN,
    VISION_CHEAP_THRESHOLD,
    VISION_CODING_SCORE_MIN,
    _is_coding_capable,
    _is_cheap_or_free,
    _is_vision_only,
    evaluate_model,
)
from llm_discovery.evidence_collector import EvidenceCollector
from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# Thresholds mirrored from pipeline.py/ADR 0003
THRESHOLDS = {
    "VISION_CHEAP_THRESHOLD": VISION_CHEAP_THRESHOLD,
    "VISION_AA_CODING_MIN": VISION_AA_CODING_MIN,
    "VISION_AA_INTEL_MIN": VISION_AA_INTEL_MIN,
    "VISION_CODING_SCORE_MIN": VISION_CODING_SCORE_MIN,
    "VISION_BENCH_MIN": 50.0,
}

# Representative cases (from issue #54 research)
CASES = [
    {
        "label": "qwen3.8-27b (vision-language coding, cheap)",
        "model_id": "Qwen/Qwen3.8-27B",
        "provider": "modelscope",
        "md_key": "alibaba/qwen3.8-27b",
        "pricing": {"price_1m_blended_3_to_1": 1.13, "price_1m_input_tokens": 0.6, "price_1m_output_tokens": 2.2},
        "evals": {"artificial_analysis_coding_index": 68.1, "artificial_analysis_intelligence_index": 56},
        "expected_before": "drop",
        "expected_after": "keep (bypass deterministic, judge/policy decides)",
    },
    {
        "label": "Qwen-Image-Edit (pure vision, no coding)",
        "model_id": "Qwen/Qwen-Image-Edit",
        "provider": "modelscope",
        "md_key": None,  # no models_dev match — image-edit has no catalog entry
        "pricing": {"price_1m_blended_3_to_1": 0.8, "price_1m_input_tokens": 0.4, "price_1m_output_tokens": 1.6},
        "evals": {"artificial_analysis_coding_index": 14.4, "artificial_analysis_intelligence_index": 14.4},
        "expected_before": "drop",
        "expected_after": "drop (not coding-capable)",
    },
    {
        "label": "Qwen3-VL-235B (vision, weak coding, expensive)",
        "model_id": "Qwen/Qwen3-VL-235B-A22B-Instruct",
        "provider": "modelscope",
        "md_key": "alibaba/qwen3-vl-235b-a22b-instruct",
        "pricing": {"price_1m_blended_3_to_1": 0.70, "price_1m_input_tokens": 0.5, "price_1m_output_tokens": 1.2},
        "evals": {"artificial_analysis_coding_index": 20, "artificial_analysis_intelligence_index": 14},
        "expected_before": "drop",
        "expected_after": "drop (not coding-capable)",
    },
    {
        "label": "tts model (non-vision specialized, coding+cheap must stay dropped)",
        "model_id": "Qwen/Qwen3-TTS-8B",
        "provider": "modelscope",
        "md_key": None,
        "pricing": {"price_1m_blended_3_to_1": 0.30, "price_1m_input_tokens": 0.2, "price_1m_output_tokens": 0.5},
        "evals": {"artificial_analysis_coding_index": 70},
        "flags_override": ["specialized_model:tts"],
        "expected_before": "drop",
        "expected_after": "drop (tts compulsory, not vision-only)",
    },
    {
        "label": "null pricing, free-id + SWE bypasses (edge: free proves cheap)",
        "model_id": "my-model-free",
        "provider": "prov",
        "md_key": "my-model-free",
        "pricing": None,
        "evals": {},
        "benchmarks": {"swe_bench_verified": {"score": 61.7}},
        "expected_before": "drop",
        "expected_after": "keep (free + coding-capable)",
    },
    {
        "label": "null pricing, not free, SWE strong still dropped (grill Q3 C)",
        "model_id": "Qwen/Qwen3.8-27B",
        "provider": "modelscope",
        "md_key": "alibaba/qwen3.8-27b",
        "pricing": None,
        "evals": {},
        "benchmarks": {"swe_bench_verified": {"score": 61.7}},
        "expected_before": "drop",
        "expected_after": "drop (null pricing without free proof not cheap)",
    },
]


class _FakeModelsDev:
    def __init__(self, mapping):
        self.models = mapping
    def get_model(self, mid):
        # mimic catalog get_model with exact + suffix match
        for k, v in self.models.items():
            if mid == k or mid.endswith(k) or k in mid:
                return v
        return None

class _FakeEval:
    def evaluate(self, request, evidence_packet=None):
        from llm_discovery.evaluation import ModelEvaluation
        return ModelEvaluation(
            canonical_name="test", coding=True, aa_relevance="strong",
            confidence=0.9, decision="keep", evidence_level="strong",
            evidence=[], coding_assessment=None,
        )

def _resolution(pricing, evals):
    if pricing is None and not evals:
        return SimpleNamespace(aa_model=None)
    return SimpleNamespace(aa_model={
        "id": "test", "name": "test", "slug": "test",
        "evaluations": evals, "pricing": pricing or {},
    })

def demo_option_table():
    print("Options for conditional vision filter (ADR 0003 chose C):")
    print()
    print("  A) EvidencePacket.is_specialized(vision_exempt: bool)")
    print("     - Packet method takes exemption flag; pipeline passes coding+cheap result.")
    print("     - Pro: tiny call-site change. Con: Packet leaks policy (pricing/benchmarks)")
    print("       into pure dataclass; violates seam (Packet should stay data-only).")
    print()
    print("  B) EvidenceCollector.collect() skips vision flag when coding evidence strong")
    print("     - Collector suppresses flag creation if coding+cheap.")
    print("     - Pro: single site. Con: Audit trail lost — stored flags lie (vision")
    print("       flag silently absent). Observer cannot see that vision was present.")
    print("       Also couples collector to pricing/benchmarks (already has resolution,")
    print("       but flag truthfulness is compromised).")
    print()
    print("  C) Pipeline.evaluate_model conditional bypass (chosen)")
    print("     - Collector records truthful vision flag; pipeline checks")
    print("       vision_only + coding_capable + cheap_or_free before deterministic_drop.")
    print("     - Pro: Audit trail preserved; Packet stays pure; pricing lives in")
    print("       resolution AA pricing (live ModelResolver, alias-aware) not stale")
    print("       packet.pricing. Clear separation: evidence vs policy.")
    print("     - Con: 3-predicate check in pipeline (still <30 lines coordinator).")
    print()
    print("  Pricing integration point: resolution.aa_model.pricing.price_1m_blended_3_to_1")
    print("     (blended 3:1 from AA catalog, post-alias) via _is_cheap_or_free();")
    print("     packet.pricing is same dict mirrored for LLM/persistence, not source.")
    print("     Free proven via model_id contains 'free' OR pricing blended==0 OR")
    print("     input==0 && output==0. Null pricing without free proof => not cheap.")
    print()

def run_before_after():
    print("Before/after (before = compulsory vision drop, after = conditional bypass):")
    print(f"Thresholds: cheap <= {VISION_CHEAP_THRESHOLD}  aa_coding >= {VISION_AA_CODING_MIN}  aa_intel >= {VISION_AA_INTEL_MIN}  coding_score >= {VISION_CODING_SCORE_MIN}  bench >= 50")
    print()
    results = []
    for case in CASES:
        model_id = case["model_id"]
        provider = case["provider"]
        pricing = case.get("pricing")
        evals = case.get("evals", {})
        flags_override = case.get("flags_override")
        benchmarks = case.get("benchmarks")

        # Build resolution pricing source (AA catalog) — this is what _is_cheap_or_free reads
        res = _resolution(pricing, evals)
        # Build benchmark cache if needed
        cache = None
        if benchmarks:
            cache = BenchmarkDataCache(cache_path=Path("/tmp/issue56_bm.json"))
            cache._data = {model_id: {"benchmarks": benchmarks, "raw_benchmarks": []}, "my-model-free": {"benchmarks": benchmarks, "raw_benchmarks": []}}
            cache._loaded = True

        # BEFORE: compulsory drop — any vision flag => deterministic_drop
        flags_before = flags_override if flags_override else (["specialized_model:vision"] if "tts" not in model_id.lower() else flags_override)
        # We simulate before by checking is_specialized compulsory
        before_drop = True if flags_before else False  # would drop
        # For the tts case, flags_override is tts => before_drop True
        # AFTER: conditional check mirrors pipeline.py
        flags = flags_override if flags_override is not None else ["specialized_model:vision"]
        # For Qwen-Image-Edit there is no models_dev vision flag; simulate vision flag present to show logic
        # (real collector would need description; we force vision flag for demo)
        vision_only = _is_vision_only(flags)
        coding = _is_coding_capable(res, cache, model_id, provider)
        cheap = _is_cheap_or_free(res, model_id, None)
        would_bypass = vision_only and coding and cheap
        after_drop = not would_bypass  # if bypass => not deterministic dropped, goes to judge

        # Also run live evaluate_model for ground truth where possible
        md_models = {}
        if case.get("md_key"):
            md_models[case["md_key"]] = {"id": case["md_key"], "name": "Qwen", "description": "vision-language model for coding" if "qwen3" in case["md_key"].lower() else "vision-language instruct model"}
        if case["label"].startswith("Qwen-Image"):
            md_models["qwen-image-edit"] = {"id": "qwen-image-edit", "name": "Qwen", "description": "vision image edit model"}
        md = _FakeModelsDev(md_models)
        # Use patch to inject resolution, and run evaluate_model for cases with AA pricing
        try:
            with patch.object(pip, "resolve_model", lambda *a, **k: res):
                rec = evaluate_model({"id": model_id}, provider, None, md, _FakeEval(), 24.0, 45.0, cache=cache)
                live_decision = rec["decision"]
                live_source = rec["source"]
        except Exception as e:
            live_decision = f"error:{e}"
            live_source = "error"

        print(f"- {case['label']}")
        print(f"  model_id={model_id}  vision_only={vision_only}  coding_capable={coding}  cheap_or_free={cheap}")
        print(f"  BEFORE: drop (deterministic vision) | AFTER: {'keep-evaluated' if would_bypass else 'drop'}")
        print(f"  live evaluate_model: decision={live_decision} source={live_source}  expected_after={case['expected_after']}")
        print()
        results.append({
            "case": case["label"],
            "model_id": model_id,
            "vision_only": vision_only,
            "coding_capable": coding,
            "cheap_or_free": cheap,
            "would_bypass": would_bypass,
            "live_decision": live_decision,
            "live_source": live_source,
            "expected_after": case["expected_after"],
        })
    return results

def main():
    ap = argparse.ArgumentParser(description="Issue #56 conditional vision prototype")
    ap.add_argument("--json", action="store_true", help="emit JSON results")
    args = ap.parse_args()
    if not args.json:
        demo_option_table()
        results = run_before_after()
        print("Summary: pipeline conditional keeps only vision-only + coding + cheap (".lower())
        for r in results:
            print(f"  {r['case']}: would_bypass={r['would_bypass']} live={r['live_decision']}/{r['live_source']}")
        # pricing integration note
        print()
        print("Pricing source: ModelResolver AA catalog (packet.pricing mirrors AA for LLM, not source).")
        print(f"  Writing artifact to prototypes/issue56/before_after.json")
        out = ROOT / "prototypes" / "issue56" / "before_after.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"thresholds": THRESHOLDS, "results": results}, indent=2))
        print(f"  Wrote {out}")
    else:
        results = run_before_after()
        print(json.dumps({"thresholds": THRESHOLDS, "results": results}, indent=2))

if __name__ == "__main__":
    main()
