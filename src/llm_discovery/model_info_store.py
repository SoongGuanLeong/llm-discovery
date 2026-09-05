"""
Reusable cross-provider model-info store — schema & key normalization.

Decisions (issue #66, part of #63 map):
- Key decision, field inclusion, schema location, evidence gating, type shapes
- Consumes rules from #64 (evidence trust & merge) and #65 (pricing aggregation)
- Persistence/location finalized in #68; this module owns schema + normalization

See also:
- src/llm_discovery/results.py  (_normalize_model_id, _normalize_tier)
- src/llm_discovery/model_matching.py (normalize_model_id)
- ADR 0002 vendor alias, 0004 tier normalization
"""

from __future__ import annotations

import json
import os
import re
import statistics
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Key normalization
# ---------------------------------------------------------------------------

def _normalize_tier(tier: str | None) -> str | None:
    """Re-export of results._normalize_tier for store-layer tier handling."""
    if tier == "contributor_special":
        return "contributor_free"
    return tier


def _normalize_model_id_stepfun(model_id: str) -> str:
    """Stepfun -> step prefix normalization (from results._normalize_model_id)."""
    if model_id.startswith("stepfun-"):
        return "step-" + model_id[len("stepfun-"): ]
    if model_id.startswith("stepfun/"):
        return "step/" + model_id[len("stepfun/"): ]
    return model_id


def normalize_store_key(model_id: str) -> str:
    """
    Normalize a provider model_id to a cross-provider store key.

    Spec (issue #66):
    - Input: raw provider model_id as seen in provider catalogs or
      ProviderBatchWriter records (e.g. "openai/gpt-4o:free",
      "MiniMax/MiniMax-M3:free", "stepfun/step-2.5-free",
      "muse-spark-1.2-contributor-free").
    - Output: lowercased, provider-prefix-stripped, free-stripped,
      stepfun-normalized, whitespace-trimmed key.
    - Cross-provider: provider prefix (before "/" or ":") removed so
      same model from different providers collapses.
    - Case: lowercased unconditionally ("GPT-4O" -> "gpt-4o").
    - Free markers: strips trailing ":free", "-free", "_free", "/free"
      case-insensitive, one occurrence. Handled before and after
      stepfun mapping so "stepfun/step-1-free" normalizes correctly.
    - Stepfun: "stepfun-" / "stepfun/" -> "step-" / "step/" (results.py).
    - Vendor suffixes (-contributor, -next): NOT stripped at key level.
      Alias resolution (muse -contributor -> muse-spark-1-2, qwen -next
      -> qwen3-8-flash-next) lives in model_matching / resolver and is
      applied before store lookup when AA canonical is needed. Keeping
      them distinct in the store key avoids silent collisions between
      true variants; merge-time logic can coalesce them if evidence
      proves they are the same logical model (see ADR 0002). If caller
      wants alias-coalesced keys, call model_matching.normalize_model_id()
      first, then normalize_store_key().
    - No alias_map lookup at this layer; store key is deterministic
      from raw name alone. Whole-string lowercasing + provider strip is
      the full case handling.
    - Version dots vs hyphens are preserved as-is ("qwen3.8-flash" !=
      "qwen-3.8-flash" at key level). Callers that need dot/hyphen
      insensitivity should canonicalize via model_matching.normalize_model_id
      before calling this function, or use normalized_key_with_matcher().

    Examples:
        "openai/gpt-4o:free"          -> "gpt-4o"
        "MiniMax/MiniMax-M3"           -> "minimax-m3"
        "minimax-m3-free"              -> "minimax-m3"
        "stepfun/step-2.5-free"        -> "step-2.5"
        "STEPFUN-2.5_FREE"               -> "step-2.5"  (case + underscore free)
        "muse-spark-1.2-contributor"   -> "muse-spark-1.2-contributor" (suffix kept)
        "muse-spark-1.2-contributor-free" -> "muse-spark-1.2-contributor"
        "qwen3.8-flash"                -> "qwen3.8-flash" (dot kept)

    Stability: output is lowercased, provider-stripped, free-stripped slug
    suitable as dict key in data/model_info_store.json.
    """
    if not model_id:
        return ""
    raw = model_id.strip().lower()
    # Strip free suffix BEFORE provider split handles "minimax-m3/free" correctly
    raw = re.sub(r"[:/_-]free$", "", raw)
    # Stepfun normalization before provider strip handles "stepfun/step-2.5-free" uniformly
    raw = _normalize_model_id_stepfun(raw)
    # Re-strip free after stepfun (e.g. stepfun mapping may expose new suffix)
    raw = re.sub(r"[:/_-]free$", "", raw)
    # Strip provider prefix: take last segment after "/".
    # Also handle ":" provider separator (e.g. "openrouter:qwen:free" -> last after / then split :).
    # First, split on "/" and take last.
    slug = raw.rsplit("/", 1)[-1]
    # If slug still contains ":" (e.g. "gpt-4o:free"), handle free marker there.
    # Split on ":" for the free case: "gpt-4o:free" -> ["gpt-4o", "free"]
    # But preserve non-free colons? Only ":free" is expected; treat generically.
    if ":" in slug:
        # Reassemble without trailing :free segment; keep other colons as-is? Simpler: split.
        parts = slug.split(":")
        # If last part == "free", drop it.
        if parts[-1] == "free":
            slug = ":".join(parts[:-1])
        # else keep slug as-is (contains colon provider prefix variant)
    # Apply stepfun normalization again on slug (covers hyphen variant after split)
    slug = _normalize_model_id_stepfun(slug)
    # Strip free markers that use - _ / (-free, _free, /free) after slash handling.
    # Also handle :free already stripped; do again for variants like "-free", "_free".
    slug = re.sub(r"[:/_-]free$", "", slug)
    # After stepfun, a trailing "-free" may have appeared via slash->hyphen? Already handled.
    slug = slug.strip("-_./:")
    return slug


