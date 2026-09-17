"""Issue #240: Fixed-snapshot audit harness with evidence-backed gate.

Covers deltas for weak/none, error, strong/moderate/keep/drop, uncertain, LLM calls,
web searches, wall duration; evidence-backed promotions via new AA/bench/verified URL;
fixed catalogs fingerprint; Candidate TTL and Derived Cache semantics; CI gate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
import pytest


def _rec(model_id, evidence_level, decision="keep", tier="flash", aa_score=None, evidence=None, benchmarks=None, provider_claims=None):
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
    }
    if provider_claims is not None:
        r["provider_claims"] = provider_claims
    return r


def _bench(scores):
    return {"scores": scores, "raw_benchmarks": []}


# ---------------------------------------------------------------------------
# Thresholds frozen (ADR 0008)
# ---------------------------------------------------------------------------

def test_thresholds_frozen_passes():
    from llm_discovery.audit_harness import check_thresholds_frozen

    result = check_thresholds_frozen()
    assert result["ok"] is True, result["failures"]


def test_thresholds_frozen_fails_when_min_score_loosened(monkeypatch):
    import llm_discovery.benchmarks as bm
    from llm_discovery.audit_harness import check_thresholds_frozen

    monkeypatch.setattr(bm, "MIN_SCORE", 15.0)
    result = check_thresholds_frozen()
    assert result["ok"] is False
    assert any("MIN_SCORE" in f for f in result["failures"])


def test_thresholds_frozen_fails_when_max_score_loosened(monkeypatch):
    import llm_discovery.benchmarks as bm
    from llm_discovery.audit_harness import check_thresholds_frozen

    monkeypatch.setattr(bm, "MAX_SCORE", 30.0)
    result = check_thresholds_frozen()
    assert result["ok"] is False


def test_thresholds_frozen_detects_policygate_ladder_loosening(monkeypatch):
    from llm_discovery import policy_gate
    from llm_discovery.audit_harness import check_thresholds_frozen

    orig = policy_gate.PolicyGate._deterministic_evidence_level

    def loosened(verified_score, coding_score, profile, provider_claims=None, model_id=None):
        if verified_score is not None and verified_score >= 15:
            return "moderate"
        return orig(verified_score, coding_score, profile, provider_claims, model_id)

    monkeypatch.setattr(policy_gate.PolicyGate, "_deterministic_evidence_level", staticmethod(loosened))
    result = check_thresholds_frozen()
    assert result["ok"] is False
    assert any("deterministic" in f.lower() for f in result["failures"])


# ---------------------------------------------------------------------------
# Candidate TTL and Derived Cache unchanged
# ---------------------------------------------------------------------------

def test_candidate_ttl_unchanged():
    from llm_discovery.audit_harness import check_candidate_ttl

    assert check_candidate_ttl()["ok"] is True


def test_candidate_ttl_fails_when_loosened(monkeypatch):
    import llm_discovery.candidate_store as cs
    from llm_discovery.audit_harness import check_candidate_ttl

    monkeypatch.setattr(cs, "CANDIDATE_TTL_DAYS", 30)
    assert check_candidate_ttl()["ok"] is False


def test_store_semantics_unchanged():
    from llm_discovery.audit_harness import check_store_semantics

    assert check_store_semantics()["ok"] is True


def test_store_semantics_fails_when_version_changed(monkeypatch):
    import llm_discovery.model_info_store as mis
    from llm_discovery.audit_harness import check_store_semantics

    monkeypatch.setattr(mis, "STORE_FILE_VERSION", 99)
    assert check_store_semantics()["ok"] is False


# ---------------------------------------------------------------------------
# Fixed catalogs snapshot fingerprint (data/artificial_analysis_models.json + models_dev + benchmarks)
# ---------------------------------------------------------------------------

def test_fixed_snapshot_fingerprint_uses_fixed_catalogs(tmp_path):
    from llm_discovery.audit_harness import FIXED_CATALOG_FILES, fixed_snapshot_fingerprint

    assert any("artificial_analysis_models.json" in str(p) for p in FIXED_CATALOG_FILES)
    assert any("models_dev_catalog.json" in str(p) for p in FIXED_CATALOG_FILES)
    assert any("benchmarks.json" in str(p) for p in FIXED_CATALOG_FILES)

    # create fake data dir with same basenames
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "artificial_analysis_models.json").write_text(json.dumps({"models": []}))
    (data_dir / "models_dev_catalog.json").write_text(json.dumps({"models": {}, "providers": {}}))
    # benchmarks.json missing allowed (derived)
    fp = fixed_snapshot_fingerprint(data_dir)
    assert fp["artificial_analysis_models.json"]["exists"] is True
    assert fp["models_dev_catalog.json"]["exists"] is True
    assert fp["benchmarks.json"]["exists"] is False
    # adding benchmarks.json should make it exist
    (data_dir / "benchmarks.json").write_text(json.dumps({"m": {"benchmarks": {}}}))
    fp2 = fixed_snapshot_fingerprint(data_dir)
    assert fp2["benchmarks.json"]["exists"] is True


def test_fixed_catalogs_match_passes_when_same_sha(tmp_path):
    from llm_discovery.audit_harness import fixed_snapshot_fingerprint, check_fixed_catalogs_match

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "artificial_analysis_models.json").write_text(json.dumps({"models": [{"id": "a"}]}))
    (data_dir / "models_dev_catalog.json").write_text(json.dumps({"models": {"x": {}}, "providers": {}}))
    fp = fixed_snapshot_fingerprint(data_dir)
    assert check_fixed_catalogs_match(fp, fp)["ok"] is True


def test_fixed_catalogs_match_fails_when_aa_sha_differs(tmp_path):
    from llm_discovery.audit_harness import check_fixed_catalogs_match

    fp_a = {
        "artificial_analysis_models.json": {"exists": True, "sha256": "aaaa"},
        "models_dev_catalog.json": {"exists": True, "sha256": "bbbb"},
        "benchmarks.json": {"exists": False},
    }
    fp_b = {
        "artificial_analysis_models.json": {"exists": True, "sha256": "cccc"},
        "models_dev_catalog.json": {"exists": True, "sha256": "bbbb"},
        "benchmarks.json": {"exists": False},
    }
    result = check_fixed_catalogs_match(fp_a, fp_b)
    assert result["ok"] is False
    assert any("artificial_analysis" in f for f in result["failures"])


def test_fixed_catalogs_benchmarks_mismatch_is_warn_only():
    from llm_discovery.audit_harness import check_fixed_catalogs_match

    fp_a = {
        "artificial_analysis_models.json": {"exists": True, "sha256": "aaaa"},
        "models_dev_catalog.json": {"exists": True, "sha256": "bbbb"},
        "benchmarks.json": {"exists": True, "sha256": "1111"},
    }
    fp_b = {
        "artificial_analysis_models.json": {"exists": True, "sha256": "aaaa"},
        "models_dev_catalog.json": {"exists": True, "sha256": "bbbb"},
        "benchmarks.json": {"exists": True, "sha256": "2222"},
    }
    assert check_fixed_catalogs_match(fp_a, fp_b)["ok"] is True


# ---------------------------------------------------------------------------
# Metrics deltas: weak/none, error, strong/moderate/keep/drop, uncertain, LLM calls, etc
# ---------------------------------------------------------------------------

def test_collect_metrics_and_diff_all_fields(tmp_path):
    from llm_discovery.audit_harness import collect_metrics, diff_metrics

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
    assert deltas["totals"]["drop"] == 0
    assert deltas["evidence_levels"]["weak"] == -40
    assert deltas["evidence_levels"]["none"] == -10
    assert deltas["evidence_levels"]["strong"] == 10
    assert deltas["evidence_levels"]["moderate"] == 10
    assert deltas["llm_calls"] == -5
    assert deltas["web_searches"] == -5
    assert deltas["wall_duration_s"] == 10.0


def test_collect_metrics_from_results_dir(tmp_path):
    from llm_discovery.audit_harness import collect_metrics

    results = tmp_path / "results"
    results.mkdir()
    payload = {
        "provider": "prov_a",
        "evaluated_at": "2026-09-17T00:00:00+00:00",
        "keep": [_rec("m-keep-1", "strong")],
        "uncertain": [_rec("m-weak-1", "weak", decision="uncertain", tier="uncertain")],
        "drop_llm": [_rec("m-drop-1", "weak", decision="drop", tier="drop")],
        "error": [_rec("m-err-1", "none", decision="error", tier="error")],
    }
    (results / "prov_a.yaml").write_text(yaml.safe_dump(payload))
    metrics = collect_metrics(results)
    assert metrics["totals"]["keep"] == 1
    assert metrics["totals"]["uncertain"] == 1
    assert metrics["totals"]["drop"] == 1
    assert metrics["totals"]["error"] == 1
    assert metrics["evidence_levels"]["strong"] == 1
    assert metrics["evidence_levels"]["weak"] == 2
    assert metrics["evidence_levels"]["none"] == 1


# ---------------------------------------------------------------------------
# Evidence-backed promotions (verified URL)
# ---------------------------------------------------------------------------

def test_evidence_backed_passes_when_new_bench_found():
    from llm_discovery.audit_harness import check_evidence_backed_promotions_verified

    baseline = {"glm-5.3": _rec("glm-5.3", "weak", benchmarks=_bench({}))}
    current = {"glm-5.3": _rec("glm-5.3", "strong", benchmarks=_bench({"swe_bench_verified": {"score": 55}}), evidence=["https://qwen.ai/blog/qwen3"])}
    assert check_evidence_backed_promotions_verified(baseline, current)["ok"] is True


def test_evidence_backed_passes_when_new_aa_found():
    from llm_discovery.audit_harness import check_evidence_backed_promotions_verified

    baseline = {"model-x": _rec("model-x", "weak", aa_score=None, benchmarks=_bench({}))}
    current = {"model-x": _rec("model-x", "moderate", aa_score=30, benchmarks=_bench({}))}
    assert check_evidence_backed_promotions_verified(baseline, current)["ok"] is True


def test_evidence_backed_passes_when_new_verified_url_for_moderate():
    from llm_discovery.audit_harness import check_evidence_backed_promotions_verified

    # moderate promotion via verified first-party URL (qwen.ai is allowlisted blog domain)
    baseline = {"qwen-3b": _rec("qwen-3b", "weak", benchmarks=_bench({}), evidence=[])}
    current = {"qwen-3b": _rec("qwen-3b", "moderate", benchmarks=_bench({}), evidence=["See https://qwen.ai/blog/qwen3 coding model"])}
    result = check_evidence_backed_promotions_verified(baseline, current)
    assert result["ok"] is True, result["unbacked"]


def test_evidence_backed_fails_when_only_unverified_url_for_moderate():
    from llm_discovery.audit_harness import check_evidence_backed_promotions_verified

    # example.com not allowlisted -> should not count as verified, so unbacked
    baseline = {"m1": _rec("m1", "weak", benchmarks=_bench({}), evidence=[])}
    current = {"m1": _rec("m1", "moderate", benchmarks=_bench({}), evidence=["https://example.com/swe"])}
    result = check_evidence_backed_promotions_verified(baseline, current)
    assert result["ok"] is False
    assert len(result["unbacked"]) == 1


def test_evidence_backed_fails_when_strong_with_only_verified_url_no_bench_aa():
    from llm_discovery.audit_harness import check_evidence_backed_promotions_verified

    # strong requires AA/bench, URL alone insufficient
    baseline = {"m2": _rec("m2", "weak", benchmarks=_bench({}), evidence=[])}
    current = {"m2": _rec("m2", "strong", benchmarks=_bench({}), evidence=["https://qwen.ai/blog/qwen3"])}
    result = check_evidence_backed_promotions_verified(baseline, current)
    assert result["ok"] is False


def test_evidence_backed_fails_when_no_new_evidence():
    from llm_discovery.audit_harness import check_evidence_backed_promotions_verified

    baseline = {"model-y": _rec("model-y", "weak", benchmarks=_bench({}))}
    current = {"model-y": _rec("model-y", "strong", benchmarks=_bench({}))}
    result = check_evidence_backed_promotions_verified(baseline, current)
    assert result["ok"] is False
    assert result["unbacked"][0]["model_id"] == "model-y"


def test_evidence_backed_respects_canonical_identity():
    from llm_discovery.audit_harness import check_evidence_backed_promotions_verified

    baseline = {"glm-5.3": _rec("glm-5.3", "weak", benchmarks=_bench({}))}
    current = {"glm-5-3": _rec("glm-5-3", "strong", benchmarks=_bench({"swe_bench_verified": {"score": 55}}))}
    assert check_evidence_backed_promotions_verified(baseline, current)["ok"] is True


# ---------------------------------------------------------------------------
# Gate integration (CI-enforceable)
# ---------------------------------------------------------------------------

def test_gate_passes_when_evidence_backed_and_thresholds_frozen():
    from llm_discovery.audit_harness import gate

    baseline_metrics = {
        "totals": {"keep": 100, "uncertain": 600, "drop": 400, "error": 40},
        "evidence_levels": {"strong": 80, "moderate": 20, "weak": 500, "none": 140},
        "llm_calls": 100, "web_searches": 50, "wall_duration_s": 200.0,
    }
    current_metrics = {
        "totals": {"keep": 110, "uncertain": 580, "drop": 400, "error": 30},
        "evidence_levels": {"strong": 85, "moderate": 25, "weak": 480, "none": 130},
        "llm_calls": 90, "web_searches": 40, "wall_duration_s": 210.0,
    }
    baseline_recs = {"m1": _rec("m1", "weak", benchmarks=_bench({}))}
    current_recs = {"m1": _rec("m1", "moderate", aa_score=30, benchmarks=_bench({}))}
    result = gate(baseline_metrics, current_metrics, baseline_recs, current_recs)
    assert result["ok"] is True


def test_gate_fails_when_unbacked_promotion():
    from llm_discovery.audit_harness import gate

    baseline_metrics = {
        "totals": {"keep": 100, "uncertain": 600, "drop": 400, "error": 40},
        "evidence_levels": {"strong": 80, "moderate": 20, "weak": 500, "none": 140},
        "llm_calls": 100, "web_searches": 50, "wall_duration_s": 200.0,
    }
    current_metrics = {
        "totals": {"keep": 150, "uncertain": 500, "drop": 400, "error": 40},
        "evidence_levels": {"strong": 120, "moderate": 30, "weak": 400, "none": 140},
        "llm_calls": 100, "web_searches": 50, "wall_duration_s": 200.0,
    }
    baseline_recs = {"m1": _rec("m1", "weak", benchmarks=_bench({}))}
    current_recs = {"m1": _rec("m1", "strong", benchmarks=_bench({}))}
    result = gate(baseline_metrics, current_metrics, baseline_recs, current_recs)
    assert result["ok"] is False
    assert any("unbacked" in f.lower() for f in result["failures"])


def test_gate_fails_when_thresholds_loosened(monkeypatch):
    import llm_discovery.benchmarks as bm
    from llm_discovery.audit_harness import gate

    monkeypatch.setattr(bm, "MIN_SCORE", 15.0)
    baseline_metrics = {
        "totals": {"keep": 100, "uncertain": 600, "drop": 400, "error": 40},
        "evidence_levels": {"strong": 80, "moderate": 20, "weak": 500, "none": 140},
        "llm_calls": 100, "web_searches": 50, "wall_duration_s": 200.0,
    }
    result = gate(baseline_metrics, baseline_metrics, {}, {})
    assert result["ok"] is False
    assert any("threshold" in f.lower() or "MIN_SCORE" in f for f in result["failures"])


def test_gate_fails_when_snapshot_mismatch():
    from llm_discovery.audit_harness import gate

    baseline_metrics = {
        "totals": {"keep": 100, "uncertain": 600, "drop": 400, "error": 40},
        "evidence_levels": {"strong": 80, "moderate": 20, "weak": 500, "none": 140},
        "llm_calls": 100, "web_searches": 50, "wall_duration_s": 200.0,
    }
    fp_a = {
        "artificial_analysis_models.json": {"exists": True, "sha256": "aaaa"},
        "models_dev_catalog.json": {"exists": True, "sha256": "bbbb"},
        "benchmarks.json": {"exists": False},
    }
    fp_b = {
        "artificial_analysis_models.json": {"exists": True, "sha256": "cccc"},
        "models_dev_catalog.json": {"exists": True, "sha256": "bbbb"},
        "benchmarks.json": {"exists": False},
    }
    result = gate(baseline_metrics, baseline_metrics, {}, {}, baseline_fingerprint=fp_a, current_fingerprint=fp_b)
    assert result["ok"] is False
    assert any("snapshot" in f.lower() for f in result["failures"])


def test_fixed_snapshot_harness_runs_same_snapshot():
    from llm_discovery.audit_harness import run_fixed_snapshot_audit_harness

    snapshot = {"prov_a": [{"id": "glm-5.3"}, {"id": "ghost-1"}]}

    def baseline_fn(provider, model_id, aa, md, cache):
        return _rec(model_id, "weak", benchmarks=_bench({}))

    def improved_fn(provider, model_id, aa, md, cache):
        if model_id == "glm-5.3":
            return _rec(model_id, "strong", benchmarks=_bench({"swe_bench_verified": {"score": 55}}))
        return _rec(model_id, "weak", benchmarks=_bench({}))

    result = run_fixed_snapshot_audit_harness(snapshot, baseline_fn, improved_fn)
    assert "baseline" in result and "current" in result and "deltas" in result
    assert result["gate"]["ok"] is True
    # deltas for keep/uncertain etc
    assert "keep" in result["deltas"]["totals"]
    assert "uncertain" in result["deltas"]["totals"]
    assert "error" in result["deltas"]["totals"]
    assert "weak" in result["deltas"]["evidence_levels"]
    assert "strong" in result["deltas"]["evidence_levels"]
    assert "llm_calls" in result["deltas"]
    assert "web_searches" in result["deltas"]
    assert "wall_duration_s" in result["deltas"]
