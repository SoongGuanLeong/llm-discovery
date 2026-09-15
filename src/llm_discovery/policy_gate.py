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

import re

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


def _version_tuple(v: str) -> tuple[int, ...]:
    """Parse version string like '2.5', '3.0', '2-5' into comparable tuple."""
    if not v:
        return ()
    # normalize hyphen to dot, split
    parts = re.split(r"[.\-]", v)
    out: list[int] = []
    for p in parts:
        # strip non-digit suffix (e.g., '3a' -> 3)
        m = re.match(r"(\d+)", p)
        if m:
            try:
                out.append(int(m.group(1)))
            except Exception:
                out.append(0)
        elif p:
            out.append(0)
    return tuple(out)


def _has_older_kept_sibling(model_id: str, store: Any | None) -> bool:
    """Check if store has an older version of same family/variant that is kept.

    Example: agnes-3.0-flash is newer than agnes-2.5-flash. If store has
    agnes-2.5-flash with decision keep, then agnes-3.0-flash qualifies.
    Uses base_without_numeric_version to handle version-in-variant like 2.5-flash.
    """
    if not store or not model_id:
        return False
    try:
        from .model_matching import ModelNormalizer
    except Exception:
        return False
    # numeric version pattern (without trailing variant)
    _num_ver_re = re.compile(r"\d+(?:[\.\-]\d+)+")
    def _numeric_part(v: str) -> str:
        m = _num_ver_re.search(v or "")
        return m.group(0) if m else ""
    def _base_without_version(mid: str) -> str:
        # normalized without numeric version -> family+variant base
        norm = ModelNormalizer.normalize(mid)
        base = _num_ver_re.sub("", norm)
        base = re.sub(r"-+", "-", base).strip("-")
        return base
    try:
        cur_sig = ModelNormalizer.extract_signature(model_id)
    except Exception:
        return False
    cur_num = _numeric_part(cur_sig.version)
    if not cur_num:
        # fallback: try to find numeric version directly in model_id
        cur_num = _numeric_part(model_id)
        if not cur_num:
            return False
    cur_ver = _version_tuple(cur_num)
    if not cur_ver:
        return False
    cur_base = _base_without_version(model_id)
    if not cur_base:
        return False
    # ensure store loaded
    try:
        if hasattr(store, "_ensure_loaded"):
            store._ensure_loaded()
        data = getattr(store, "_data", None)
        if data is None:
            return False
        # data is dict store_key -> ModelInfoRecord
        for _key, rec in list(data.items()):
            try:
                judge = getattr(rec, "judge", None)
                if judge is None and isinstance(rec, dict):
                    judge = rec.get("judge")
                decision = None
                if judge is not None:
                    decision = judge.get("decision") if isinstance(judge, dict) else getattr(judge, "decision", None)
                if decision is None:
                    decision = getattr(rec, "decision", None) if not isinstance(rec, dict) else rec.get("decision")
                if not decision or str(decision).strip().lower() != "keep":
                    continue
                sibling_id = _key  # normalized store key
                # base must match
                sib_base = _base_without_version(sibling_id)
                if sib_base != cur_base:
                    continue
                # extract sibling numeric version
                try:
                    sib_sig = ModelNormalizer.extract_signature(sibling_id)
                    sib_num = _numeric_part(sib_sig.version) or _numeric_part(sibling_id)
                except Exception:
                    sib_num = _numeric_part(sibling_id)
                if not sib_num:
                    continue
                sib_ver = _version_tuple(sib_num)
                if not sib_ver:
                    continue
                if sib_ver < cur_ver:
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