def normalized_key_with_matcher(model_id: str) -> str:
    """
    Alias-aware / dot-insensitive variant: canonicalize via
    model_matching.normalize_model_id before store-key rules.

    Use when caller wants "qwen3.8-flash" and "qwen-3.8-flash" to
    map the same key, or wants version-format folding. This is opt-in
    because store-layer keys default to stable raw normalization; this
    helper folds additional variants at caller request.
    """
    try:
        from .model_matching import normalize_model_id as _mm_normalize
    except Exception:
        return normalize_store_key(model_id)
    # Apply matcher normalization on the bare slug first, then store rules.
    slug = model_id.strip().rsplit("/", 1)[-1]
    canonical = _mm_normalize(slug)
    # _mm_normalize already lowercases and strips free, but stepfun not handled there
    canonical = _normalize_model_id_stepfun(canonical)
    # Re-apply store free strip for safety (matcher strips :free/-free already, but ensure)
    canonical = re.sub(r"[:/_-]free$", "", canonical)
    return canonical.strip("-_./:")

# ---------------------------------------------------------------------------
# Evidence gating & field inclusion matrix
# ---------------------------------------------------------------------------

EVIDENCE_LEVEL_ORDER: dict[str, int] = {
    "strong": 3,
    "moderate": 2,
    "weak": 1,
    "none": 0,
    "": 0,
}

CACHEABLE_LEVELS = {"strong"}

# Hallucinated evidence / UUID denylist per ADR 0006 §3 floors.
HALLUCINATED_DENYLIST = {"tokenmix.ai", "callsphere.ai", "benchlm"}

_UUID_RE = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.IGNORECASE)
_UUID_HEX32_RE = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)


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


def is_accurate_enough(record: dict[str, Any] | "ModelInfoRecord") -> tuple[bool, str]:
    """ADR 0006 Accurate-Enough Gate predicate.

    All floors must pass for Keeper eligibility. Returns (ok, reason).
    Reason is empty when ok, otherwise first failing floor.

    Floors:
    - evidence_level == strong
    - coding_score != null
    - pricing present OR free-marker exception (model_id contains free OR blended == 0)
    - aa_model_id present OR supplement bench >=50 with http URL
    - benchmark_coverage >=0.25
    - evidence contains at least one http URL
    - not UUID-shaped model_id
    - not hallucinated denylist in evidence
    """
    if isinstance(record, dict):
        d = record
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
            pricing_blended = pricing
    else:
        model_id = getattr(record, "model_id", "") or ""
        evidence_level = record.evidence_level
        coding_score = record.coding_score
        pricing = record.pricing
        aa_model_id = record.aa_model_id
        benchmarks = record.benchmarks.to_dict() if record.benchmarks else {}
        evidence = record.evidence or []
        benchmark_coverage = record.benchmarks.benchmark_coverage if record.benchmarks else None
        pricing_blended = pricing.blended if pricing else None

    if _normalize_evidence_level(evidence_level) != "strong":
        return False, f"evidence_level={evidence_level} not strong"
    if coding_score is None:
        return False, "coding_score is null"
    has_pricing = False
    if isinstance(pricing, dict):
        has_pricing = pricing_blended is not None or pricing.get("input") is not None or pricing.get("output") is not None
        if not pricing:
            has_pricing = False
    elif pricing is not None:
        has_pricing = True
    is_free = _is_free_model_id(model_id) or (pricing_blended == 0)
    if not has_pricing and not is_free:
        return False, "pricing missing and not free"
    has_aa = bool(aa_model_id)
    has_supp_50 = False
    scores_for_coverage = {}
    if isinstance(benchmarks, dict):
        scores = benchmarks.get("scores") or {}
        scores_for_coverage = scores
        for key in ("swe_bench_verified", "terminal_bench", "terminal_bench_2_1", "swe_bench_pro"):
            val = scores.get(key)
            sc = val.get("score") if isinstance(val, dict) else getattr(val, "score", None) if val is not None else None
            if sc is not None and sc >= 50:
                has_supp_50 = True
                break
    has_url = any("http" in str(e).lower() for e in (evidence or []))
    if not has_aa and not (has_supp_50 and has_url):
        return False, "aa_model_id missing and no supplement >=50 with URL"
    # Derive benchmark_coverage from scores when field absent (tests & legacy)
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
    if not has_url:
        return False, "evidence lacks http URL"
    if _is_uuid_model_id(model_id):
        return False, f"model_id is UUID {model_id}"
    if _is_hallucinated_evidence(evidence):
        return False, "hallucinated evidence denylist hit"
    return True, ""

