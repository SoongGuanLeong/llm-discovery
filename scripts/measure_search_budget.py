#!/usr/bin/env python3
"""Live A/B measurement: SEARCH_MAX_RESULTS 3 vs 5 on a 20-model weak sample.

Issue #216 (parent #212). No source-code changes: each arm simply sets the
SEARCH_MAX_RESULTS env, which the #215 plumbing (make_searcher + llm.py
truncation) already respects. The judge LLM comes from config/providers.yaml
(judge_llm section); search backends are DuckDuckGo + SearXNG fallback unless
BRAVE_API_KEY is set.

Usage:
    .venv/bin/python scripts/measure_search_budget.py
    .venv/bin/python scripts/measure_search_budget.py --workers 4         --output .scratch/research/216-ab-measurement.json

Outputs a markdown 8-metric table to stdout and JSON (metrics + decision +
per-model rows) to --output.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

from llm_discovery import search_budget as sb  # noqa: E402
from llm_discovery.benchmarks import BenchmarkDataCache  # noqa: E402
from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog  # noqa: E402
from llm_discovery.config import load_config  # noqa: E402
from llm_discovery.evaluator import EvaluatorCoordinator  # noqa: E402
from llm_discovery.llm import LocalLLMEvaluator  # noqa: E402
from llm_discovery.search import make_searcher  # noqa: E402

DATA_DIR = ROOT / "data"


def load_weak_records() -> list[dict[str, Any]]:
    """Weak/none records from the last full build's per-provider results YAML."""
    recs: list[dict[str, Any]] = []
    for path in sorted((DATA_DIR / "results").glob("*.yaml")):
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
        provider = str(data.get("provider", "unknown"))
        for section in ("drop_llm", "error"):
            for rec in data.get(section) or []:
                level = str(rec.get("evidence_level", "none")).lower()
                if level in ("weak", "none"):
                    recs.append({
                        "provider": provider,
                        "model_id": str(rec.get("model_id", "")).strip(),
                        "evidence_level": level,
                    })
    return recs