class PolicyGate:
    """Deterministic policy: LLM result + benchmarks + AA → final record."""

    def __init__(self, min_score: float, max_score: float, cache: Any = None, store: Any = None):
        self.min_score = min_score
        self.max_score = max_score
        self.cache = cache
        self.store = store

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

        # Pricing for report
        pricing = aa_model.get("pricing") if aa_model else None
        evaluation: dict[str, Any] = {
            "provider_model_id": model_id,
            "source": "llm",
            "coding": llm_result.coding,
            "canonical_name": llm_result.canonical_name,
            "judge_model": getattr(llm_result, "judge_model", None),
            "aa_model_id": aa_model_id,
            "aa_name": aa_name,
            "aa_slug": aa_slug,
            "aa_score": verified_score,
            "coding_score": coding_score,
            "pricing": pricing,
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

        # --- Hybrid deterministic evidence_level override (issue #35) ---
        # LLM is primary, but deterministic signals promote weak/moderate -> strong/moderate
        # when benchmarks/AA justify it. Never demote LLM strong.
        orig_level = evaluation.get("evidence_level")
        det_level = self._deterministic_evidence_level(
            verified_score, coding_score, profile
        )
        final_level = self._max_evidence_level(orig_level, det_level)
        if final_level != orig_level:
            print(f"  [evaluate] {model_id}: EVIDENCE_LEVEL PROMOTE {orig_level} -> {final_level} (deterministic: aa={verified_score}, coding_score={coding_score}, coverage={profile.benchmark_coverage():.2f}/{profile.coverage_with_supplements():.2f})" )
            evaluation["evidence_level"] = final_level
            evaluation.setdefault("evidence", []).append(
                f"Evidence level promoted {orig_level}→{final_level} via deterministic signals (aa={verified_score}, coding_score={coding_score})"
            )

        # --- Triangulation guard for no-AA / claim-only moderate without source URL (issue #39) ---
        # Never demote LLM strong; only demote unverified claim-only moderate -> weak to prevent hallucination.
        if evaluation.get("evidence_level") == "moderate" and det_level == "weak":
            # No AA and no benchmark scores means deterministic weak; verify URL exists
            if verified_score is None and not (profile.scores if profile else {}):
                has_url = any("http" in str(e) for e in evaluation.get("evidence", []))
                if not has_url:
                    # Only demote if LLM claimed moderate without verification
                    if orig_level == "moderate":
                        print(f"  [evaluate] {model_id}: TRIANGULATION GUARD moderate -> weak (no source URL for claim-only, aa=None, no benchmarks)")
                        evaluation["evidence_level"] = "weak"
                        evaluation.setdefault("evidence", []).append(
                            "Unverified claim-only moderate without source URL demoted to weak (triangulation requires first-party URL)"
                        )

        # Pricing from AA model (blended $/1M)
        pricing_blended = None
        if aa_model is not None:
            pricing_blended = aa_model.get("pricing", {}).get("price_1m_blended_3_to_1")
            # Treat 0 as free (already handled in categorize)
        # Sibling heuristic: if no aa_score/coding_score but older version kept, treat as keep
        has_sibling = False
        try:
            # prefer explicit store passed to gate, else try cache if it looks like store
            _store = getattr(self, "store", None)
            # fallback: if cache is actually a store (duck typing)
            if _store is None and self.cache is not None and hasattr(self.cache, "_data"):
                _store = self.cache
            if verified_score is None and (coding_score is None or not profile.scores):
                has_sibling = _has_older_kept_sibling(model_id, _store)
                if has_sibling:
                    print(f"  [evaluate] {model_id}: SIBLING heuristic -> older kept sibling found, treating as flash")
        except Exception:
            has_sibling = False
        tier = categorize_model(
            coding=deterministic_coding,
            aa_score=verified_score,
            min_score=self.min_score,
            max_score=self.max_score,
            judge_decision=llm_result.decision,
            model_id=model_id,
            coding_score=coding_score if profile.scores else None,
            has_critical_weakness=has_weakness,
            pricing_blended=pricing_blended,
            has_older_kept_sibling=has_sibling,
        )
        evaluation["tier"] = tier
        if has_sibling and verified_score is None and (coding_score is None or not profile.scores):
            evaluation.setdefault("evidence", []).append(
                f"Sibling heuristic: no aa_score but older kept sibling exists for {model_id}, assuming newer version superior -> keep as {tier}"
            )
        # Include pricing influence in evidence for report
        if pricing_blended is not None:
            # Intelligence per dollar for report visibility
            intel = coding_score if coding_score is not None else (verified_score * 100/63 if verified_score else 0)
            denom = pricing_blended if pricing_blended > 0 else 0.05
            denom = denom + 0.05
            value = intel / denom if intel else 0
            evaluation.setdefault("evidence", []).append(f"Pricing blended ${pricing_blended:.2f}/1M, intelligence per dollar {value:.1f} influenced tier={tier}")

        # --- Router override: always keep + flash regardless of coding/tier ---
        if _is_router_model(model_id):
            evaluation["tier"] = "flash"
            evaluation["decision"] = "keep"
            evaluation["coding"] = True
            evaluation.setdefault("evidence", []).append("Router model: always keep (routing meta-model)")
            print(f"  [evaluate] {model_id}: ROUTER override -> KEEP flash")
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
            f"aa_score={verified_score}, evidence_level={evaluation.get('evidence_level')})"
        )
        return evaluation

    @staticmethod
    def _deterministic_evidence_level(verified_score, coding_score, profile) -> str:
        """Compute deterministic evidence level from AA + coding_score + coverage.

        Hybrid promotion: LLM weak/moderate promoted when deterministic signals justify.
        Never demotes LLM strong. Thresholds align with categorize min 24 / max 45.
        """
        # Strong: frontier AA alone (>=55) or AA+benchmark combo or high coding_score
        if coding_score is not None and coding_score >= 45:
            return "strong"
        if verified_score is not None and verified_score >= 55:
            return "strong"
        if profile is not None:
            bc = profile.benchmark_coverage()
            cs = profile.coverage_with_supplements()
            scores = profile.scores or {}
            if verified_score is not None and verified_score >= 45 and bc >= 0.25:
                return "strong"
            if verified_score is not None and verified_score >= 50 and cs >= 0.08:
                return "strong"
            # SWE/Terminal direct thresholds
            for key in ("swe_bench_verified", "terminal_bench", "terminal_bench_2_1", "swe_bench_pro"):
                val = scores.get(key)
                sc = val.get("score") if isinstance(val, dict) else getattr(val, "score", None)
                if sc is not None and sc >= 50:
                    return "strong"
            # Moderate: AA in flash band or meaningful coding signal
            if verified_score is not None and verified_score >= 24:
                return "moderate"
            if coding_score is not None and coding_score >= 20:
                return "moderate"
            # Any positive supplement with reasonable score (>=30) counts as moderate
            # Avoid promoting low aider 11.1 -> weak
            for key, val in scores.items():
                sc = val.get("score") if isinstance(val, dict) else getattr(val, "score", None)
                if sc is not None and sc >= 30:
                    return "moderate"
        else:
            if verified_score is not None and verified_score >= 24:
                return "moderate"
        return "weak"

    @staticmethod
    def _max_evidence_level(a: str | None, b: str | None) -> str:
        order = {"none": 0, "weak": 1, "moderate": 2, "strong": 3}
        # normalize None -> weak
        a = a or "weak"
        b = b or "weak"
        return a if order.get(a, 1) >= order.get(b, 1) else b

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