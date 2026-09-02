"""Policy gate seam — deterministic coding/tier policy.

Encapsulates the five-stage policy previously inlined in evaluate_model:
  - benchmark profile build + coding_score + weakness check
  - deterministic coding override
  - categorize_model (tier)
  - Python hard gate (coding/tier/unknown → final decision)

Pipeline coordinator calls:
    gate = PolicyGate(min_score, max_score, cache)
    result = gate.apply(llm_result, resolution, model_id, provider_name)

Keeps pipeline <30 lines and isolates policy bugs to this module.
"""
from typing import Any

from .benchmarks import build_benchmark_profile, compute_coding_score, has_critical_weakness
from .categorize import categorize_model


def _is_router_model(model_id: str) -> bool:
    """Router models (e.g. kilo-auto/free, openrouter/free) are always kept.

    Routers are meta-models that delegate to free candidates; they have no
    coding benchmarks but must appear in the keep list for routing.
    """
    lower = model_id.lower()
    # Exact router ids + generic router substring
    if lower in ("kilo-auto/free", "openrouter/free"):
        return True
    if "router" in lower:
        return True
    # kilo auto-routing pattern
    if "auto" in lower and "free" in lower:
        return True
    return False


def _aa_score(aa_model: dict[str, Any] | None) -> float | None:
    if aa_model is None:
        return None
    return aa_model.get("evaluations", {}).get("artificial_analysis_intelligence_index")


