"""Issue #235: Before/after measurement harness (gate).

Harness runs baseline vs improved on same snapshot and reports deltas for
all metrics; strong/moderate promotions are evidence-backed (new AA/bench
found), not threshold change; Candidate TTL and Derived Cache semantics unchanged;
gate is CI-enforceable and fails if thresholds loosened.

Tests are pure, no network/LLM/files required beyond tmp_path.
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

import pytest


def _write_provider_yaml(path: Path, provider: str, keep, uncertain, drop, error):
    payload = {
        "provider": provider,
        "evaluated_at": "2026-09-17T00:00:00+00:00",
        "keep": keep,
        "uncertain": uncertain,
        "drop_llm": drop,
        "error": error,
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def _rec(model_id, evidence_level, decision="keep", tier="flash", aa_score=None, coding_score=None, benchmarks=None, evidence=None):
    r = {
        "model_id": model_id,
        "provider_model_id": model_id,
        "decision": decision,
        "tier": tier,
        "evidence_level": evidence_level,
        "confidence": 0.9,
        "evidence": evidence or [],
        "benchmarks": benchmarks or {"scores": {}, "raw_benchmarks": []},
        "aa_score": aa_score,
        "coding_score": coding_score,
    }
    return r


# ---------------------------------------------------------------------------
# Threshold freeze gate
# ---------------------------------------------------------------------------

def test_thresholds_frozen_passes_on_current_code():
    from llm_discovery.build_gate import check_thresholds_frozen

    result = check_thresholds_frozen()
    assert result["ok"] is True, result["failures"]
    assert result["failures"] == []


def test_thresholds_frozen_fails_when_min_score_loosened(monkeypatch):
    import llm_discovery.benchmarks as bm
    from llm_discovery.build_gate import check_thresholds_frozen

    monkeypatch.setattr(bm, "MIN_SCORE", 15.0)
    result = check_thresholds_frozen()
    assert result["ok"] is False
    assert any("MIN_SCORE" in f for f in result["failures"])


def test_thresholds_frozen_fails_when_max_score_loosened(monkeypatch):
    import llm_discovery.benchmarks as bm
    from llm_discovery.build_gate import check_thresholds_frozen

    monkeypatch.setattr(bm, "MAX_SCORE", 30.0)
    result = check_thresholds_frozen()
    assert result["ok"] is False


def test_thresholds_frozen_detects_deterministic_loosening(monkeypatch):
    from llm_discovery import policy_gate
    from llm_discovery.build_gate import check_thresholds_frozen

    orig = policy_gate.PolicyGate._deterministic_evidence_level

    def loosened(verified_score, coding_score, profile, provider_claims=None, model_id=None):
        # loosened: AA >=15 promotes to moderate (should be >=24)
        if verified_score is not None and verified_score >= 15:
            return "moderate"
        return orig(verified_score, coding_score, profile, provider_claims, model_id)

    monkeypatch.setattr(policy_gate.PolicyGate, "_deterministic_evidence_level", staticmethod(loosened))
    result = check_thresholds_frozen()
    assert result["ok"] is False
    assert any("deterministic" in f.lower() or "AA" in f for f in result["failures"])


# ---------------------------------------------------------------------------
# Candidate TTL and Derived Cache semantics unchanged
# ---------------------------------------------------------------------------

def test_candidate_ttl_unchanged_passes():
    from llm_discovery.build_gate import check_candidate_ttl

    result = check_candidate_ttl()
    assert result["ok"] is True, result["failures"]


def test_candidate_ttl_fails_when_loosened(monkeypatch):
    import llm_discovery.candidate_store as cs
    from llm_discovery.build_gate import check_candidate_ttl

    monkeypatch.setattr(cs, "CANDIDATE_TTL_DAYS", 30)
    result = check_candidate_ttl()
    assert result["ok"] is False


def test_store_semantics_unchanged_passes():
    from llm_discovery.build_gate import check_store_semantics

    result = check_store_semantics()
    assert result["ok"] is True, result["failures"]


def test_store_semantics_fails_when_version_changed(monkeypatch):
    import llm_discovery.model_info_store as mis
    from llm_discovery.build_gate import check_store_semantics

    monkeypatch.setattr(mis, "STORE_FILE_VERSION", 99)
    result = check_store_semantics()
    assert result["ok"] is False


# ---------------------------------------------------------------------------
# Metrics collection and diff
# ---------------------------------------------------------------------------

def test_collect_metrics_from_results_dir(tmp_path):
    from llm_discovery.build_gate import collect_metrics

    results = tmp_path / "results"
    results.mkdir()
    # provider A: 1 keep strong, 1 uncertain weak, 1 drop, 1 error
    _write_provider_yaml(
        results / "prov_a.yaml", "prov_a",
        keep=[_rec("m-keep-1", "strong", evidence=["https://example.com/swe"], benchmarks={"scores": {"swe_bench_verified": {"score": 55}}, "raw_benchmarks": []}, aa_score=55)],
        uncertain=[_rec("m-weak-1", "weak", decision="uncertain", tier="uncertain")],
        drop=[_rec("m-drop-1", "weak", decision="drop", tier="drop")],
        error=[_rec("m-err-1", "none", decision="error", tier="error")],
    )
    _write_provider_yaml(
        results / "prov_b.yaml", "prov_b",
        keep=[_rec("m-keep-2", "moderate", aa_score=24)],
        uncertain=[],
        drop=[],
        error=[],
    )
    metrics = collect_metrics(results)
    assert metrics["totals"]["keep"] == 2
    assert metrics["totals"]["uncertain"] == 1
    assert metrics["totals"]["drop"] == 1
    assert metrics["totals"]["error"] == 1
    assert metrics["evidence_levels"]["strong"] == 1
    assert metrics["evidence_levels"]["moderate"] == 1
    assert metrics["evidence_levels"]["weak"] == 2  # drop weak + uncertain weak
    assert metrics["evidence_levels"]["none"] == 1


def test_collect_metrics_also_handles_telemetry_dict():
    from llm_discovery.build_gate import collect_metrics

    # build_all returns telemetry dict with totals/evidence_levels/llm_calls etc
    telemetry = {
        "totals": {"keep": 198, "uncertain": 614, "drop": 400, "error": 36},
        "evidence_levels": {"strong": 120, "moderate": 80, "weak": 551, "none": 361},
        "llm_calls": 120,
        "web_searches": 45,
        "wall_duration_s": 123.4,
    }
    metrics = collect_metrics(telemetry)
    assert metrics["totals"]["uncertain"] == 614
    assert metrics["llm_calls"] == 120
    assert metrics["web_searches"] == 45


def test_diff_metrics_reports_deltas():
    from llm_discovery.build_gate import diff_metrics

    baseline = {
        "totals": {"keep": 100, "uncertain": 600, "drop": 400, "error": 40},
        "evidence_levels": {"strong": 80, "moderate": 20, "weak": 500, "none": 140},
        "llm_calls": 100,
        "web_searches": 50,
        "wall_duration_s": 200.0,
    }
    current = {
        "totals": {"keep": 120, "uncertain": 550, "drop": 400, "error": 30},
        "evidence_levels": {"strong": 90, "moderate": 30, "weak": 460, "none": 130},
        "llm_calls": 95,
        "web_searches": 45,
        "wall_duration_s": 210.0,
    }
    deltas = diff_metrics(baseline, current)
    assert deltas["totals"]["uncertain"] == -50
    assert deltas["totals"]["error"] == -10
    assert deltas["totals"]["keep"] == 20
    assert deltas["evidence_levels"]["weak"] == -40
    assert deltas["llm_calls"] == -5
    assert deltas["wall_duration_s"] == 10.0


# ---------------------------------------------------------------------------
# Evidence-backed promotions
# ---------------------------------------------------------------------------

def _bench(scores):
    return {"scores": scores, "raw_benchmarks": []}


def test_evidence_backed_promotion_passes_when_new_bench_found():
    from llm_discovery.build_gate import check_evidence_backed_promotions

    baseline = {
        "glm-5.3": _rec("glm-5.3", "weak", benchmarks=_bench({})),
    }
    current = {
        "glm-5.3": _rec("glm-5.3", "strong", benchmarks=_bench({"swe_bench_verified": {"score": 55}}), aa_score=None, evidence=["https://example.com/swe"]),
    }
    result = check_evidence_backed_promotions(baseline, current)
    assert result["ok"] is True
    assert result["unbacked"] == []


def test_evidence_backed_promotion_passes_when_new_aa_found():
    from llm_discovery.build_gate import check_evidence_backed_promotions

    baseline = {
        "model-x": _rec("model-x", "weak", aa_score=None, benchmarks=_bench({})),
    }
    current = {
        "model-x": _rec("model-x", "moderate", aa_score=30, benchmarks=_bench({})),
    }
    result = check_evidence_backed_promotions(baseline, current)
    assert result["ok"] is True


def test_evidence_backed_promotion_fails_when_no_new_evidence():
    from llm_discovery.build_gate import check_evidence_backed_promotions

    baseline = {
        "model-y": _rec("model-y", "weak", aa_score=None, benchmarks=_bench({})),
    }
    current = {
        "model-y": _rec("model-y", "strong", aa_score=None, benchmarks=_bench({})),
    }
    result = check_evidence_backed_promotions(baseline, current)
    assert result["ok"] is False
    assert len(result["unbacked"]) == 1
    assert result["unbacked"][0]["model_id"] == "model-y"


def test_evidence_backed_respects_canonical_identity():
    from llm_discovery.build_gate import check_evidence_backed_promotions

    # baseline key uses dot, current uses hyphen but canonical same
    baseline = {
        "glm-5.3": _rec("glm-5.3", "weak", benchmarks=_bench({})),
    }
    current = {
        "glm-5-3": _rec("glm-5-3", "strong", benchmarks=_bench({"swe_bench_verified": {"score": 55}})),
    }
    result = check_evidence_backed_promotions(baseline, current)
    # should match via canonical_key and see evidence, so pass
    assert result["ok"] is True


def test_no_promotion_no_check_needed():
    from llm_discovery.build_gate import check_evidence_backed_promotions

    baseline = {"a": _rec("a", "weak", benchmarks=_bench({}))}
    current = {"a": _rec("a", "weak", benchmarks=_bench({}))}
    result = check_evidence_backed_promotions(baseline, current)
    assert result["ok"] is True
    assert result["unbacked"] == []


# ---------------------------------------------------------------------------
# Gate integration: CI-enforceable
# ---------------------------------------------------------------------------

def test_gate_passes_when_evidence_backed_and_thresholds_frozen(tmp_path):
    from llm_discovery.build_gate import gate

    baseline_metrics = {
        "totals": {"keep": 100, "uncertain": 600, "drop": 400, "error": 40},
        "evidence_levels": {"strong": 80, "moderate": 20, "weak": 500, "none": 140},
        "llm_calls": 100,
        "web_searches": 50,
        "wall_duration_s": 200.0,
    }
    current_metrics = {
        "totals": {"keep": 110, "uncertain": 580, "drop": 400, "error": 30},
        "evidence_levels": {"strong": 85, "moderate": 25, "weak": 480, "none": 130},
        "llm_calls": 90,
        "web_searches": 40,
        "wall_duration_s": 210.0,
    }
    baseline_recs = {"m1": _rec("m1", "weak", benchmarks=_bench({}))}
    current_recs = {"m1": _rec("m1", "moderate", aa_score=30, benchmarks=_bench({}))}

    result = gate(baseline_metrics, current_metrics, baseline_recs, current_recs)
    assert result["ok"] is True
    assert "deltas" in result
    assert result["thresholds"]["ok"] is True


def test_gate_fails_when_unbacked_promotion(tmp_path):
    from llm_discovery.build_gate import gate

    baseline_metrics = {
        "totals": {"keep": 100, "uncertain": 600, "drop": 400, "error": 40},
        "evidence_levels": {"strong": 80, "moderate": 20, "weak": 500, "none": 140},
        "llm_calls": 100,
        "web_searches": 50,
        "wall_duration_s": 200.0,
    }
    current_metrics = {
        "totals": {"keep": 150, "uncertain": 500, "drop": 400, "error": 40},
        "evidence_levels": {"strong": 120, "moderate": 30, "weak": 400, "none": 140},
        "llm_calls": 100,
        "web_searches": 50,
        "wall_duration_s": 200.0,
    }
    baseline_recs = {"m1": _rec("m1", "weak", benchmarks=_bench({}))}
    current_recs = {"m1": _rec("m1", "strong", benchmarks=_bench({}))}  # no new evidence

    result = gate(baseline_metrics, current_metrics, baseline_recs, current_recs)
    assert result["ok"] is False
    assert any("unbacked" in f.lower() or "evidence" in f.lower() for f in result["failures"])


def test_gate_fails_when_thresholds_loosened(monkeypatch):
    import llm_discovery.benchmarks as bm
    from llm_discovery.build_gate import gate

    monkeypatch.setattr(bm, "MIN_SCORE", 15.0)
    baseline_metrics = {
        "totals": {"keep": 100, "uncertain": 600, "drop": 400, "error": 40},
        "evidence_levels": {"strong": 80, "moderate": 20, "weak": 500, "none": 140},
        "llm_calls": 100,
        "web_searches": 50,
        "wall_duration_s": 200.0,
    }
    current_metrics = baseline_metrics
    result = gate(baseline_metrics, current_metrics, {}, {})
    assert result["ok"] is False
    assert any("threshold" in f.lower() or "MIN_SCORE" in f for f in result["failures"])


def test_collect_metrics_from_results_dir_integration_reports_all_fields(tmp_path):
    from llm_discovery.build_gate import collect_metrics, diff_metrics, format_report

    base_dir = tmp_path / "base"
    base_dir.mkdir()
    cur_dir = tmp_path / "cur"
    cur_dir.mkdir()
    _write_provider_yaml(
        base_dir / "p.yaml", "p",
        keep=[_rec("a", "strong")],
        uncertain=[_rec("b", "weak", decision="uncertain", tier="uncertain"), _rec("c", "weak", decision="uncertain", tier="uncertain")],
        drop=[_rec("d", "weak", decision="drop", tier="drop")],
        error=[_rec("e", "none", decision="error", tier="error")],
    )
    _write_provider_yaml(
        cur_dir / "p.yaml", "p",
        keep=[_rec("a", "strong"), _rec("b", "moderate", aa_score=30)],
        uncertain=[_rec("c", "weak", decision="uncertain", tier="uncertain")],
        drop=[_rec("d", "weak", decision="drop", tier="drop")],
        error=[],
    )
    base_m = collect_metrics(base_dir)
    cur_m = collect_metrics(cur_dir)
    deltas = diff_metrics(base_m, cur_m)
    assert deltas["totals"]["uncertain"] == -1
    assert deltas["totals"]["error"] == -1
    report = format_report(base_m, cur_m)
    assert "uncertain" in report.lower()
    assert "error" in report.lower()
    assert "strong" in report.lower() or "evidence" in report.lower()


def test_fixed_snapshot_harness_runs_same_snapshot(tmp_path):
    """Same-snapshot harness: runs baseline and improved discovery on same provider snapshot."""
    from llm_discovery.build_gate import run_fixed_snapshot_harness

    # Fake snapshot: two provider model lists with fixed evidence
    snapshot = {
        "prov_a": [{"id": "glm-5.3"}, {"id": "ghost-1"}],
    }

    def fake_discover_baseline(provider, model_id, aa, md, cache):
        # baseline: no alias recovery, ghost stays weak
        if model_id == "glm-5.3":
            return _rec(model_id, "weak", benchmarks=_bench({}))
        return _rec(model_id, "weak", benchmarks=_bench({}))

    def fake_discover_improved(provider, model_id, aa, md, cache):
        if model_id == "glm-5.3":
            return _rec(model_id, "strong", benchmarks=_bench({"swe_bench_verified": {"score": 55}}))
        return _rec(model_id, "weak", benchmarks=_bench({}))

    result = run_fixed_snapshot_harness(snapshot, fake_discover_baseline, fake_discover_improved)
    assert "baseline" in result and "current" in result and "deltas" in result
    assert result["deltas"]["totals"]["uncertain"] < 0 or result["deltas"]["evidence_levels"]["weak"] < 0
    assert result["gate"]["ok"] is True
