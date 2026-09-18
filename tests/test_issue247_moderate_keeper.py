"""Issue #247: moderate Keeper predicate + Ephemeral Report rewrite spec.

Spec: moderate Keeper-eligible with guards (AA required, pricing present,
Benchmark Coverage >= 0.25). URL floor waived when aa_model_id present.
Writer rewrites decision to uncertain on demotion and stores gate_reason.
No per-model hardcode.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def _agnes_record(**over):
    rec = {
        "model_id": "agnes-3.0-flash",
        "provider_model_id": "agnes-3.0-flash",
        "decision": "keep",
        "tier": "flash",
        "evidence_level": "moderate",
        "confidence": 0.75,
        "coding_score": 35.5,
        "aa_model_id": "6ace0ead-584f-4ce6-a9b7-6c3f9cc91c3c",
        "aa_score": 35.5,
        "pricing": {
            "price_1m_blended_3_to_1": 0.075,
            "price_1m_input_tokens": 0.05,
            "price_1m_output_tokens": 0.15,
        },
        "benchmarks": {
            "benchmark_coverage": 0.25,
            "scores": {"aa_intelligence": {"score": 35.5}},
            "raw_benchmarks": [],
        },
        "benchmark_coverage": 0.25,
        "evidence": ["AA Intelligence score of 35.5 is above minimum."],
        "coding_assessment": None,
    }
    rec.update(over)
    return rec


def test_moderate_aa_backed_no_url_is_keeper():
    from llm_discovery.gate import is_accurate_enough

    ok, reason = is_accurate_enough(_agnes_record())
    assert ok is True, reason


def test_moderate_without_aa_stays_candidate():
    from llm_discovery.gate import is_accurate_enough

    rec = _agnes_record(aa_model_id=None, aa_score=None,
                        evidence=["https://example.com/swe"])
    ok, reason = is_accurate_enough(rec)
    assert ok is False
    assert "aa_model_id missing" in reason


def test_moderate_low_coverage_stays_candidate():
    from llm_discovery.gate import is_accurate_enough

    rec = _agnes_record(
        benchmark_coverage=0.0,
        benchmarks={
            "benchmark_coverage": 0.0,
            "scores": {},
            "raw_benchmarks": [],
        },
    )
    ok, reason = is_accurate_enough(rec)
    assert ok is False
    assert "benchmark_coverage" in reason


def test_moderate_pricing_null_stays_candidate():
    from llm_discovery.gate import is_accurate_enough

    rec = _agnes_record(pricing=None)
    ok, reason = is_accurate_enough(rec)
    assert ok is False
    assert "pricing" in reason


def test_moderate_coding_null_stays_candidate():
    from llm_discovery.gate import is_accurate_enough

    rec = _agnes_record(coding_score=None)
    ok, reason = is_accurate_enough(rec)
    assert ok is False
    assert "coding_score" in reason


def test_strong_supplement_url_path_still_keeper():
    from llm_discovery.gate import is_accurate_enough

    rec = _agnes_record(
        evidence_level="strong",
        aa_model_id=None,
        aa_score=None,
        benchmarks={
            "benchmark_coverage": 0.25,
            "scores": {"swe_bench_verified": {"score": 55}},
            "raw_benchmarks": [],
        },
        evidence=["bench result https://example.com/swe"],
    )
    ok, reason = is_accurate_enough(rec)
    assert ok is True, reason


def test_weak_never_keeper():
    from llm_discovery.gate import is_accurate_enough

    rec = _agnes_record(evidence_level="weak")
    ok, _ = is_accurate_enough(rec)
    assert ok is False


def test_writer_demotes_with_rewrite_and_reason(tmp_path: Path):
    from llm_discovery.results import ProviderBatchWriter

    bad = _agnes_record(model_id="no-aa-model", provider_model_id="no-aa-model",
                        aa_model_id=None, aa_score=None,
                        evidence=["no url here"])
    good = _agnes_record()
    out = tmp_path / "results"
    path = ProviderBatchWriter().write(
        {"keep": [good, bad], "uncertain": [], "drop": [], "error": []},
        "prov247",
        out,
    )
    payload = yaml.safe_load(Path(path).read_text())
    assert len(payload["keep"]) == 1
    assert payload["keep"][0]["model_id"] == "agnes-3.0-flash"
    assert "gate_reason" not in payload["keep"][0]
    assert len(payload["uncertain"]) == 1
    demoted = payload["uncertain"][0]
    assert demoted["decision"] == "uncertain"
    assert demoted.get("gate_reason", "") != ""


def test_writer_strips_stale_reason_on_repass(tmp_path: Path):
    from llm_discovery.results import ProviderBatchWriter

    rec = _agnes_record(gate_reason="stale reason")
    out = tmp_path / "results"
    path = ProviderBatchWriter().write(
        {"keep": [rec], "uncertain": [], "drop": [], "error": []},
        "prov247",
        out,
    )
    payload = yaml.safe_load(Path(path).read_text())
    assert len(payload["keep"]) == 1
    assert "gate_reason" not in payload["keep"][0]
