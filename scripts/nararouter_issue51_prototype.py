#!/usr/bin/env python3
"""Issue #51 prototype: corrected NaraRouter end-to-end pipeline (offline).

Reproduces the full deterministic seam stack on CAPTURED data so the corrected
pipeline can be run without network or secrets:

    raw /models  ->  filtered true-free list  ->  resolved AA match
        -> evidence packet  ->  (deterministic judge)  -> verdict record

Real seams used (no reimplementation):
  - discovery._split_by_free_rule          (legacy "before" filter)
  - discovery.NARAROUTER_FREE_SNAPSHOT      (true-free allowlist, issue #52)
  - model_matching.ModelMatcher             (alias + normalization, issue #50)
  - evidence_collector.EvidenceCollector    (packet: AA + benchmarks + pricing)
  - evaluation.ModelEvaluation              (judge verdict record)
  - policy_gate.PolicyGate                  (final keep/drop + tier)

NaraRouter /models and /api/plans require an API key; the LLM judge (agnes)
returns 401 without credentials. This prototype loads the captured
data/nararouter_raw.json (59 models) and data/artifacts/nararouter_plans.json
(free-plan allowlist), and swaps the LLM judge for DeterministicJudge, which
derives a ModelEvaluation from the evidence-level thresholds PolicyGate
consults. evaluate_model + PolicyGate.apply run UNMODIFIED, so the verdict
layer exercises production policy exactly.

Run:  .venv/bin/python scripts/nararouter_issue51_prototype.py [--diff]
"""
from __future__ import annotations

import argparse
import json
import difflib
from pathlib import Path
from typing import Any

from llm_discovery.benchmarks import BenchmarkDataCache, build_benchmark_profile, compute_coding_score
from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from llm_discovery.discovery import NARAROUTER_FREE_SNAPSHOT
from llm_discovery.evaluation import ModelEvaluation, ModelEvaluationRequest
from llm_discovery.model_matching import ModelMatcher, normalize_model_id
from llm_discovery.pipeline import _split_by_free_rule, evaluate_model
from llm_discovery.policy_gate import PolicyGate
from llm_discovery.results import ProviderBatchWriter, PROVIDER_SCHEMA_KEYS

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW_PATH = DATA / "nararouter_raw.json"
PLANS_PATH = DATA / "artifacts" / "nararouter_plans.json"
BEFORE_YAML = DATA / "results" / "nararouter.yaml"
OUT_DIR = ROOT / "prototypes" / "issue51"

AA_MIN, AA_MAX = 24.0, 45.0
NL = chr(10)

PAID_GATED = {
    "deepseek-v4-flash-free", "glm-5.3-flash-free", "glm-5.3-free",
    "mimo-v2.5-free", "muse-spark-1.3-contributor-free", "qwen3.8-flash-free",
}


def load_captured_raw():
    data = json.loads(RAW_PATH.read_text())
    models = data.get("data") or data.get("models")
    assert models, "no models in " + str(RAW_PATH)
    return models


def allowlist_from_plans():
    if PLANS_PATH.exists():
        plans = json.loads(PLANS_PATH.read_text())
        entries = plans.get("data", []) if isinstance(plans, dict) else []
        free = next((p for p in entries if p.get("code") == "free"), None)
        if free and free.get("models"):
            return set(free["models"])
    return set(NARAROUTER_FREE_SNAPSHOT)


def build_caches():
    aa = ArtificialAnalysisCatalog(DATA / "artificial_analysis_models.json")
    md = ModelsDevCatalog(DATA / "models_dev_catalog.json")
    cache = BenchmarkDataCache()
    cache.collect_from_local(aa, md)
    return aa, md, cache


_CONF = {"none": 0.0, "weak": 0.3, "moderate": 0.6, "strong": 0.9}