# Field inclusion matrix by evidence_level (issue #66 output requirement).
# True = cached, False = not cached. Only strong is cacheable per ADR 0006.
# Weak/moderate/none rows are "do not cache" — gate is is_accurate_enough().
FIELD_INCLUSION_MATRIX: dict[str, dict[str, bool]] = {
    "strong": {
        "aa_model_id": True,
        "aa_score": True,
        "coding_score": True,
        "benchmarks": True,
        "evidence": True,
        "evidence_level": True,
        "confidence": True,
        "tier": True,
        "pricing": True,
        "_meta": True,
    },
    "moderate": {
        "aa_model_id": False,
        "aa_score": False,
        "coding_score": False,
        "benchmarks": False,
        "evidence": False,
        "evidence_level": False,
        "confidence": False,
        "tier": False,
        "pricing": False,
        "_meta": False,
    },
    "weak": {
        "aa_model_id": False,
        "aa_score": False,
        "coding_score": False,
        "benchmarks": False,
        "evidence": False,
        "evidence_level": False,
        "confidence": False,
        "tier": False,
        "pricing": False,
        "_meta": False,
    },
    "none": {
        "aa_model_id": False,
        "aa_score": False,
        "coding_score": False,
        "benchmarks": False,
        "evidence": False,
        "evidence_level": False,
        "confidence": False,
        "tier": False,
        "pricing": False,
        "_meta": False,
    },
}


def _normalize_evidence_level(level: str | None) -> str:
    if not level:
        return "none"
    lvl = level.strip().lower()
    if lvl in EVIDENCE_LEVEL_ORDER:
        return lvl
    return "none"


def should_cache(evidence_level: str | None, confidence: float | None = None) -> bool:
    """Evidence gating: cache only strong per ADR 0006.

    Null/weak/moderate/none -> do not cache. Confidence is not used for gating
    (only for tie-break during merge), but accepted for call-site symmetry.
    Strong-only Keeper; moderate remains Candidate until promoted.
    """
    lvl = _normalize_evidence_level(evidence_level)
    return lvl in CACHEABLE_LEVELS


def evidence_level_rank(level: str | None) -> int:
    """Numeric rank for ordering (higher = stronger). Used in per-field best-of merge."""
    return EVIDENCE_LEVEL_ORDER.get(_normalize_evidence_level(level), 0)

# ---------------------------------------------------------------------------
# Pricing aggregation (consumes #65 rules)
# ---------------------------------------------------------------------------

# Thresholds from #65 decision (blended/input/output separated):
PRICING_OUTLIER_BLEND_THRESHOLD = 0.20  # $/1M
PRICING_OUTLIER_IO_THRESHOLD = 0.15
PRICING_OUTLIER_RATIO = 0.50  # >50% from median


def is_pricing_outlier(candidate_blended: float, median_blended: float, candidate_io: float | None = None, median_io: float | None = None) -> bool:
    """Outlier if >50% from median AND > absolute $ threshold. Checked when n>=2.

    Uses blended + io thresholds per #65. io check only if both provided.
    """
    if median_blended == 0:
        return abs(candidate_blended) > PRICING_OUTLIER_BLEND_THRESHOLD
    ratio = abs(candidate_blended - median_blended) / abs(median_blended) if median_blended else 0
    if ratio > PRICING_OUTLIER_RATIO and abs(candidate_blended - median_blended) > PRICING_OUTLIER_BLEND_THRESHOLD:
        return True
    if candidate_io is not None and median_io is not None and median_io != 0:
        io_ratio = abs(candidate_io - median_io) / abs(median_io)
        if io_ratio > PRICING_OUTLIER_RATIO and abs(candidate_io - median_io) > PRICING_OUTLIER_IO_THRESHOLD:
            return True
    return False