class PolicyGate:
    """Deterministic policy: LLM result + benchmarks + AA → final record."""

    def __init__(self, min_score: float, max_score: float, cache: Any = None):
        self.min_score = min_score
        self.max_score = max_score
        self.cache = cache

    def apply(
        self,
        llm_result: Any,
        resolution: Any,
        model_id: str,
        provider_name: str,
        profile: Any = None,
    ) -> dict[str, Any]:
        """Map LLM judge output + deterministic signals to final evaluation record.

        profile is optional dedup — when provided (from Judge), reuse instead
        of rebuilding.
        """
        # --- Benchmark profile (deterministic, dedup) ---
        if profile is None:
            profile = build_benchmark_profile(model_id, provider_name, self.cache)
        benchmarks_dict = profile.to_dict() if profile.scores else {}
        coding_score, score_confidence, score_reasons = (
            compute_coding_score(profile) if profile.scores else (None, 0.0, ["No benchmark data"])
        )
        has_weakness, weakness_reason = (
            has_critical_weakness(profile) if profile.scores else (False, None)
        )
        if profile.scores:
            print(
                f"  [evaluate] {model_id}: benchmarks={profile.available_benchmarks()}, "
                f"coding_score={coding_score}, confidence={score_confidence}"
            )

        # --- Deterministic AA fields from resolution ---
        aa_model = resolution.aa_model if resolution else None
        if aa_model is not None:
            aa_model_id = aa_model.get("id")
            aa_name = aa_model.get("name")
            aa_slug = aa_model.get("slug")
            verified_score = _aa_score(aa_model)
        else:
            aa_model_id = None
            aa_name = None
            aa_slug = None
            verified_score = None

        evaluation: dict[str, Any] = {
            "provider_model_id": model_id,
            "source": "llm",
            "coding": llm_result.coding,
            "canonical_name": llm_result.canonical_name,
            "aa_model_id": aa_model_id,
            "aa_name": aa_name,
            "aa_slug": aa_slug,
            "aa_score": verified_score,
            "coding_score": coding_score,
            "benchmarks": benchmarks_dict,
            "confidence": llm_result.confidence,
            "decision": llm_result.decision,
            "evidence_level": llm_result.evidence_level,
            "evidence": llm_result.evidence,
            "coding_assessment": llm_result.coding_assessment.model_dump()
            if llm_result.coding_assessment
            else None,
        }

        if has_weakness:
            print(f"  [evaluate] {model_id}: CRITICAL WEAKNESS - {weakness_reason}")
            evaluation["critical_weakness"] = weakness_reason

        # --- Deterministic coding override ---
        deterministic_coding = llm_result.coding
        deterministic_coding_reason = None
        if not deterministic_coding and profile.scores:
            if coding_score is not None and coding_score >= 35.0:
                deterministic_coding = True
                deterministic_coding_reason = f"coding_score={coding_score:.1f} >= 35 (coding_min)"
            elif benchmarks_dict.get("swe_bench_verified", {}).get("score", 0) >= 50.0:
                sb_score = benchmarks_dict["swe_bench_verified"]["score"]
                deterministic_coding = True
                deterministic_coding_reason = f"SWE-bench Verified={sb_score:.1f}% >= 50%"
            elif benchmarks_dict.get("terminal_bench", {}).get("score", 0) >= 50.0:
                tb_score = benchmarks_dict["terminal_bench"]["score"]
                deterministic_coding = True
                deterministic_coding_reason = f"Terminal-Bench={tb_score:.1f}% >= 50%"
            elif benchmarks_dict.get("terminal_bench_2_1", {}).get("score", 0) >= 50.0:
                tb_score = benchmarks_dict["terminal_bench_2_1"]["score"]
                deterministic_coding = True
                deterministic_coding_reason = f"Terminal-Bench 2.1={tb_score:.1f}% >= 50%"

        if deterministic_coding != llm_result.coding and deterministic_coding_reason:
            print(
                f"  [evaluate] {model_id}: OVERRIDE LLM non-coding -> coding "
                f"(deterministic: {deterministic_coding_reason})"
            )
            evaluation["evidence"] = evaluation.get("evidence", []) + [
                f"Deterministic override: {deterministic_coding_reason}"
            ]

        tier = categorize_model(
            coding=deterministic_coding,
            aa_score=verified_score,
            min_score=self.min_score,
            max_score=self.max_score,
            judge_decision=llm_result.decision,
            model_id=model_id,
            coding_score=coding_score if profile.scores else None,
            has_critical_weakness=has_weakness,
        )
        evaluation["tier"] = tier

        # --- Router override: always keep regardless of coding/tier ---
        if _is_router_model(model_id):
            # Router keep overrides any drop; preserve max if already max, else flash
            if tier != "max":
                tier = "flash"
            evaluation["tier"] = tier
            evaluation["decision"] = "keep"
            evaluation["coding"] = True
            evaluation.setdefault("evidence", []).append("Router model: always keep (routing meta-model)")
            print(f"  [evaluate] {model_id}: ROUTER override -> KEEP {tier}")
            return evaluation

        # --- Python policy: map LLM decision to final decision ---
        if not deterministic_coding:
            evaluation["decision"] = "drop"
            evaluation["tier"] = "drop"
            evaluation.setdefault("evidence", []).append(
                "Model assessed as non-coding (LLM + deterministic); forced drop"
            )
        elif tier == "drop":
            evaluation["decision"] = "drop"
            evaluation.setdefault("evidence", []).append(
                "Tier assessment below minimum; forced drop"
            )
        elif deterministic_coding and not llm_result.coding:
            evaluation["decision"] = "keep"
            evaluation.setdefault("evidence", []).append(
                "Deterministic evidence overrides LLM assessment"
            )
        elif llm_result.decision == "error":
            evaluation["decision"] = "error"
        elif llm_result.decision == "keep":
            evaluation["decision"] = "keep"
        elif llm_result.decision == "drop":
            evaluation["decision"] = "drop"
        elif llm_result.decision == "unknown":
            evaluation["decision"] = "drop"
            evaluation["tier"] = "drop"
            evaluation.setdefault("evidence", []).append(
                "Insufficient evidence to determine coding quality; defaulted to drop"
            )
        else:
            evaluation["decision"] = "drop"

        print(
            f"  [evaluate] {model_id}: {evaluation['decision'].upper()} {tier} "
            f"(coding={llm_result.coding}, coding_score={coding_score}, "
            f"aa_score={verified_score}, evidence_level={llm_result.evidence_level})"
        )
        return evaluation

    def error_record(
        self, model_id: str, exc: Exception, provider_name: str, profile: Any = None
    ) -> dict[str, Any]:
        """Judge failure → decision=error with benchmark context."""
        if profile is None:
            profile = build_benchmark_profile(model_id, provider_name, self.cache)
        benchmarks_dict = profile.to_dict() if profile.scores else {}
        coding_score, _, _ = (
            compute_coding_score(profile) if profile.scores else (None, 0.0, [])
        )
        rec = {
            "provider_model_id": model_id,
            "source": "llm_error",
            "coding": False,
            "canonical_name": None,
            "aa_model_id": None,
            "aa_name": None,
            "aa_slug": None,
            "aa_score": None,
            "coding_score": coding_score,
            "benchmarks": benchmarks_dict,
            "confidence": 0.0,
            "decision": "error",
            "tier": "error",
            "evidence_level": "none",
            "evidence": [f"LLM evaluation failed: {exc}"],
            "coding_assessment": None,
        }
        return rec
