"""Search-budget A/B measurement helpers (issue #216).

Pure, deterministic helpers for the 20-model weak-sample 3-vs-5
measurement: sample selection, per-model metric extraction, arm
aggregation, and the adopt/keep decision rule.

This module is self-contained: no network access and no LLM calls.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Any

# Level ordering: none < weak < moderate < strong (mirrors PolicyGate._max_evidence_level)
LEVEL_ORDER = {"none": 0, "weak": 1, "moderate": 2, "strong": 3}

# Specialized id patterns (mirror EvidenceCollector pre-filter) — excluded
# from the weak sample: web search must not be expected to promote these.
SPECIALIZED_ID_PATTERNS = (
    "tts", "text-to-speech", "speech-to-text", "whisper", "speech",
    "safety", "guard", "moderation",
    "embedding", "embed", "rerank", "reranker",
    "vision", "audio", "voice", "llava", "flux", "diffusion",
)

_URL_RE = re.compile(r"https?://[^\s\)]+")  # same family as verified_claim guard


def _is_router(model_id: str) -> bool:
    """Router meta-models are force-strong; never part of a weak sample."""
    try:
        from .policy_gate import _is_router_model
        return _is_router_model(model_id)
    except Exception:
        lower = model_id.lower()
        return "router" in lower or ("auto" in lower and "free" in lower)


def _is_specialized_id(model_id: str) -> bool:
    lower = model_id.lower()
    return any(p in lower for p in SPECIALIZED_ID_PATTERNS)


def select_weak_sample(
    records: list[dict[str, Any]],
    n: int = 20,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Deterministically pick *n* weak/none records for the A/B sample.

    records: list of dicts with at least "provider" and "model_id"
    (optionally "evidence_level"). Routers and specialized ids are
    excluded; duplicates on (provider, model_id) are dropped. A stable
    sort plus seeded shuffle makes the sample reproducible.
    """
    seen: set[tuple[str, str]] = set()
    pool: list[dict[str, Any]] = []
    for rec in records:
        model_id = str(rec.get("model_id", "")).strip()
        provider = str(rec.get("provider", "")).strip()
        level = str(rec.get("evidence_level", "weak")).strip().lower()
        if level not in ("weak", "none"):
            continue
        if not model_id or _is_router(model_id) or _is_specialized_id(model_id):
            continue
        key = (provider, model_id)
        if key in seen:
            continue
        seen.add(key)
        pool.append(dict(rec))
    pool.sort(key=lambda r: (r.get("provider", ""), r.get("model_id", "")))
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool[:n]


def extract_urls(evidence: list[str] | None) -> list[str]:
    """Pull every http(s) URL out of evidence strings (same regex family as
    the hardened triangulation guard, issue #214)."""
    urls: list[str] = []
    for item in evidence or []:
        for url in _URL_RE.findall(str(item)):
            urls.append(url.rstrip('.,;'))
    return urls


def count_hallucinated_urls(evidence: list[str] | None) -> int:
    """Count cited URLs whose domain is NOT in the first-party allowlist.

    An allowlisted URL is legitimate evidence; a non-allowlisted cited URL
    is a hallucination candidate (the triangulation guard demotes it).
    """
    try:
        from .verified_claim import is_allowlisted_url
    except Exception:
        return 0
    return sum(1 for url in extract_urls(evidence) if not is_allowlisted_url(url))


def was_promoted(old_level: str | None, new_level: str | None) -> bool:
    """Strict improvement on the none < weak < moderate < strong scale."""
    return LEVEL_ORDER.get(new_level or "none", 0) > LEVEL_ORDER.get(old_level or "none", 0)


def has_guard_demotion(evidence: list[str] | None) -> bool:
    """True when the triangulation guard fired on this record."""
    for item in evidence or []:
        s = str(item)
        if "TRIANGULATION GUARD" in s or "demoted to weak (triangulation" in s:
            return True
    return False


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile (0-100). Empty input -> 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, round(p / 100.0 * len(ordered)))
    return ordered[rank - 1]


@dataclass
class ArmMetrics:
    """Aggregated 8-metric table for one arm (max_results=3 or 5)."""
    arm: str
    sample_n: int
    promotions: int = 0
    hallucinations: int = 0
    latency_p50_s: float = 0.0
    latency_p95_s: float = 0.0
    errors: int = 0
    evidence_urls_total: int = 0
    evidence_urls_unique: int = 0
    guard_demotions: int = 0
    wall_time_s: float = 0.0
    cost_usd: float = 0.0
    cost_note: str = ""

    @property
    def promotion_rate(self) -> float:
        return self.promotions / self.sample_n if self.sample_n else 0.0


def aggregate_arm(
    arm: str,
    rows: list[dict[str, Any]],
    wall_time_s: float,
    cost_note: str = "",
    cost_usd: float = 0.0,
) -> ArmMetrics:
    """Roll per-model rows up into the 8-metric arm summary.

    row keys: baseline_level, new_level, promoted (bool), error (bool),
    latency_s, evidence (list[str]), hallucinated_urls (int).
    """
    m = ArmMetrics(arm=arm, sample_n=len(rows), wall_time_s=wall_time_s,
                   cost_usd=cost_usd, cost_note=cost_note)
    all_urls: set[str] = set()
    latencies: list[float] = []
    for row in rows:
        latencies.append(float(row.get("latency_s", 0.0)))
        if row.get("error"):
            m.errors += 1
        if row.get("promoted"):
            m.promotions += 1
        m.hallucinations += int(row.get("hallucinated_urls", 0))
        if row.get("guard_demotion"):
            m.guard_demotions += 1
        urls = row.get("urls") or []
        m.evidence_urls_total += len(urls)
        all_urls.update(urls)
    m.evidence_urls_unique = len(all_urls)
    m.latency_p50_s = percentile(latencies, 50)
    m.latency_p95_s = percentile(latencies, 95)
    return m


def evaluate_decision(
    a: ArmMetrics,
    b: ArmMetrics,
    min_promotion_rate: float = 0.10,
    max_p95_growth: float = 0.30,
) -> dict[str, Any]:
    """Issue #216 decision rule, applied to arm A (3) vs arm B (5).

    Adopt 5 only if ALL hold:
      1. B promotion rate >= min_promotion_rate (default 10%, i.e. >=2/20)
      2. B hallucination count <= B promotion count
      3. B p95 latency < A p95 + 30% (p95(B) <= 1.30 * p95(A))
    """
    checks = {
        "promotion_rate": {
            "value": b.promotion_rate,
            "threshold": min_promotion_rate,
            "pass": b.promotion_rate >= min_promotion_rate,
        },
        "hallucination_lte_promotion": {
            "hallucinations": b.hallucinations,
            "promotions": b.promotions,
            "pass": b.hallucinations <= b.promotions,
        },
        "p95_growth": {
            "p95_a": a.latency_p95_s,
            "p95_b": b.latency_p95_s,
            "max_growth": max_p95_growth,
            "pass": b.latency_p95_s <= a.latency_p95_s * (1.0 + max_p95_growth)
                    if a.latency_p95_s > 0 else b.latency_p95_s <= 1.0 + max_p95_growth,
        },
    }
    adopt = all(c["pass"] for c in checks.values())
    reasons = [k for k, c in checks.items() if not c["pass"]]
    return {
        "adopt_search_max_results_5": adopt,
        "checks": checks,
        "failed_checks": reasons,
    }
