"""Fixed-snapshot build-all harness gate (issue #235).

Compares baseline vs improved on same snapshot; asserts uncertain+error drops
are due to acquisition (new AA/bench/verified evidence), not permissiveness
(threshold loosening). Candidate TTL and Derived Cache semantics unchanged;
CI-enforceable (exit non-zero on failure).

Pure functions + thin YAML/telemetry adapters; no network/LLM required.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Frozen constants per ADR 0008 / issue #222 / #224
# ---------------------------------------------------------------------------

FROZEN_MIN_SCORE = 24.0
FROZEN_MAX_SCORE = 45.0

FROZEN_CANDIDATE_TTL_DAYS = 90
FROZEN_CANDIDATE_TTL_MIN_DAYS = 60

FROZEN_DEFAULT_TTL_DAYS = 14
FROZEN_STORE_FILE_VERSION = 2
FROZEN_PRICING_TTL_DAYS = 7
FROZEN_BENCHMARK_TTL_DAYS = 90

# ADR 0008 deterministic thresholds (duplicated as frozen gate)
# strong: coding>=45, AA>=55, AA>=45+bc>=0.25, AA>=50+cs>=0.08, SWE/Terminal>=50
# moderate: AA>=24, coding>=20, any supplement >=30, verified claim (not tested here)
# weak: else


def _make_profile(scores: dict[str, Any] | None, bc: float | None = None, cs: float | None = None):
    """Minimal BenchmarkProfile stub for deterministic gate checks."""
    from llm_discovery.benchmarks import BenchmarkProfile

    p = BenchmarkProfile(model_id="gate-check", provider="gate")
    p.scores = scores or {}
    # raw_benchmarks not needed for coverage checks when scores empty
    p.raw_benchmarks = []
    # Override coverage helpers when explicit bc/cs supplied (for AA+coverage cases)
    orig_bc = p.benchmark_coverage
    orig_cs = p.coverage_with_supplements

    def _bc():
        if bc is not None:
            return bc
        try:
            return orig_bc()
        except Exception:
            return 0.0

    def _cs():
        if cs is not None:
            return cs
        try:
            return orig_cs()
        except Exception:
            return 0.0

    p.benchmark_coverage = _bc  # type: ignore
    p.coverage_with_supplements = _cs  # type: ignore
    return p


# Threshold vectors: (verified_score, coding_score, scores, bc, cs, expected_level, label)
_THRESHOLD_VECTORS: list[tuple[Any, Any, dict | None, Any, Any, str, str]] = [
    # Strong boundaries
    (None, 45, {}, None, None, "strong", "coding>=45 strong"),
    (None, 44.9, {}, None, None, "moderate", "coding 44.9 moderate (>=20)"),
    (55, None, {}, None, None, "strong", "AA>=55 strong"),
    (54.9, None, {}, None, None, "moderate", "AA 54.9 not strong (>=24 moderate)"),
    (45, None, {}, 0.25, None, "strong", "AA45+bc0.25 strong"),
    (45, None, {}, 0.24, None, "moderate", "AA45+bc0.24 only moderate"),
    (50, None, {}, None, 0.08, "strong", "AA50+cs0.08 strong"),
    (50, None, {}, None, 0.07, "moderate", "AA50+cs0.07 only moderate"),
    (None, None, {"swe_bench_verified": {"score": 50}}, None, None, "strong", "SWE 50 strong"),
    (None, None, {"swe_bench_verified": {"score": 49.9}}, None, None, "moderate", "SWE 49.9 moderate (>=30 supplement)"),
    (None, None, {"terminal_bench": {"score": 50}}, None, None, "strong", "Terminal 50 strong"),
    # Moderate boundaries
    (24, None, {}, None, None, "moderate", "AA>=24 moderate"),
    (23.9, None, {}, None, None, "weak", "AA 23.9 weak"),
    (None, 20, {}, None, None, "moderate", "coding>=20 moderate"),
    (None, 19.9, {}, None, None, "weak", "coding 19.9 weak"),
    (None, None, {"aider": {"score": 30}}, None, None, "moderate", "supplement 30 moderate"),
    (None, None, {"aider": {"score": 29.9}}, None, None, "weak", "supplement 29.9 weak"),
    # Weak else
    (None, None, {}, None, None, "weak", "no signal weak"),
    (10, None, {}, None, None, "weak", "AA 10 weak"),
]


def check_thresholds_frozen() -> dict[str, Any]:
    """Verify deterministic thresholds frozen per ADR 0008."""
    failures: list[str] = []
    try:
        from llm_discovery.benchmarks import MAX_SCORE, MIN_SCORE
        from llm_discovery.policy_gate import PolicyGate

        if MIN_SCORE != FROZEN_MIN_SCORE:
            failures.append(f"MIN_SCORE {MIN_SCORE} != frozen {FROZEN_MIN_SCORE}")
        if MAX_SCORE != FROZEN_MAX_SCORE:
            failures.append(f"MAX_SCORE {MAX_SCORE} != frozen {FROZEN_MAX_SCORE}")

        for verified, coding, scores, bc, cs, expected, label in _THRESHOLD_VECTORS:
            profile = _make_profile(scores, bc, cs)
            try:
                actual = PolicyGate._deterministic_evidence_level(verified, coding, profile)
            except Exception as exc:
                failures.append(f"deterministic error for {label}: {exc}")
                continue
            if actual != expected:
                failures.append(f"deterministic {label}: expected {expected}, got {actual} (verified={verified}, coding={coding}, scores={scores})")
    except Exception as exc:
        failures.append(f"check_thresholds_frozen exception: {exc}")
    return {"ok": not failures, "failures": failures}


def check_candidate_ttl() -> dict[str, Any]:
    """Verify Candidate TTL unchanged (60-90d, default 14d)."""
    failures: list[str] = []
    try:
        from llm_discovery.candidate_store import CANDIDATE_TTL_DAYS, CANDIDATE_TTL_MIN_DAYS
        from llm_discovery.model_info_store import DEFAULT_TTL_DAYS

        if CANDIDATE_TTL_DAYS != FROZEN_CANDIDATE_TTL_DAYS:
            failures.append(f"CANDIDATE_TTL_DAYS {CANDIDATE_TTL_DAYS} != frozen {FROZEN_CANDIDATE_TTL_DAYS}")
        if CANDIDATE_TTL_MIN_DAYS != FROZEN_CANDIDATE_TTL_MIN_DAYS:
            failures.append(f"CANDIDATE_TTL_MIN_DAYS {CANDIDATE_TTL_MIN_DAYS} != frozen {FROZEN_CANDIDATE_TTL_MIN_DAYS}")
        if DEFAULT_TTL_DAYS != FROZEN_DEFAULT_TTL_DAYS:
            failures.append(f"DEFAULT_TTL_DAYS {DEFAULT_TTL_DAYS} != frozen {FROZEN_DEFAULT_TTL_DAYS}")
    except Exception as exc:
        failures.append(f"check_candidate_ttl exception: {exc}")
    return {"ok": not failures, "failures": failures}


def check_store_semantics() -> dict[str, Any]:
    """Verify Derived Cache / store semantics unchanged (STORE_FILE_VERSION, rebuild)."""
    failures: list[str] = []
    try:
        from llm_discovery.model_info_store import (
            BENCHMARK_TTL_DAYS,
            PRICING_TTL_DAYS,
            STORE_FILE_VERSION,
        )

        if STORE_FILE_VERSION != FROZEN_STORE_FILE_VERSION:
            failures.append(f"STORE_FILE_VERSION {STORE_FILE_VERSION} != frozen {FROZEN_STORE_FILE_VERSION}")
        if PRICING_TTL_DAYS != FROZEN_PRICING_TTL_DAYS:
            failures.append(f"PRICING_TTL_DAYS {PRICING_TTL_DAYS} != frozen {FROZEN_PRICING_TTL_DAYS}")
        if BENCHMARK_TTL_DAYS != FROZEN_BENCHMARK_TTL_DAYS:
            failures.append(f"BENCHMARK_TTL_DAYS {BENCHMARK_TTL_DAYS} != frozen {FROZEN_BENCHMARK_TTL_DAYS}")
        # Verify cache_db rebuild still available and schema unchanged (presence check)
        from llm_discovery import cache_db as _cdb

        if not hasattr(_cdb, "rebuild_cache_db") or not hasattr(_cdb, "verify_cache_db"):
            failures.append("cache_db rebuild/verify missing")
    except Exception as exc:
        failures.append(f"check_store_semantics exception: {exc}")
    return {"ok": not failures, "failures": failures}


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------

def _empty_metrics() -> dict[str, Any]:
    return {
        "totals": {"keep": 0, "uncertain": 0, "drop": 0, "error": 0},
        "evidence_levels": {"strong": 0, "moderate": 0, "weak": 0, "none": 0},
        "llm_calls": 0,
        "web_searches": 0,
        "wall_duration_s": 0.0,
    }


def collect_metrics(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Collect build metrics from results dir or telemetry dict.

    Accepts:
      - Path to results directory (scans *.yaml keep/uncertain/drop/error and evidence_level)
      - Path to single telemetry JSON/YAML (not used here)
      - Dict already containing totals/evidence_levels/llm_calls etc (pass-through normalized)
    """
    if isinstance(source, dict):
        # Telemetry dict pass-through (from build_all return)
        # Handle both nested telemetry and flat
        data = source.get("telemetry", source) if isinstance(source, dict) else source
        # If data itself has totals/evidence_levels, use it
        if "totals" in data or "evidence_levels" in data:
            out = _empty_metrics()
            # totals
            for k in ("keep", "uncertain", "drop", "error"):
                try:
                    out["totals"][k] = int(data.get("totals", {}).get(k, data.get(k, 0)) or 0)
                except Exception:
                    out["totals"][k] = 0
            # evidence levels
            for lvl in ("strong", "moderate", "weak", "none"):
                try:
                    out["evidence_levels"][lvl] = int(data.get("evidence_levels", {}).get(lvl, 0) or 0)
                except Exception:
                    out["evidence_levels"][lvl] = 0
            # also fallback to direct keys
            if out["evidence_levels"]["strong"] == 0 and out["evidence_levels"]["moderate"] == 0:
                # maybe evidence_levels nested differently, try flat
                for lvl in ("strong", "moderate", "weak", "none"):
                    if lvl in data:
                        try:
                            out["evidence_levels"][lvl] = int(data[lvl])
                        except Exception:
                            pass
            out["llm_calls"] = int(data.get("llm_calls", data.get("search", {}).get("judge_calls", 0)) or 0)
            out["web_searches"] = int(data.get("web_searches", data.get("search", {}).get("calls", 0)) or 0)
            wd = data.get("wall_duration_s", data.get("wall_seconds", 0)) or 0
            try:
                out["wall_duration_s"] = float(wd)
            except Exception:
                out["wall_duration_s"] = 0.0
            # handle nested search snapshot
            if "search" in data and isinstance(data["search"], dict):
                if out["llm_calls"] == 0:
                    out["llm_calls"] = int(data["search"].get("judge_calls", 0) or 0)
                if out["web_searches"] == 0:
                    out["web_searches"] = int(data["search"].get("calls", 0) or 0)
            return out
        # fallback: treat as already metrics
        return dict(source)

    p = Path(source)
    if not p.exists():
        return _empty_metrics()
    # If p is a file, try to read as single yaml/json metrics snapshot
    if p.is_file():
        try:
            txt = p.read_text()
            data = yaml.safe_load(txt) if p.suffix in (".yaml", ".yml") else __import__("json").loads(txt)
            if isinstance(data, dict):
                return collect_metrics(data)
        except Exception:
            pass
        return _empty_metrics()

    # Directory: scan results YAMLs
    out = _empty_metrics()
    for yf in sorted(p.glob("*.yaml")):
        try:
            data = yaml.safe_load(yf.read_text()) or {}
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for bucket in ("keep", "uncertain", "drop", "drop_llm", "error"):
            lst = data.get(bucket) or []
            if not isinstance(lst, list):
                continue
            # totals bucket mapping: drop_llm -> drop
            tkey = "drop" if bucket in ("drop", "drop_llm") else bucket
            if tkey in out["totals"]:
                out["totals"][tkey] += len(lst)
            # evidence levels per record
            for rec in lst:
                if not isinstance(rec, dict):
                    continue
                lvl = str(rec.get("evidence_level", "")).strip().lower()
                if lvl in out["evidence_levels"]:
                    out["evidence_levels"][lvl] += 1
                elif lvl == "" and str(rec.get("decision", "")).strip().lower() == "error":
                    out["evidence_levels"]["none"] += 1
        # provider-level telemetry not in yaml; llm_calls/web_searches left 0
    return out


