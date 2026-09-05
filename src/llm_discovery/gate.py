"""Accurate-Enough Gate — ADR 0006 §3 Keeper eligibility.

All 7 floors must pass for Keeper; fail => Candidate (re-evaluated every build).
Router keeps tagged separately per ADR 0006 (always keep but not coding Keeper).
Strong-only via should_cache; moderate/weak never cached.

Floors:
- evidence_level == strong
- coding_score != null
- pricing present OR free-marker (:free/-free/_free//free or blended==0)
- aa_model_id present OR supplement bench >=50 with http URL
- benchmark_coverage >=0.25 (KEY_SIGNALS: aa_intelligence, swe_bench_verified, livecodebench, humaneval)
- evidence contains http URL
- not UUID-shaped model_id and not hallucinated denylist (tokenmix.ai, callsphere.ai, benchlm)

Gate operates on provider keep record dicts (YAML or pipeline evaluation) before
store.put / merge_records. Store slim v2 holds only benchmarks+pricing+_meta;
gate fields are not persisted, so check must happen at source.
"""
from __future__ import annotations

import re
from typing import Any

HALLUCINATED_DENYLIST = {"tokenmix.ai", "callsphere.ai", "benchlm"}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.IGNORECASE)
_UUID_HEX32_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)

CACHEABLE_LEVELS = {"strong"}

SUPPLEMENT_50_KEYS = ("swe_bench_verified", "terminal_bench", "terminal_bench_2_1", "swe_bench_pro")


def _normalize_evidence_level(level: str | None) -> str:
    if not level:
        return ""
    return str(level).strip().lower()


def should_cache(evidence_level: str | None, confidence: float | None = None) -> bool:
    """Strong-only gate per ADR 0006. Moderate/weak/none never cached."""
    lvl = _normalize_evidence_level(evidence_level)
    return lvl in CACHEABLE_LEVELS


def _is_uuid_model_id(model_id: str | None) -> bool:
    if not model_id:
        return False
    mid = model_id.strip()
    return bool(_UUID_RE.match(mid) or _UUID_HEX32_RE.match(mid))


def _is_hallucinated_evidence(evidence: list[str] | None) -> bool:
    if not evidence:
        return False
    joined = " ".join(str(e) for e in evidence).lower()
    return any(d in joined for d in HALLUCINATED_DENYLIST)


def _is_free_model_id(model_id: str | None) -> bool:
    if not model_id:
        return False
    lower = model_id.strip().lower()
    return bool(re.search(r"(?:[:/_-]|^)free$", lower))


def _is_router_model_id(model_id: str | None) -> bool:
    if not model_id:
        return False
    lower = model_id.strip().lower()
    if lower in ("kilo-auto/free", "openrouter/free"):
        return True
    if "router" in lower:
        return True
    if "auto" in lower and "free" in lower:
        return True
    return False


def is_accurate_enough(record: dict[str, Any]) -> tuple[bool, str]:
    """ADR 0006 Accurate-Enough Gate predicate.

    Returns (ok, reason). Reason empty when ok, otherwise first failing floor.
    Operates on provider keep record dict (same shape as ProviderBatchWriter._to_record
    or PolicyGate.apply output).
    """
    d = record if isinstance(record, dict) else {}
    model_id = d.get("model_id") or d.get("provider_model_id") or ""
    evidence_level = d.get("evidence_level")
    coding_score = d.get("coding_score")
    pricing = d.get("pricing")
    aa_model_id = d.get("aa_model_id")
    benchmarks = d.get("benchmarks") or {}
    evidence = d.get("evidence") or []
    benchmark_coverage = d.get("benchmark_coverage")
    if benchmark_coverage is None and isinstance(benchmarks, dict):
        benchmark_coverage = benchmarks.get("benchmark_coverage")
    pricing_blended = None
    if isinstance(pricing, dict):
        pricing_blended = pricing.get("blended", pricing.get("price_1m_blended_3_to_1", pricing.get("price_blended")))
    elif pricing is not None:
        try:
            pricing_blended = float(pricing)  # scalar pricing
        except Exception:
            pricing_blended = pricing

    # Floor 1: evidence_level strong
    if _normalize_evidence_level(evidence_level) != "strong":
        return False, f"evidence_level={evidence_level} not strong"
    # Floor 2: coding_score != null
    if coding_score is None:
        return False, "coding_score is null"
    # Floor 3: pricing present OR free-marker
    has_pricing = False
    if isinstance(pricing, dict):
        # empty dict {} counts as missing
        if pricing:
            has_pricing = pricing_blended is not None or pricing.get("input") is not None or pricing.get("output") is not None
            # also handle priced dict with only per_provider_overrides -> missing blended
            # need at least blended/input/output
            if not pricing_blended and not pricing.get("input") and not pricing.get("output") and not pricing.get("price_1m_input_tokens"):
                # only per_provider_overrides present -> not sufficient
                has_pricing = pricing_blended is not None
        else:
            has_pricing = False
    elif pricing is not None:
        has_pricing = True
    is_free = _is_free_model_id(model_id) or (pricing_blended == 0)
    if not has_pricing and not is_free:
        return False, "pricing missing and not free"
    # Floor 4: aa_model_id or supplement >=50 with http URL
    has_aa = bool(aa_model_id)
    has_supp_50 = False
    scores_for_coverage: dict[str, Any] = {}
    if isinstance(benchmarks, dict):
        scores = benchmarks.get("scores") or {}
        scores_for_coverage = scores
        for key in SUPPLEMENT_50_KEYS:
            val = scores.get(key)
            sc = None
            if isinstance(val, dict):
                sc = val.get("score")
            elif val is not None:
                try:
                    sc = getattr(val, "score", None)
                except Exception:
                    sc = None
            if sc is not None:
                try:
                    if float(sc) >= 50:
                        has_supp_50 = True
                        break
                except Exception:
                    continue
    has_url = any("http" in str(e).lower() for e in (evidence or []))
    if not has_aa and not (has_supp_50 and has_url):
        return False, "aa_model_id missing and no supplement >=50 with URL"
    # Floor 5: benchmark_coverage >=0.25
    if benchmark_coverage is None and isinstance(benchmarks, dict):
        key_signals = {"aa_intelligence", "swe_bench_verified", "livecodebench", "humaneval"}
        if scores_for_coverage:
            present = len(key_signals.intersection(scores_for_coverage.keys()))
            benchmark_coverage = present / 4.0
    try:
        bc = float(benchmark_coverage) if benchmark_coverage is not None else None
    except Exception:
        bc = None
    if bc is None or bc < 0.25:
        return False, f"benchmark_coverage {benchmark_coverage} < 0.25"
    # Floor 6: evidence contains http URL
    if not has_url:
        return False, "evidence lacks http URL"
    # Floor 7: not UUID / hallucinated
    if _is_uuid_model_id(model_id):
        return False, f"model_id is UUID {model_id}"
    if _is_hallucinated_evidence(evidence):
        return False, "hallucinated evidence denylist hit"
    return True, ""