def run_arm(
    max_results: int,
    sample: list[dict[str, Any]],
    cfg: Any,
    aa: Any,
    models_dev: Any,
    cache: Any,
    workers: int,
) -> tuple[list[dict[str, Any]], float]:
    """Run one arm: set SEARCH_MAX_RESULTS env, evaluate every sample model."""
    os.environ["SEARCH_MAX_RESULTS"] = str(max_results)
    api_key = os.environ.get(cfg.judge_llm.secret) if cfg.judge_llm.secret else None
    searcher = make_searcher(os.environ.get("BRAVE_API_KEY"))
    evaluator = LocalLLMEvaluator(
        base_url=cfg.judge_llm.base_url,
        model=cfg.judge_llm.model,
        api_key=api_key,
        min_score=cfg.artificial_analysis.min_score,
        search_web=searcher.search,
        timeout=getattr(cfg.judge_llm, "timeout", 120) or 120,
    )

    rows: list[dict[str, Any]] = []
    started_at = time.monotonic()
    futures: dict[Any, tuple[dict[str, Any], float]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for rec in sample:
            coordinator = EvaluatorCoordinator(
                provider_name=rec["provider"],
                aa=aa,
                models_dev=models_dev,
                evaluator=evaluator,
                min_score=cfg.artificial_analysis.min_score,
                max_score=cfg.artificial_analysis.max_score,
                cache=cache,
                store=None,  # measurement only: never write the store
            )
            model = {"id": rec["model_id"], "provider": rec["provider"]}
            futures[pool.submit(coordinator.evaluate, model)] = (rec, time.monotonic())
        for fut in as_completed(futures):
            rec, t0 = futures[fut]
            latency = time.monotonic() - t0
            row: dict[str, Any] = {
                "model": f"{rec['provider']}/{rec['model_id']}",
                "baseline_level": rec["evidence_level"],
                "latency_s": round(latency, 2),
                "error": False,
                "promoted": False,
                "guard_demotion": False,
                "hallucinated_urls": 0,
                "urls": [],
                "new_level": None,
                "decision": None,
                "evidence": [],
            }
            try:
                result = fut.result()
            except Exception as exc:  # noqa: BLE001 — mirror pipeline thread boundary
                row["error"] = True
                row["new_level"] = "none"
                row["decision"] = "error"
                row["evidence"] = [f"LLM evaluation failed: {exc}"]
                rows.append(row)
                continue
            row["new_level"] = result.get("evidence_level")
            row["decision"] = result.get("decision")
            if str(row["decision"]).strip().lower() == "error":
                row["error"] = True  # gate-level LLM failure, not just thread exception
            row["evidence"] = result.get("evidence", []) or []
            if row["error"]:
                # Failure messages can embed the judge endpoint URL — not judge-cited
                # evidence, so error rows contribute no URLs / no hallucinations.
                row["urls"] = []
                row["hallucinated_urls"] = 0
            else:
                row["urls"] = sb.extract_urls(row["evidence"])
                row["hallucinated_urls"] = sb.count_hallucinated_urls(row["evidence"])
            row["guard_demotion"] = sb.has_guard_demotion(row["evidence"])
            row["promoted"] = sb.was_promoted(row["baseline_level"], row["new_level"])
            rows.append(row)
    rows.sort(key=lambda r: r["model"])
    return rows, time.monotonic() - started_at


def markdown_table(a: sb.ArmMetrics, b: sb.ArmMetrics, decision: dict[str, Any]) -> str:
    lines = [
        "| Metric | A: max_results=3 | B: max_results=5 |",
        "|--------|------------------|------------------|",
        f"| promotion count | {a.promotions}/{a.sample_n} | {b.promotions}/{b.sample_n} |",
        f"| promotion rate | {a.promotion_rate:.1%} | {b.promotion_rate:.1%} |",
        f"| hallucination count | {a.hallucinations} | {b.hallucinations} |",
        f"| latency p50 | {a.latency_p50_s:.1f}s | {b.latency_p50_s:.1f}s |",
        f"| latency p95 | {a.latency_p95_s:.1f}s | {b.latency_p95_s:.1f}s |",
        f"| error count | {a.errors} | {b.errors} |",
        f"| evidence URLs (total/unique) | {a.evidence_urls_total}/{a.evidence_urls_unique} | {b.evidence_urls_total}/{b.evidence_urls_unique} |",
        f"| guard demotions | {a.guard_demotions} | {b.guard_demotions} |",
        f"| wall time | {a.wall_time_s:.1f}s | {b.wall_time_s:.1f}s |",
        f"| cost | {a.cost_usd:.2f} USD ({a.cost_note}) | {b.cost_usd:.2f} USD ({b.cost_note}) |",
        "",
        f"Decision: **{'ADOPT 5' if decision['adopt_search_max_results_5'] else 'KEEP 3'}** — "
        + ", ".join(f"failed: {k}" for k in decision["failed_checks"])
        + ("" if decision["failed_checks"] else " (all checks pass)"),
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--output", default=".scratch/research/216-ab-measurement.json")
    ap.add_argument("--arms", default="3,5", help="comma-separated max_results arms")
    args = ap.parse_args()

    cfg = load_config()
    aa = ArtificialAnalysisCatalog(DATA_DIR / "artificial_analysis_models.json")
    models_dev = ModelsDevCatalog(DATA_DIR / "models_dev_catalog.json")
    cache = BenchmarkDataCache()
    cache.collect_from_local(aa, models_dev)

    records = load_weak_records()
    sample = sb.select_weak_sample(records, n=20, seed=42)
    print(f"Sample: {len(sample)} of {len(records)} weak/none pool records "
          f"(seed=42, routers/specialized excluded)")
    for rec in sample:
        print(f"  {rec['provider']}/{rec['model_id']} (baseline {rec['evidence_level']})")

    arms = [int(x) for x in args.arms.split(",")]
    arm_results: dict[int, tuple[list[dict], float]] = {}
    for arm in arms:
        print(f"\n=== Arm max_results={arm} ===")
        rows, wall = run_arm(arm, sample, cfg, aa, models_dev, cache, args.workers)
        arm_results[arm] = (rows, wall)
        for row in rows:
            flag = "PROMO" if row["promoted"] else ("ERR" if row["error"] else ".")
            print(f"  [{flag}] {row['model']}: {row['baseline_level']} -> {row['new_level']} "
                  f"({row['latency_s']}s, urls={len(row['urls'])}, halluc={row['hallucinated_urls']})")

    first, second = arms[0], arms[-1]
    cost_note = "Brave free tier, ~$0" if os.environ.get("BRAVE_API_KEY") else "DDG/SearXNG: $0 (no key)"
    a = sb.aggregate_arm(f"max_results_{first}", arm_results[first][0],
                         arm_results[first][1], cost_note=cost_note)
    b = sb.aggregate_arm(f"max_results_{second}", arm_results[second][0],
                         arm_results[second][1], cost_note=cost_note)
    decision = sb.evaluate_decision(a, b)

    out = {
        "issue": 216,
        "parent": 212,
        "date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "judge": {"base_url": cfg.judge_llm.base_url, "model": cfg.judge_llm.model},
        "sample_n": len(sample),
        "sample": [f"{r['provider']}/{r['model_id']}" for r in sample],
    }
    arms_json = {}
    for arm in arms:
        rows, wall = arm_results[arm]
        m = sb.aggregate_arm(f"max_results_{arm}", rows, wall, cost_note=cost_note)
        arms_json[str(arm)] = {"metrics": vars(m), "rows": rows}
    out["arms"] = arms_json
    out["decision"] = decision
    out["decision_rule"] = (
        "adopt 5 only if promotion_rate(B) >= 10% and hallucinations(B) <= promotions(B) "
        "and p95(B) < p95(A) * 1.30"
    )

    print("\n" + markdown_table(a, b, decision))
    out_path = ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
