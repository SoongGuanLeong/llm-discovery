"""T1 tiering: max / flash / drop / uncertain / error bands.

Pure logic — no secrets, no network. The pipeline classifies a model once the
LLM judge has rendered its keep/drop/error decision and the Python hard gate
applied.

Bands (locked by T1, issue #1):
    max     = strategic reserve (better quality + performance token)
    flash   = cost effective general coding
    contributor_special = contributor models (models with 'contributor' in name)
    drop    = not coding OR critical weakness OR strong negative evidence
    uncertain = insufficient evidence to determine quality
    error   = judge evaluation failed (NOT a real drop — needs retry/review)

Tiering rules:
- max: coding + (coding_score >= coding_max_score
    OR aa_score >= max_score OR model_id indicates pro/flagship)
- flash: coding + (coding_score >= coding_min_score
    OR aa_score >= min_score OR no scores but kept)
- uncertain: coding but insufficient evidence (coding_score and aa_score both None)
- drop: not coding OR critical weakness OR (coding_score < coding_min_score)
- error: judge evaluation failed

*coding_score* is the multi-signal weighted score (on a 0-100 scale that
includes SWE-bench scores), while *aa_score* is the AA Intelligence Index
(compressed 1-63 range).  Separate thresholds apply to each so the wider
coding_score scale doesn't inflate tier assignments.
"""
import re
from typing import Optional

DEFAULT_MAX_THRESHOLD = 45.0

# Thresholds for coding_score (multi-signal weighted average, 0-100 scale
# mixing AA Intelligence, SWE-bench, LiveCodeBench, HumanEval)
DEFAULT_CODING_MAX_THRESHOLD = 65.0
DEFAULT_CODING_MIN_THRESHOLD = 35.0


def categorize_model(
    coding: bool,
    aa_score: Optional[float],
    *,
    min_score: float = 24.0,
    max_score: Optional[float] = DEFAULT_MAX_THRESHOLD,
    judge_decision: str = "keep",
    model_id: Optional[str] = None,
    coding_score: Optional[float] = None,
    has_critical_weakness: bool = False,
    coding_min_score: float = DEFAULT_CODING_MIN_THRESHOLD,
    coding_max_score: Optional[float] = DEFAULT_CODING_MAX_THRESHOLD,
    pricing: Optional[float] = None,
    pricing_blended: Optional[float] = None,
) -> str:
    """Return 'max' | 'flash' | 'contributor_special' | 'drop' | 'uncertain' | 'error' per T1 bands.

    When *judge_decision* is "error" the model is surfaced as "error"
    so it can be retried or reviewed -- it is **not** silently dropped.
    
    Pricing-aware: if pricing (blended $/1M) provided, intelligence per dollar
    influences max vs flash. Numerator is combined intelligence (coding_score
    preferred, else aa_score scaled). Denominator is price. Cheap high-quality
    models bias to flash even if raw score is max-range.
    Dots in version numbers are preserved (2.5 stays 2.5).
    """
    if judge_decision == "error":
        return "error"
    if not coding:
        return "drop"
    
    # Contributor special tier: models with 'contributor' in name
    if model_id and "contributor" in model_id.lower():
        return "contributor_special"
    
    if has_critical_weakness:
        return "drop"

    # Resolve effective price (blended preferred)
    eff_price = pricing_blended if pricing_blended is not None else pricing

    has_flagship = False
    if model_id:
        model_lower = model_id.lower()
        flagship_patterns = ("pro", "ultra", "super", "opus", "flagship", "max", "premium")
        tokens = set(re.split(r"[^a-z0-9]+", model_lower))
        tokens = {t for t in tokens if t}
        has_flagship = any(p in tokens for p in flagship_patterns)
    
    # Determine base tier from scores
    base_tier: Optional[str] = None
    if coding_score is not None:
        if coding_score < coding_min_score:
            return "drop"
        if coding_max_score is not None and coding_score >= coding_max_score:
            base_tier = "max"
        elif aa_score is not None and max_score is not None and aa_score >= max_score:
            base_tier = "max"
        else:
            base_tier = "flash"
    elif aa_score is not None:
        if aa_score < min_score:
            return "drop"
        if max_score is not None and aa_score >= max_score:
            base_tier = "max"
        else:
            base_tier = "flash"
    else:
        return "uncertain"

    # Flagship boost: if flagship and no strong price demotion yet, promote flash->max
    if has_flagship and base_tier == "flash":
        # Only promote if not ultra-cheap efficient
        if eff_price is None or eff_price > 0.5:
            base_tier = "max"

    # Pricing-aware adjustment: intelligence per dollar
    if eff_price is not None and base_tier is not None:
        # Combined intelligence: coding_score preferred, else aa_score scaled to 0-100
        if coding_score is not None:
            intelligence = coding_score
        elif aa_score is not None:
            # AA 63 ~= 100 coding scale
            intelligence = aa_score * (100.0 / 63.0)
        else:
            intelligence = 0

        # Avoid div-by-zero for free models: treat 0 price as 0.05
        denom = eff_price if eff_price > 0 else 0.05
        # Small epsilon to soften
        denom = denom + 0.05
        value = intelligence / denom  # intelligence per dollar

        # Cheap + efficient -> demote max to flash (STRICT: lower price, higher value)
        if base_tier == "max":
            if eff_price <= 0.2:
                return "flash"
            if eff_price <= 0.35 and value >= 85:
                return "flash"
            if eff_price <= 0.6 and value >= 130:
                return "flash"
        # Expensive but only flash-range quality -> keep flash (do not promote)
        # Optional promotion: very expensive + high value stays max already handled

    return base_tier if base_tier else "uncertain"