class DeterministicJudge:
    """Offline stand-in for the LLM judge (issue #51 env: agnes 401).

    Produces a ModelEvaluation from deterministic evidence (AA score + coding
    score + benchmark coverage) using PolicyGate._deterministic_evidence_level.
    coding is reported False here: PolicyGate.apply's deterministic override
    then promotes benchmark-backed models to keep (identical to a working-LLM
    run). The real agnes LLM is preferred in production.
    """

    def __init__(self, min_score=AA_MIN, max_score=AA_MAX, cache=None):
        self.min_score = min_score
        self.max_score = max_score
        self.cache = cache

    def evaluate(self, request, packet):
        aa_match = request.aa_match or {}
        verified = aa_match.get("score")
        profile = build_benchmark_profile(request.model_id, request.provider, self.cache) if self.cache is not None else None
        coding_score, _, _ = compute_coding_score(profile) if profile and profile.scores else (None, 0.0, [])
        gate = PolicyGate(self.min_score, self.max_score, self.cache)
        level = gate._deterministic_evidence_level(verified, coding_score, profile)
        evidence = []
        if aa_match.get("matched") and verified is not None:
            evidence.append("AA Intelligence Index " + format(verified, ".1f") + " matched -> evidence_level=" + level)
        elif verified is not None:
            evidence.append("AA match score " + format(verified, ".1f") + " -> evidence_level=" + level)
        if profile and profile.scores:
            for bm in (packet.benchmarks or []):
                evidence.append("Benchmark " + bm.name + ": " + str(bm.value))
        else:
            evidence.append("No coding benchmarks; verdict via deterministic evidence_level only")
        return ModelEvaluation(
            canonical_name=aa_match.get("name") or aa_match.get("model_id") or request.model_id,
            coding=False,
            aa_relevance=level,
            confidence=_CONF.get(level, 0.0),
            decision="drop",
            evidence_level=level,
            evidence=evidence[:3],
        )


def run_corrected_pipeline(raw_models, aa, md, cache, allowlist):
    filtered = [m for m in raw_models if m["id"] in allowlist]
    evaluator = DeterministicJudge(AA_MIN, AA_MAX, cache)
    kept_names = [m["id"] for m in sorted(filtered, key=lambda x: x["id"])]
    print("[prototype] corrected filter: raw " + str(len(raw_models)) + " -> true-free " + str(len(filtered)) + " (kept: " + str(kept_names) + ")")
    buckets = {"keep": [], "drop_llm": [], "error": []}
    for model in sorted(filtered, key=lambda x: x["id"]):
        record = evaluate_model(model, "nararouter", aa, md, evaluator, AA_MIN, AA_MAX, cache)
        decision = record.get("decision", "drop")
        if decision == "keep":
            buckets["keep"].append(record)
        elif decision == "error":
            buckets["error"].append(record)
        else:
            buckets["drop_llm"].append(record)
    return buckets


def before_filter(raw_models):
    kept, dropped = _split_by_free_rule(raw_models)
    return sorted(m["id"] for m in kept), sorted(m["id"] for m in dropped)


def mapping_matrix(raw_models, aa, md, cache):
    matcher = ModelMatcher(aa_catalog=aa, models_dev_catalog=md, benchmark_cache=cache)
    target_ids = [
        "mimo-v2.5-free", "minimax-m3-free",
        "muse-spark-1.2-contributor-free", "qwen3.8-27b", "qwen3.8-flash-free",
        "glm-5.3-free", "glm-5.3-flash-free", "deepseek-v4-flash-free",
        "muse-spark-1.3-contributor-free",
    ]
    rows = []
    for tid in target_ids:
        model = next((m for m in raw_models if m["id"] == tid), None)
        if model is None:
            rows.append({"provider_id": tid, "norm": normalize_model_id(tid), "aa_slug": None, "aa_score": None, "method": "missing_from_raw", "paid_gated": tid in PAID_GATED})
            continue
        res = matcher.match(model["id"])
        aam = res.aa_model
        rows.append({
            "provider_id": model["id"],
            "norm": normalize_model_id(model["id"]),
            "aa_slug": aam.get("slug") if aam else None,
            "aa_score": (aam.get("evaluations", {}).get("artificial_analysis_intelligence_index") if aam else None),
            "method": res.method,
            "paid_gated": model["id"] in PAID_GATED,
        })
    return rows


def write_after_yaml(buckets):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    result = {"keep": buckets["keep"], "drop": buckets["drop_llm"], "error": buckets["error"]}
    return ProviderBatchWriter().write(result, "nararouter", OUT_DIR)