def aggregate_pricing(observations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Aggregate pricing from multiple provider observations.

    - Each observation: {"blended": float, "input": float, "output": float, "provider": str}
      or raw AA shape with price_1m_blended_3_to_1 etc. This function normalizes.
    - Single observation: stored as-is (no outlier check).
    - n>=2: mean of non-outlier observations; outliers moved to per_provider_overrides.
    - Null pricing observations are ignored.
    - Returns None if no valid observations.
    - Output shape: {"blended": avg, "input": avg_in, "output": avg_out, "per_provider_overrides": {provider: {blended,input,output}}}
    """
    # Normalize observations to uniform shape
    normed: list[dict[str, Any]] = []
    for obs in observations:
        if not obs:
            continue
        blended = obs.get("blended", obs.get("price_1m_blended_3_to_1", obs.get("price_blended")))
        inp = obs.get("input", obs.get("price_1m_input_tokens"))
        out = obs.get("output", obs.get("price_1m_output_tokens"))
        if blended is None and inp is None and out is None:
            continue
        normed.append({
            "blended": blended,
            "input": inp,
            "output": out,
            "provider": obs.get("provider", obs.get("source_provider")),
        })
    if not normed:
        return None
    if len(normed) == 1:
        o = normed[0]
        return {
            "blended": o["blended"],
            "input": o["input"],
            "output": o["output"],
            "per_provider_overrides": {},
        }
    # n>=2: outlier detection on blended (primary) and io fallback
    blended_vals = [o["blended"] for o in normed if o["blended"] is not None]
    if not blended_vals:
        # no blended, fallback to input average
        return {
            "blended": None,
            "input": statistics.mean([o["input"] for o in normed if o["input"] is not None]) if any(o["input"] is not None for o in normed) else None,
            "output": statistics.mean([o["output"] for o in normed if o["output"] is not None]) if any(o["output"] is not None for o in normed) else None,
            "per_provider_overrides": {},
        }
    median_blended = statistics.median(blended_vals)
    # median io for check
    io_vals = []
    for o in normed:
        if o["input"] is not None and o["output"] is not None:
            io_vals.append((o["input"] + o["output"]) / 2)
    median_io = statistics.median(io_vals) if io_vals else None
    non_outliers: list[dict[str, Any]] = []
    outliers: dict[str, dict[str, Any]] = {}
    for o in normed:
        if o["blended"] is None:
            non_outliers.append(o)
            continue
        cand_io = None
        if o["input"] is not None and o["output"] is not None:
            cand_io = (o["input"] + o["output"]) / 2
        if is_pricing_outlier(o["blended"], median_blended, cand_io, median_io):
            key = o["provider"] or f"obs_{len(outliers)}"
            outliers[key] = {"blended": o["blended"], "input": o["input"], "output": o["output"]}
        else:
            non_outliers.append(o)
    # If all were outliers, keep all (degenerate)
    if not non_outliers:
        non_outliers = normed
        outliers = {}
    blended_avg = statistics.mean([o["blended"] for o in non_outliers if o["blended"] is not None]) if any(o["blended"] is not None for o in non_outliers) else None
    input_avg = statistics.mean([o["input"] for o in non_outliers if o["input"] is not None]) if any(o["input"] is not None for o in non_outliers) else None
    output_avg = statistics.mean([o["output"] for o in non_outliers if o["output"] is not None]) if any(o["output"] is not None for o in non_outliers) else None
    return {
        "blended": blended_avg,
        "input": input_avg,
        "output": output_avg,
        "per_provider_overrides": outliers,
    }

# ---------------------------------------------------------------------------
# Store schema
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkSnapshot:
    """Trimmed benchmark snapshot (matches BenchmarkProfile.to_dict trimmed)."""
    scores: dict[str, Any] = field(default_factory=dict)
    raw_benchmarks: list[Any] = field(default_factory=list)
    benchmark_coverage: float | None = None
    coverage_with_supplements: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"scores": self.scores, "raw_benchmarks": self.raw_benchmarks}
        if self.benchmark_coverage is not None:
            d["benchmark_coverage"] = self.benchmark_coverage
        if self.coverage_with_supplements is not None:
            d["coverage_with_supplements"] = self.coverage_with_supplements
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BenchmarkSnapshot":
        if not data:
            return cls()
        return cls(
            scores=data.get("scores", {}),
            raw_benchmarks=data.get("raw_benchmarks", []),
            benchmark_coverage=data.get("benchmark_coverage"),
            coverage_with_supplements=data.get("coverage_with_supplements"),
        )


@dataclass
class StoreMeta:
    """Freshness metadata per key — slim v2 (only first_seen, last_updated, version)."""

    first_seen: str | None = None
    last_updated: str | None = None
    version: int = 2
    # LegacyCompat: v1 fields kept for compat read but never written
    source_providers: list[str] = field(default_factory=list, repr=False, compare=False)
    source_evidence_levels: list[str] = field(default_factory=list, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        # Slim v2: only 3 keys
        return {
            "first_seen": self.first_seen,
            "last_updated": self.last_updated,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StoreMeta":
        if not data:
            return cls()
        # Compat: accept v1 dict with source_providers etc, ignore on write
        return cls(
            first_seen=data.get("first_seen"),
            last_updated=data.get("last_updated"),
            version=int(data.get("version", 2)),
            source_providers=list(data.get("source_providers", [])),
            source_evidence_levels=list(data.get("source_evidence_levels", [])),
        )


@dataclass
class PricingSnapshot:
    """Pricing shape per #65: avg blended/input/output + outlier overrides."""
    blended: float | None = None
    input: float | None = None
    output: float | None = None
    per_provider_overrides: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.blended is not None:
            d["blended"] = self.blended
        if self.input is not None:
            d["input"] = self.input
        if self.output is not None:
            d["output"] = self.output
        # Always include overrides key for explicitness (empty dict when no outliers)
        d["per_provider_overrides"] = self.per_provider_overrides
        # Compat aliases for AA raw keys
        if self.blended is not None:
            d["price_1m_blended_3_to_1"] = self.blended
        if self.input is not None:
            d["price_1m_input_tokens"] = self.input
        if self.output is not None:
            d["price_1m_output_tokens"] = self.output
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PricingSnapshot":
        if not data:
            return cls()
        blended = data.get("blended", data.get("price_1m_blended_3_to_1"))
        inp = data.get("input", data.get("price_1m_input_tokens"))
        out = data.get("output", data.get("price_1m_output_tokens"))
        return cls(
            blended=blended,
            input=inp,
            output=out,
            per_provider_overrides=dict(data.get("per_provider_overrides", data.get("overrides", {}))),
        )


@dataclass
class ModelInfoRecord:
    """
    Slim Source of Truth v2 record keyed by normalize_store_key(provider model_id).

    Durable shape (file): {benchmarks, pricing, _meta} only.
    _meta = {first_seen, last_updated, version:2}
    Dropped vs v1: aa_model_id, aa_score, coding_score, evidence, evidence_level,
    confidence, tier, _meta.source_providers/source_evidence_levels.
    Keys are normalize_store_key(raw provider model_id).
    Compat: from_dict reads v1 files (with dropped fields) but to_dict writes v2.
    """
    benchmarks: BenchmarkSnapshot | None = None
    pricing: PricingSnapshot | None = None
    _meta: StoreMeta = field(default_factory=StoreMeta)
    # LegacyCompat: v1 fields kept for compat read / gate checks but never persisted
    aa_model_id: str | None = field(default=None, repr=False, compare=False)
    aa_score: float | None = field(default=None, repr=False, compare=False)
    coding_score: float | None = field(default=None, repr=False, compare=False)
    evidence: list[str] = field(default_factory=list, repr=False, compare=False)
    evidence_level: str | None = field(default=None, repr=False, compare=False)
    confidence: float | None = field(default=None, repr=False, compare=False)
    tier: str | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        # Slim v2: only benchmarks, pricing, _meta
        return {
            "benchmarks": self.benchmarks.to_dict() if self.benchmarks else {"scores": {}, "raw_benchmarks": []},
            "pricing": self.pricing.to_dict() if self.pricing else {"per_provider_overrides": {}},
            "_meta": self._meta.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelInfoRecord":
        # Compat: accept both v1 (with dropped fields) and v2 slim
        if not isinstance(data, dict):
            data = {}
        return cls(
            benchmarks=BenchmarkSnapshot.from_dict(data.get("benchmarks")),
            pricing=PricingSnapshot.from_dict(data.get("pricing")),
            _meta=StoreMeta.from_dict(data.get("_meta")),
            aa_model_id=data.get("aa_model_id"),
            aa_score=data.get("aa_score"),
            coding_score=data.get("coding_score"),
            evidence=list(data.get("evidence", [])),
            evidence_level=data.get("evidence_level"),
            confidence=data.get("confidence"),
            tier=data.get("tier"),
        )

    @classmethod
    def from_provider_record(cls, rec: dict[str, Any], provider: str | None = None, evaluated_at: str | None = None) -> "ModelInfoRecord":
        """Build slim v2 from a ProviderBatchWriter._to_record-style dict."""
        bm = rec.get("benchmarks")
        if isinstance(bm, dict):
            bench = BenchmarkSnapshot(
                scores=dict(bm.get("scores", {})),
                raw_benchmarks=list(bm.get("raw_benchmarks", [])),
                benchmark_coverage=bm.get("benchmark_coverage"),
                coverage_with_supplements=bm.get("coverage_with_supplements"),
            )
        else:
            bench = BenchmarkSnapshot()
        pricing_raw = rec.get("pricing")
        pricing_snap: PricingSnapshot | None = None
        if isinstance(pricing_raw, dict):
            pricing_snap = PricingSnapshot(
                blended=pricing_raw.get("blended", pricing_raw.get("price_1m_blended_3_to_1")),
                input=pricing_raw.get("input", pricing_raw.get("price_1m_input_tokens")),
                output=pricing_raw.get("output", pricing_raw.get("price_1m_output_tokens")),
                per_provider_overrides=dict(pricing_raw.get("per_provider_overrides", {})),
            )
        elif pricing_raw is not None:
            try:
                pricing_snap = PricingSnapshot(blended=float(pricing_raw))
            except Exception:
                pricing_snap = None
        now = evaluated_at or datetime.now(UTC).isoformat()
        meta = StoreMeta(
            first_seen=now,
            last_updated=now,
            version=2,
        )
        # Keep legacy fields for in-memory gate/compat but they will not be persisted
        return cls(
            benchmarks=bench,
            pricing=pricing_snap,
            _meta=meta,
            aa_model_id=rec.get("aa_model_id"),
            aa_score=rec.get("aa_score"),
            coding_score=rec.get("coding_score"),
            evidence=list(rec.get("evidence", [])),
            evidence_level=rec.get("evidence_level"),
            confidence=rec.get("confidence"),
            tier=_normalize_tier(rec.get("tier", rec.get("category"))),
        )

# ---------------------------------------------------------------------------
# Merge rules (consumes #64 per-field best-of + gap-fill)
# ---------------------------------------------------------------------------

def _benchmark_union_max(existing: BenchmarkSnapshot | None, incoming: BenchmarkSnapshot | None) -> BenchmarkSnapshot:
    """Union benchmarks, keeping max per key (from #64)."""
    if not existing:
        return incoming or BenchmarkSnapshot()
    if not incoming:
        return existing
    merged_scores: dict[str, Any] = dict(existing.scores)
    for k, v in (incoming.scores or {}).items():
        if k not in merged_scores:
            merged_scores[k] = v
        else:
            # Compare numeric score, keep max
            try:
                ev = merged_scores[k]
                e_score = ev.get("score") if isinstance(ev, dict) else getattr(ev, "score", 0)
                i_score = v.get("score") if isinstance(v, dict) else getattr(v, "score", 0)
                if float(i_score) > float(e_score):
                    merged_scores[k] = v
            except Exception:
                # keep existing on error
                pass
    # raw_benchmarks: dedup by name+source? simple union dedup by json repr
    seen = {str(b) for b in (existing.raw_benchmarks or [])}
    merged_raw = list(existing.raw_benchmarks or [])
    for b in (incoming.raw_benchmarks or []):
        if str(b) not in seen:
            merged_raw.append(b)
            seen.add(str(b))
    # coverage: max
    bc = None
    if existing.benchmark_coverage is not None or incoming.benchmark_coverage is not None:
        vals = [v for v in [existing.benchmark_coverage, incoming.benchmark_coverage] if v is not None]
        bc = max(vals) if vals else None  # type: ignore
    cws = None
    if existing.coverage_with_supplements is not None or incoming.coverage_with_supplements is not None:
        vals = [v for v in [existing.coverage_with_supplements, incoming.coverage_with_supplements] if v is not None]
        cws = max(vals) if vals else None  # type: ignore
    return BenchmarkSnapshot(scores=merged_scores, raw_benchmarks=merged_raw, benchmark_coverage=bc, coverage_with_supplements=cws)  # type: ignore


def merge_records(existing: ModelInfoRecord | None, incoming: ModelInfoRecord) -> ModelInfoRecord:
    """
    Slim v2 merge: benchmarks gap-fill union (never overwrite), pricing re-aggregated,
    freshness min(first_seen)/max(last_updated). No scalar overwrite for dropped fields.
    Legacy fields gap-filled only in memory for compat (not persisted).
    """
    if existing is None:
        return incoming

    # Pricing: always re-aggregate (avg across observations, outlier to overrides)
    merged_pricing = None
    obs_list = []
    for snap in (existing.pricing, incoming.pricing):
        if snap:
            obs = snap.to_dict() if hasattr(snap, 'to_dict') else dict(snap)
            obs_list.append(obs)
    if len(obs_list) >= 2:
        try:
            agg = aggregate_pricing(obs_list)
            if agg:
                merged_pricing = PricingSnapshot(blended=agg.get('blended'), input=agg.get('input'), output=agg.get('output'), per_provider_overrides=agg.get('per_provider_overrides', {}))
            else:
                merged_pricing = existing.pricing
        except Exception:
            merged_pricing = existing.pricing or incoming.pricing
    elif len(obs_list) == 1:
        merged_pricing = existing.pricing or incoming.pricing
    else:
        merged_pricing = None

    # Slim freshness: first_seen = min, last_updated = max, version = 2
    first_seen_vals = [t for t in [existing._meta.first_seen, incoming._meta.first_seen] if t]
    last_vals = [t for t in [existing._meta.last_updated, incoming._meta.last_updated] if t]
    merged_meta = StoreMeta(
        first_seen=min(first_seen_vals) if first_seen_vals else (existing._meta.first_seen or incoming._meta.first_seen),
        last_updated=max(last_vals) if last_vals else (incoming._meta.last_updated or existing._meta.last_updated),
        version=2,
    )

    # Legacy gap-fill for compat (not persisted in v2 file)
    def _gap_fill(field_name: str):
        e_val = getattr(existing, field_name, None)
        i_val = getattr(incoming, field_name, None)
        if e_val is not None and e_val != "" and e_val != []:
            if isinstance(e_val, list) and len(e_val) == 0 and isinstance(i_val, list) and len(i_val) > 0:
                return i_val
            return e_val
        if i_val is not None:
            return i_val
        return e_val

    return ModelInfoRecord(
        benchmarks=_benchmark_union_max(existing.benchmarks, incoming.benchmarks),
        pricing=merged_pricing,
        _meta=merged_meta,
        aa_model_id=_gap_fill("aa_model_id"),
        aa_score=_gap_fill("aa_score"),
        coding_score=_gap_fill("coding_score"),
        evidence=_gap_fill("evidence"),
        evidence_level=_gap_fill("evidence_level"),
        confidence=_gap_fill("confidence"),
        tier=_gap_fill("tier"),
    )

# ---------------------------------------------------------------------------
# Schema helpers & example
# ---------------------------------------------------------------------------

STORE_SCHEMA_DOC = """
# data/model_info_store.json — committed snapshot (JSON, atomic write with .bak)
# Key: normalize_store_key(provider_model_id)  -> ModelInfoRecord
# Example (#66 output requirement):
{
  "muse-spark-1.2": {
    "aa_model_id": "muse-spark-1-2",
    "aa_score": 56.8,
    "coding_score": 58.3,
    "tier": "flash",
    "evidence_level": "strong",
    "confidence": 0.92,
    "evidence": ["AA Intelligence 56.8", "SWE-Bench Verified 80.5"],
    "benchmarks": {
      "benchmark_coverage": 0.5,
      "coverage_with_supplements": 0.25,
      "scores": {
        "aa_intelligence": {"score": 56.8, "metric": "index", "source": "artificial_analysis"},
        "swe_bench_verified": {"score": 80.5, "metric": "resolved", "source": "models_dev"}
      },
      "raw_benchmarks": []
    },
    "pricing": {
      "blended": 0.45,
      "input": 0.30,
      "output": 0.90,
      "price_1m_blended_3_to_1": 0.45,
      "price_1m_input_tokens": 0.30,
      "price_1m_output_tokens": 0.90,
      "per_provider_overrides": {"groq": {"blended": 0.90, "input": 0.60, "output": 1.80}}
    },
    "_meta": {
      "first_seen": "2026-09-04T05:00:00+00:00",
      "last_updated": "2026-09-04T06:00:00+00:00",
      "source_providers": ["groq", "openrouter"],
      "source_evidence_levels": ["strong", "moderate"],
      "version": 1
    }
  }
}
"""

EXAMPLE_YAML_SNIPPET = """
# YAML view of same record (for human snapshot / docs):
muse-spark-1.2:  # normalize_store_key("muse-spark-1.2-contributor-free")
  aa_model_id: muse-spark-1-2
  aa_score: 56.8
  coding_score: 58.3
  tier: flash           # _normalize_tier applied
  evidence_level: strong
  confidence: 0.92
  evidence:
    - "AA Intelligence 56.8"
    - "SWE-Bench Verified 80.5"
  benchmarks:
    scores:
      aa_intelligence: {score: 56.8, source: artificial_analysis}
      swe_bench_verified: {score: 80.5, source: models_dev}
    raw_benchmarks: []
  pricing:
    blended: 0.45
    input: 0.30
    output: 0.90
    per_provider_overrides:
      groq: {blended: 0.90, input: 0.60, output: 1.80}  # outlier excluded from avg
  _meta:
    first_seen: "2026-09-04T05:00:00+00:00"
    last_updated: "2026-09-04T06:00:00+00:00"
    source_providers: [groq, openrouter]
    source_evidence_levels: [strong, moderate]
"""

# Recommended persistence (decision #66):
RECOMMENDED_STORE_PATH = "data/model_info_store.json"
RECOMMENDED_STORE_FORMAT = "json"  # JSON over YAML (machine-managed, atomic)

# Backward compatibility: old candidates rejected with rationale
REJECTED_ALTERNATIVES = {
    "data/model_cache.yaml (gitignored)": "rejected — gitignored hides drift, no committed snapshot for audit; conflicts with #68 snapshot goal",
    "YAML committed": "rejected — YAML requires careful quoting for scores, harder atomic write than JSON, benchmarks.json precedent is JSON",
}

__all__ = [
    "normalize_store_key",
    "normalized_key_with_matcher",
    "should_cache",
    "evidence_level_rank",
    "is_pricing_outlier",
    "aggregate_pricing",
    "merge_records",
    "ModelInfoRecord",
    "BenchmarkSnapshot",
    "PricingSnapshot",
    "StoreMeta",
    "ModelInfoStore",
    "STORE_FILE_VERSION",
    "RECOMMENDED_STORE_PATH_OBJ",
    "EVIDENCE_LEVEL_ORDER",
    "CACHEABLE_LEVELS",
    "FIELD_INCLUSION_MATRIX",
    "RECOMMENDED_STORE_PATH",
    "STORE_SCHEMA_DOC",
    "EXAMPLE_YAML_SNIPPET",
]

# ---------------------------------------------------------------------------
# Persistence — issue #68
# ---------------------------------------------------------------------------
# Location: data/model_info_store.json (JSON, committed snapshot).
# Invalidation: never expire (TTL=None default). Optional is_stale() when
# caller passes ttl_days (e.g. 90) but not enforced by default — data hardly
# changes, new name = new record, stronger evidence merges via merge_records.
# Concurrency: file lock (fcntl.flock) + atomic write (tmp + os.replace).
# Versioning: STORE_FILE_VERSION at file level, StoreMeta.version per record.
# Read path: lazy load on first get(), in-memory dict, per-model lookup.

STORE_FILE_VERSION: int = 2
DEFAULT_TTL_DAYS: int = 14  # SCD1 freshness gate per #72 Q7
RECOMMENDED_STORE_PATH_OBJ: Path = Path(RECOMMENDED_STORE_PATH)

def is_stale(last_updated: str | None, ttl_days: int | None = None) -> bool:
    """Return True if record older than ttl_days. None ttl = never stale."""
    if ttl_days is None or ttl_days <= 0:
        return False
    if not last_updated:
        return False
    try:
        dt = datetime.fromisoformat(last_updated.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - dt).days
        return age_days > ttl_days
    except Exception:
        return False

def dumps_compact(payload: dict[str, Any]) -> str:
    """Return minified JSON (no whitespace) for token-cheap LLM reads. #72 Q3."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=False)

def _acquire_lock(fh) -> None:
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass

def _release_lock(fh) -> None:
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass

def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-store-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=False)
            fh.write("\n")
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except Exception:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass

class ModelInfoStore:
    """Persistence for cross-provider store (issue #68).

    File: data/model_info_store.json  {"version": 1, "models": {key: record_dict}}
    Committed snapshot (git exception), atomic write, fcntl lock.
    Lazy load: first get() loads file into memory; writes flush immediately.
    TTL: never expire by default; caller may pass ttl_days to treat stale as miss.
    Merge: put() merges via merge_records (strong > moderate, tie confidence/newer).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path: Path = Path(path) if path is not None else RECOMMENDED_STORE_PATH_OBJ
        self._data: dict[str, ModelInfoRecord] = {}
        self._loaded: bool = False
        self._file_version: int = STORE_FILE_VERSION

    # -- load / save --

    def load(self) -> None:
        if not self.path.exists():
            self._data = {}
            self._loaded = True
            self._file_version = STORE_FILE_VERSION
            return
        try:
            raw = json.loads(self.path.read_text())
        except Exception:
            self._data = {}
            self._loaded = True
            return
        # version header
        if isinstance(raw, dict) and "models" in raw:
            self._file_version = int(raw.get("version", STORE_FILE_VERSION))
            models_raw = raw.get("models", {})
        elif isinstance(raw, dict):
            # legacy bare dict without wrapper
            self._file_version = int(raw.get("_version", STORE_FILE_VERSION))
            models_raw = {k: v for k, v in raw.items() if not k.startswith("_")}
            if "_version" in raw:
                models_raw = raw.get("models", models_raw)
        else:
            models_raw = {}
        data: dict[str, ModelInfoRecord] = {}
        for k, v in (models_raw or {}).items():
            try:
                data[str(k)] = ModelInfoRecord.from_dict(v) if isinstance(v, dict) else v
            except Exception:
                continue
        self._data = data
        self._loaded = True

    def save(self) -> None:
        payload = {
            "version": STORE_FILE_VERSION,
            "models": {k: v.to_dict() for k, v in self._data.items()},
        }
        _atomic_write_json(self.path, payload)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    # -- read path --

    def get(self, provider_model_id: str) -> ModelInfoRecord | None:
        self._ensure_loaded()
        key = normalize_store_key(provider_model_id)
        if not key:
            return None
        return self._data.get(key)

    def lookup(self, provider_model_id: str) -> ModelInfoRecord | None:
        return self.get(provider_model_id)

    def get_by_key(self, store_key: str) -> ModelInfoRecord | None:
        self._ensure_loaded()
        return self._data.get(store_key)

    def contains(self, provider_model_id: str) -> bool:
        return self.get(provider_model_id) is not None

    def is_stale_record(self, provider_model_id: str, ttl_days: int | None = None) -> bool:
        rec = self.get(provider_model_id)
        if rec is None:
            return False
        return is_stale(rec._meta.last_updated, ttl_days)

    def get_if_fresh(self, provider_model_id: str, ttl_days: int | None = None) -> ModelInfoRecord | None:
        rec = self.get(provider_model_id)
        if rec is None:
            return None
        if is_stale(rec._meta.last_updated, ttl_days):
            return None
        return rec

    # -- write path --

    def put(self, store_key: str, record: ModelInfoRecord) -> None:
        self._ensure_loaded()
        if record.evidence_level is not None or record.coding_score is not None:
            if not should_cache(record.evidence_level, record.confidence):
                return
        # File-lock protected critical section to avoid lost updates in ThreadPool
        lock_fh = None
        try:
            try:
                import fcntl
                lock_path = self.path.parent / ".store.lock"
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_fh = open(lock_path, "w")
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            except Exception:
                lock_fh = None
            # Reload fresh inside lock
            try:
                if self.path.exists():
                    raw = json.loads(self.path.read_text())
                    if isinstance(raw, dict) and "models" in raw:
                        fresh = {}
                        for k, v in (raw.get("models", {}) or {}).items():
                            try:
                                fresh[str(k)] = ModelInfoRecord.from_dict(v) if isinstance(v, dict) else v
                            except Exception:
                                continue
                        for k, v in fresh.items():
                            if k not in self._data:
                                self._data[k] = v
                            else:
                                if k != store_key:
                                    try:
                                        disk_ts = v._meta.last_updated or ""
                                        mem_ts = self._data[k]._meta.last_updated or ""
                                        if disk_ts > mem_ts:
                                            self._data[k] = v
                                    except Exception:
                                        pass
            except Exception:
                pass
            existing = self._data.get(store_key)
            merged = merge_records(existing, record)
            self._data[store_key] = merged
            self.save()
        finally:
            if lock_fh is not None:
                try:
                    import fcntl
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                    lock_fh.close()
                except Exception:
                    try:
                        lock_fh.close()
                    except Exception:
                        pass

    def put_for_model(self, provider_model_id: str, record: ModelInfoRecord) -> None:
        key = normalize_store_key(provider_model_id)
        if not key:
            return
        self.put(key, record)

    def upsert_from_provider_record(self, provider_model_id: str, provider_record: dict[str, Any], provider: str | None = None, evaluated_at: str | None = None) -> bool:
        lvl = provider_record.get("evidence_level")
        if not should_cache(lvl, provider_record.get("confidence")):
            return False
        rec = ModelInfoRecord.from_provider_record(provider_record, provider=provider, evaluated_at=evaluated_at)
        self.put_for_model(provider_model_id, rec)
        return True

    def merge_from_dict(self, models_dict: dict[str, dict[str, Any]]) -> int:
        count = 0
        for k, v in (models_dict or {}).items():
            try:
                rec = ModelInfoRecord.from_dict(v) if isinstance(v, dict) else v
                if should_cache(rec.evidence_level, rec.confidence):
                    self.put(str(k), rec)
                    count += 1
            except Exception:
                continue
        return count

    def dumps_compact(self) -> str:
        """Minified JSON of store payload for LLM token-cheap reads. #72 Q3."""
        self._ensure_loaded()
        payload = {"version": STORE_FILE_VERSION, "models": {k: v.to_dict() for k, v in self._data.items()}}
        return dumps_compact(payload)

    def dumps_pretty(self) -> str:
        """Pretty JSON (on-disk format)."""
        self._ensure_loaded()
        payload = {"version": STORE_FILE_VERSION, "models": {k: v.to_dict() for k, v in self._data.items()}}
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

    # -- stats --

    def keys(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._data.keys())

    def size(self) -> int:
        self._ensure_loaded()
        return len(self._data)

    def __len__(self) -> int:
        return self.size()

    def __contains__(self, provider_model_id: str) -> bool:
        return self.contains(provider_model_id)

    def clear(self) -> None:
        self._data = {}
        self._loaded = True
        self.save()

    def delete(self, store_key: str) -> bool:
        """Delete one normalized key. Returns True if removed."""
        self._ensure_loaded()
        if store_key in self._data:
            del self._data[store_key]
            self.save()
            return True
        return False

    def gc(self, live_keys: set[str], ttl_days: int | None = None) -> int:
        """GC stale keys absent from live set. Share-aware via union live_keys."""
        if ttl_days is None:
            ttl_days = DEFAULT_TTL_DAYS
        self._ensure_loaded()
        to_delete = [
            k for k, rec in list(self._data.items())
            if k not in live_keys and is_stale(rec._meta.last_updated, ttl_days)
        ]
        for k in to_delete:
            del self._data[k]
        if to_delete:
            self.save()
        return len(to_delete)

    def to_dict(self) -> dict[str, Any]:
        self._ensure_loaded()
        return {k: v.to_dict() for k, v in self._data.items()}