def diff_metrics(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Delta current - baseline for all metrics."""
    deltas: dict[str, Any] = {}
    for key in ("totals", "evidence_levels"):
        deltas[key] = {}
        for k in set(list(baseline.get(key, {}).keys()) + list(current.get(key, {}).keys())):
            b = int(baseline.get(key, {}).get(k, 0) or 0)
            c = int(current.get(key, {}).get(k, 0) or 0)
            deltas[key][k] = c - b
    for k in ("llm_calls", "web_searches"):
        b = int(baseline.get(k, 0) or 0)
        c = int(current.get(k, 0) or 0)
        deltas[k] = c - b
    # wall duration float
    try:
        b = float(baseline.get("wall_duration_s", 0) or 0)
        c = float(current.get("wall_duration_s", 0) or 0)
        deltas["wall_duration_s"] = round(c - b, 3)
    except Exception:
        deltas["wall_duration_s"] = 0.0
    return deltas


# ---------------------------------------------------------------------------
# Evidence-backed promotion check
# ---------------------------------------------------------------------------

def _has_aa(rec: dict[str, Any]) -> bool:
    """AA evidence: numeric score present and >0 (aa_model_id alone not enough)."""
    v = rec.get("aa_score")
    if v is not None:
        try:
            if float(v) > 0:
                return True
        except Exception:
            # non-numeric score still counts as evidence presence
            return True
    return False


def _has_bench(rec: dict[str, Any]) -> bool:
    """Benchmark evidence: scores dict non-empty."""
    bm = rec.get("benchmarks")
    if isinstance(bm, dict):
        scores = bm.get("scores") or {}
        if isinstance(scores, dict) and len(scores) > 0:
            # require at least one numeric score
            for sv in scores.values():
                try:
                    sc = sv.get("score") if isinstance(sv, dict) else sv
                    if sc is not None and float(sc) != 0:
                        return True
                except Exception:
                    return True
    return False


def _has_new_evidence(baseline: dict[str, Any], current: dict[str, Any], cur_level: str = "") -> bool:
    """True if current has new AA/bench (and for moderate, verified URL) that baseline lacked."""
    b_aa = _has_aa(baseline)
    c_aa = _has_aa(current)
    if not b_aa and c_aa:
        return True
    b_bench = _has_bench(baseline)
    c_bench = _has_bench(current)
    if not b_bench and c_bench:
        return True
    # bench superset with meaningful new key
    if b_bench and c_bench:
        try:
            b_scores = (baseline.get("benchmarks") or {}).get("scores") or {}
            c_scores = (current.get("benchmarks") or {}).get("scores") or {}
            if isinstance(b_scores, dict) and isinstance(c_scores, dict):
                if len(c_scores) > len(b_scores):
                    for k in c_scores:
                        if k not in b_scores:
                            return True
                else:
                    for k in c_scores:
                        if k not in b_scores:
                            return True
        except Exception:
            pass
    # For moderate, a newly verified first-party URL can legitimately promote (ADR 0008 claim-only moderate)
    # Strong never via URL; keep bench/AA gate for strong.
    if cur_level == "moderate":
        b_urls = any("http" in str(e) for e in baseline.get("evidence", []) or [])
        c_urls = any("http" in str(e) for e in current.get("evidence", []) or [])
        if not b_urls and c_urls:
            return True
    return False


def check_evidence_backed_promotions(
    baseline_recs: dict[str, dict[str, Any]],
    current_recs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Check strong/moderate promotions are evidence-backed.

    Baseline and current are model_id -> record dicts. Canonical matching via
    evidence_identity.canonical_key when available, else lower/strip.
    Promotion = baseline evidence_level in weak/none/error/uncertain and current in moderate/strong.
    Unbacked when no new AA/bench/URL found.
    """
    try:
        from llm_discovery.evidence_identity import canonical_key
    except Exception:
        def canonical_key(x):  # type: ignore
            return str(x).lower().strip()

    def _canon_map(recs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for mid, rec in recs.items():
            try:
                ck = canonical_key(mid) or canonical_key(str(rec.get("provider_model_id") or rec.get("model_id") or mid))
            except Exception:
                ck = str(mid).lower()
            if ck not in out:
                out[ck] = rec
        return out

    b_map = _canon_map(baseline_recs)
    c_map = _canon_map(current_recs)

    weak_levels = {"weak", "none", "error", ""}
    strong_levels = {"moderate", "strong"}
    unbacked: list[dict[str, Any]] = []

    for ck, cur in c_map.items():
        cur_lvl = str(cur.get("evidence_level", "")).strip().lower()
        if cur_lvl not in strong_levels:
            continue
        base = b_map.get(ck)
        if base is None:
            # new model appeared as strong/moderate - require AA/bench evidence
            if not (_has_aa(cur) or _has_bench(cur)):
                unbacked.append({"model_id": cur.get("provider_model_id") or cur.get("model_id") or ck, "reason": "new model strong/moderate without AA/bench"})
            continue
        base_lvl = str(base.get("evidence_level", "")).strip().lower()
        # also consider decision tier: uncertain/error baseline
        base_dec = str(base.get("decision", "")).strip().lower()
        if base_dec in ("error", "uncertain") and cur_lvl in strong_levels:
            # treat as promotion from weak/none
            base_lvl = "weak"
        if base_lvl in weak_levels and cur_lvl in strong_levels:
            if not _has_new_evidence(base, cur, cur_lvl):
                unbacked.append({
                    "model_id": cur.get("provider_model_id") or cur.get("model_id") or ck,
                    "baseline_level": base_lvl,
                    "current_level": cur_lvl,
                    "reason": "promotion without new AA/bench evidence",
                })
    return {"ok": not unbacked, "unbacked": unbacked}


# ---------------------------------------------------------------------------
# Gate aggregation + reporting
# ---------------------------------------------------------------------------

def gate(
    baseline_metrics: dict[str, Any],
    current_metrics: dict[str, Any],
    baseline_recs: dict[str, dict[str, Any]] | None = None,
    current_recs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """CI gate: thresholds frozen, TTL/store unchanged, promotions evidence-backed."""
    baseline_recs = baseline_recs or {}
    current_recs = current_recs or {}
    t = check_thresholds_frozen()
    ttl = check_candidate_ttl()
    store = check_store_semantics()
    ev = check_evidence_backed_promotions(baseline_recs, current_recs)

    failures: list[str] = []
    if not t["ok"]:
        failures.extend([f"thresholds: {f}" for f in t["failures"]])
    if not ttl["ok"]:
        failures.extend([f"ttl: {f}" for f in ttl["failures"]])
    if not store["ok"]:
        failures.extend([f"store: {f}" for f in store["failures"]])
    if not ev["ok"]:
        for u in ev["unbacked"]:
            failures.append(f"unbacked promotion: {u.get('model_id')} {u.get('reason')} ({u.get('baseline_level')}->{u.get('current_level')})")

    deltas = diff_metrics(baseline_metrics, current_metrics)
    ok = not failures
    return {
        "ok": ok,
        "failures": failures,
        "thresholds": t,
        "ttl": ttl,
        "store": store,
        "evidence_backed": ev,
        "deltas": deltas,
        "baseline": baseline_metrics,
        "current": current_metrics,
    }


def format_report(baseline: dict[str, Any], current: dict[str, Any], gate_result: dict[str, Any] | None = None) -> str:
    """Markdown report for before/after metrics."""
    deltas = diff_metrics(baseline, current)
    lines = [
        "| Metric | Baseline | Current | Delta |",
        "|--------|----------|---------|-------|",
    ]

    def _row(name, b, c, d):
        sign = f"{d:+d}" if isinstance(d, int) else f"{d:+.2f}" if isinstance(d, float) else str(d)
        return f"| {name} | {b} | {c} | {sign} |"

    for k in ("keep", "uncertain", "drop", "error"):
        b = baseline.get("totals", {}).get(k, 0)
        c = current.get("totals", {}).get(k, 0)
        d = deltas.get("totals", {}).get(k, 0)
        lines.append(_row(k, b, c, d))
    for lvl in ("strong", "moderate", "weak", "none"):
        b = baseline.get("evidence_levels", {}).get(lvl, 0)
        c = current.get("evidence_levels", {}).get(lvl, 0)
        d = deltas.get("evidence_levels", {}).get(lvl, 0)
        lines.append(_row(f"evidence:{lvl}", b, c, d))
    for k in ("llm_calls", "web_searches"):
        b = baseline.get(k, 0)
        c = current.get(k, 0)
        d = deltas.get(k, 0)
        lines.append(_row(k, b, c, d))
    # wall duration float
    b = baseline.get("wall_duration_s", 0)
    c = current.get("wall_duration_s", 0)
    d = deltas.get("wall_duration_s", 0)
    lines.append(f"| wall_duration_s | {b:.2f} | {c:.2f} | {d:+.2f} |")

    if gate_result is not None:
        lines.append("")
        status = "PASS" if gate_result.get("ok") else "FAIL"
        lines.append(f"Gate: **{status}**")
        if gate_result.get("failures"):
            lines.append("")
            for f in gate_result["failures"]:
                lines.append(f"- {f}")
        if not gate_result.get("evidence_backed", {}).get("ok", True):
            ub = gate_result["evidence_backed"]["unbacked"]
            lines.append(f"- unbacked promotions: {len(ub)}")
    return "\n".join(lines)


def _collect_records_from_dir(results_dir: Path) -> dict[str, dict[str, Any]]:
    """Load model_id -> record dict from results dir for evidence-backed check."""
    out: dict[str, dict[str, Any]] = {}
    for yf in sorted(Path(results_dir).glob("*.yaml")):
        try:
            data = yaml.safe_load(yf.read_text()) or {}
        except Exception:
            continue
        for bucket in ("keep", "uncertain", "drop", "drop_llm", "error"):
            for rec in data.get(bucket) or []:
                if not isinstance(rec, dict):
                    continue
                mid = str(rec.get("model_id") or rec.get("provider_model_id") or "").strip()
                if not mid:
                    continue
                # keep last occurrence
                out[mid] = rec
    return out


def run_fixed_snapshot_harness(
    snapshot: dict[str, list[dict[str, Any]]],
    baseline_fn: Any,
    improved_fn: Any,
    aa: Any = None,
    md: Any = None,
    cache: Any = None,
) -> dict[str, Any]:
    """Run baseline vs improved discovery on same snapshot (pure, no IO).

    snapshot: provider -> list of {id: model_id}
    baseline_fn/improved_fn: (provider, model_id, aa, md, cache) -> record dict
    Returns {baseline: metrics, current: metrics, deltas, gate}.
    """
    baseline_recs: dict[str, dict[str, Any]] = {}
    current_recs: dict[str, dict[str, Any]] = {}

    baseline_counts = {"keep": 0, "uncertain": 0, "drop": 0, "error": 0}
    current_counts = {"keep": 0, "uncertain": 0, "drop": 0, "error": 0}
    baseline_levels = {"strong": 0, "moderate": 0, "weak": 0, "none": 0}
    current_levels = {"strong": 0, "moderate": 0, "weak": 0, "none": 0}

    for provider, models in snapshot.items():
        for m in models:
            mid = str(m.get("id") or m.get("model_id") or "").strip()
            if not mid:
                continue
            b_rec = baseline_fn(provider, mid, aa, md, cache) if callable(baseline_fn) else {}
            c_rec = improved_fn(provider, mid, aa, md, cache) if callable(improved_fn) else {}
            baseline_recs[mid] = b_rec
            current_recs[mid] = c_rec

            def _tally(rec, counts, levels):
                dec = str(rec.get("decision", "")).strip().lower()
                if dec == "keep":
                    counts["keep"] += 1
                elif dec == "uncertain":
                    counts["uncertain"] += 1
                elif dec == "drop":
                    counts["drop"] += 1
                elif dec == "error":
                    counts["error"] += 1
                else:
                    # map via evidence_level fallback
                    lvl = str(rec.get("evidence_level", "")).strip().lower()
                    if lvl in ("weak", "none"):
                        counts["uncertain"] += 1
                    elif lvl in ("strong", "moderate"):
                        counts["keep"] += 1
                lvl = str(rec.get("evidence_level", "")).strip().lower()
                if lvl in levels:
                    levels[lvl] += 1
                elif dec == "error":
                    levels["none"] += 1

            _tally(b_rec, baseline_counts, baseline_levels)
            _tally(c_rec, current_counts, current_levels)

    baseline_metrics = {
        "totals": baseline_counts,
        "evidence_levels": baseline_levels,
        "llm_calls": 0,
        "web_searches": 0,
        "wall_duration_s": 0.0,
    }
    current_metrics = {
        "totals": current_counts,
        "evidence_levels": current_levels,
        "llm_calls": 0,
        "web_searches": 0,
        "wall_duration_s": 0.0,
    }
    g = gate(baseline_metrics, current_metrics, baseline_recs, current_recs)
    deltas = diff_metrics(baseline_metrics, current_metrics)
    return {"baseline": baseline_metrics, "current": current_metrics, "deltas": deltas, "gate": g, "baseline_recs": baseline_recs, "current_recs": current_recs}