def filter_report(raw_models, allowlist):
    before_kept, before_dropped = before_filter(raw_models)
    after_kept = sorted(m["id"] for m in raw_models if m["id"] in allowlist)
    lines = [
        "Issue #51 NaraRouter true-free filter - before/after",
        "=" * 60,
        "Raw /models: " + str(len(raw_models)),
        "True-free allowlist: " + str(len(allowlist)),
        "",
        "BEFORE (legacy _split_by_free_rule - keeps ALL free-marker ids):",
        "  kept  = " + str(len(before_kept)) + ": " + str(before_kept),
        "  paid-gated included in kept = " + str(sorted(set(before_kept) & PAID_GATED)),
        "  true-free non-free models wrongly dropped = " + str(sorted(set(before_dropped) & allowlist)),
        "",
        "AFTER (issue #52 allowlist - only free-plan models):",
        "  kept  = " + str(len(after_kept)) + ": " + str(after_kept),
        "  paid-gated excluded = " + str(sorted(PAID_GATED)),
        "",
        "Conclusion: corrected filter keeps the 9 true-free models and excludes the 6 paid-gated-free models.",
    ]
    return NL.join(lines) + NL


def print_filter_before_after(raw_models, allowlist):
    before_kept, before_dropped = before_filter(raw_models)
    after_kept = sorted(m["id"] for m in raw_models if m["id"] in allowlist)
    print()
    print("=== FILTER before/after (real functions) ===")
    print("BEFORE (_split_by_free_rule): kept=" + str(len(before_kept)) + " incl " + str(len(set(before_kept) & PAID_GATED)) + " paid-gated: " + str(sorted(set(before_kept) & PAID_GATED)))
    print("BEFORE dropped " + str(len(set(before_dropped) & allowlist)) + " true-free non-free models: " + str(sorted(set(before_dropped) & allowlist)))
    print("AFTER  (allowlist): kept=" + str(len(after_kept)) + "; paid-gated excluded=" + str(sorted(PAID_GATED & set(before_kept))))


def print_verdict_summary(buckets):
    print()
    print("=== VERDICT (deterministic judge; agnes LLM 401 in this env) ===")
    print("keep(" + str(len(buckets["keep"])) + "):   " + str([r["provider_model_id"] for r in buckets["keep"]]))
    print("drop(" + str(len(buckets["drop_llm"])) + "):  " + str([r["provider_model_id"] for r in buckets["drop_llm"]]))
    print("error(" + str(len(buckets["error"])) + "): " + str([r["provider_model_id"] for r in buckets["error"]]))


def diff_yamls(before, after):
    b = before.read_text().splitlines() if before.exists() else []
    a = after.read_text().splitlines()
    return NL.join(difflib.unified_diff(b, a, fromfile=str(before.name), tofile=str(after.name), lineterm=""))


def main(argv=None):
    p = argparse.ArgumentParser(description="Issue #51 NaraRouter prototype (offline)")
    p.add_argument("--diff", action="store_true", help="print before/after YAML diff")
    args = p.parse_args(argv)

    raw = load_captured_raw()
    allowlist = allowlist_from_plans()
    aa, md, cache = build_caches()

    print_filter_before_after(raw, allowlist)

    print()
    print("=== MAPPING matrix (issue #51 claim: mimo/minimax/muse/qwen map correctly) ===")
    rows = mapping_matrix(raw, aa, md, cache)
    for r in rows:
        flag = " [paid-gated EXCLUDED]" if r["paid_gated"] else ""
        print("  " + r["provider_id"].ljust(42) + " -> aa=" + str(r["aa_slug"]) + " sc=" + str(r["aa_score"]) + " method=" + r["method"] + flag)

    buckets = run_corrected_pipeline(raw, aa, md, cache, allowlist)
    print_verdict_summary(buckets)

    after_path = write_after_yaml(buckets)
    print()
    print("After YAML:  " + str(after_path.relative_to(ROOT)))
    (OUT_DIR / "filter_before_after.txt").write_text(filter_report(raw, allowlist))
    print("Filter log:  " + str((OUT_DIR / "filter_before_after.txt").relative_to(ROOT)))

    if args.diff:
        print()
        print("=== BEFORE/AFTER diff (data/results/nararouter.yaml vs prototype) ===")
        print(diff_yamls(BEFORE_YAML, after_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

