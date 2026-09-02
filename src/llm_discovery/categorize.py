"""T1 tiering: max / flash / drop / uncertain / error bands.

Pure logic — no secrets, no network. The pipeline classifies a model once the
LLM judge has rendered its keep/drop/error decision and the Python hard gate
applied.

Bands (locked by T1, issue #1):
    max     = strategic reserve (better quality + performance token)
    flash   = cost effective general coding
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
) -> str:
    """Return 'max' | 'flash' | 'drop' | 'uncertain' | 'error' per T1 bands.

    When *judge_decision* is "error" the model is surfaced as "error"
    so it can be retried or reviewed -- it is **not** silently dropped.
    """
    if judge_decision == "error":
        return "error"
    if not coding:
        return "drop"
    
    if has_critical_weakness:
        return "drop"
    
    if model_id:
        model_lower = model_id.lower()
        flagship_patterns = ("pro", "ultra", "super", "opus", "flagship", "max", "premium")
        # Split on non-alphanumeric to get discrete tokens; match only exact tokens.
        # This prevents "max" from matching inside "minimax" or similar substrings.
        tokens = set(re.split(r"[^a-z0-9]+", model_lower))
        tokens = {t for t in tokens if t}
        if any(p in tokens for p in flagship_patterns):
            return "max"
    
    # If we have coding_score, use it as primary signal
    if coding_score is not None:
        if coding_score < coding_min_score:
            return "drop"
        if coding_max_score is not None and coding_score >= coding_max_score:
            return "max"
        # coding_score in flash band [coding_min_score, coding_max_score)
        # Check if aa_score also supports max
        if aa_score is not None and max_score is not None and aa_score >= max_score:
            return "max"
        return "flash"
    
    # No coding_score: fall back to aa_score if available
    if aa_score is not None:
        if aa_score < min_score:
            return "drop"
        if max_score is not None and aa_score >= max_score:
            return "max"
        return "flash"
    
    # No benchmark data at all: uncertain
    return "uncertain"