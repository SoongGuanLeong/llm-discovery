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

import re
import statistics
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
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

CACHEABLE_LEVELS = {"strong", "moderate"}

# Field inclusion matrix by evidence_level (issue #66 output requirement).
# True = cached, False = not cached. "weak"/"none" rows are "do not cache"
# per Decision from #64: cache gate strong/moderate only. Weak records are
# not inserted; if caller passes weak, should_cache() returns False.
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
    """Evidence gating: cache only strong/moderate per #64.

    Null/weak/none -> do not cache. Confidence is not used for gating
    (only for tie-break during merge), but accepted for call-site symmetry.
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
    """Provenance / freshness metadata per key."""
    first_seen: str | None = None
    last_updated: str | None = None
    source_providers: list[str] = field(default_factory=list)
    source_evidence_levels: list[str] = field(default_factory=list)
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "first_seen": self.first_seen,
            "last_updated": self.last_updated,
            "source_providers": self.source_providers,
            "source_evidence_levels": self.source_evidence_levels,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StoreMeta":
        if not data:
            return cls()
        return cls(
            first_seen=data.get("first_seen"),
            last_updated=data.get("last_updated"),
            source_providers=list(data.get("source_providers", [])),
            source_evidence_levels=list(data.get("source_evidence_levels", [])),
            version=int(data.get("version", 1)),
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
    Reusable store record keyed by normalized model_id.

    Shape matches ProviderBatchWriter._to_record trimmed to reusable fields:
    - Includes aa_model_id, aa_score, coding_score, benchmarks, evidence,
      evidence_level, confidence, tier, pricing
    - Omits per-provider decision/drop/error, evaluated_at (moved to _meta),
      stage, provider name (provenance tracked in _meta.source_providers)
    - _meta holds persistence-agnostic provenance (first_seen, last_updated,
      source_providers[], source_evidence_levels[]).

    Persistence location (per #66 decision): data/model_info_store.json
    (JSON, committed snapshot, atomic write with .bak). YAML considered but
    rejected: machine-managed store favors JSON for atomic reads/writes and
    consistency with data/benchmarks.json and catalogs. Location finalized
    in #68 but schema stable here.
    """
    # Core reusable fields (from _to_record, trimmed)
    aa_model_id: str | None = None
    aa_score: float | None = None
    coding_score: float | None = None
    benchmarks: BenchmarkSnapshot | None = None
    evidence: list[str] = field(default_factory=list)
    evidence_level: str | None = None  # strong | moderate | weak | none (cached only strong/moderate)
    confidence: float | None = None
    tier: str | None = None  # normalized via _normalize_tier
    pricing: PricingSnapshot | None = None
    # Provenance / freshness
    _meta: StoreMeta = field(default_factory=StoreMeta)

    def to_dict(self) -> dict[str, Any]:
        return {
            "aa_model_id": self.aa_model_id,
            "aa_score": self.aa_score,
            "coding_score": self.coding_score,
            "benchmarks": self.benchmarks.to_dict() if self.benchmarks else {},
            "evidence": self.evidence,
            "evidence_level": self.evidence_level,
            "confidence": self.confidence,
            "tier": self.tier,
            "pricing": self.pricing.to_dict() if self.pricing else {},
            "_meta": self._meta.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelInfoRecord":
        return cls(
            aa_model_id=data.get("aa_model_id"),
            aa_score=data.get("aa_score"),
            coding_score=data.get("coding_score"),
            benchmarks=BenchmarkSnapshot.from_dict(data.get("benchmarks")),
            evidence=list(data.get("evidence", [])),
            evidence_level=data.get("evidence_level"),
            confidence=data.get("confidence"),
            tier=data.get("tier"),
            pricing=PricingSnapshot.from_dict(data.get("pricing")),
            _meta=StoreMeta.from_dict(data.get("_meta")),
        )

    @classmethod
    def from_provider_record(cls, rec: dict[str, Any], provider: str | None = None, evaluated_at: str | None = None) -> "ModelInfoRecord":
        """Build from a ProviderBatchWriter._to_record-style dict."""
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
            # scalar blended fallback
            try:
                pricing_snap = PricingSnapshot(blended=float(pricing_raw))
            except Exception:
                pricing_snap = None
        tier = _normalize_tier(rec.get("tier", rec.get("category")))
        now = evaluated_at or datetime.now(UTC).isoformat()
        meta = StoreMeta(
            first_seen=now,
            last_updated=now,
            source_providers=[provider] if provider else [],
            source_evidence_levels=[rec.get("evidence_level")] if rec.get("evidence_level") else [],
        )
        return cls(
            aa_model_id=rec.get("aa_model_id"),
            aa_score=rec.get("aa_score"),
            coding_score=rec.get("coding_score"),
            benchmarks=bench,
            evidence=list(rec.get("evidence", [])),
            evidence_level=rec.get("evidence_level"),
            confidence=rec.get("confidence"),
            tier=tier,
            pricing=pricing_snap,
            _meta=meta,
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
    Merge incoming into existing per #64 rules:
    - Ordinal: strong > moderate > weak > none (tie-break confidence -> newer last_updated)
    - Per-field best-of + gap-fill: keep strongest value per field; fill null gaps from weaker
    - Benchmarks: union max per key
    - Pricing: aggregated later via aggregate_pricing; here gap-fill only if existing pricing null
    - Provenance: union source_providers / source_evidence_levels, bump last_updated
    """
    if existing is None:
        return incoming
    # Determine stronger record for tie-break
    e_rank = evidence_level_rank(existing.evidence_level)
    i_rank = evidence_level_rank(incoming.evidence_level)
    # Helper to pick winner per-field by rank, then confidence, then recency
    def _pick(field_name: str):
        e_val = getattr(existing, field_name)
        i_val = getattr(incoming, field_name)
        # Gap-fill: if existing null and incoming has value, take incoming
        if e_val is None and i_val is not None:
            return i_val
        if i_val is None:
            return e_val
        # Both present: higher evidence wins
        if i_rank > e_rank:
            return i_val
        if e_rank > i_rank:
            return e_val
        # Tie: higher confidence wins
        e_conf = existing.confidence or 0
        i_conf = incoming.confidence or 0
        if i_conf > e_conf:
            return i_val
        if e_conf > i_conf:
            return e_val
        # Tie: newer last_updated wins
        e_ts = existing._meta.last_updated or ""
        i_ts = incoming._meta.last_updated or ""
        if i_ts > e_ts:
            return i_val
        return e_val

    merged = ModelInfoRecord(
        aa_model_id=_pick("aa_model_id"),
        aa_score=_pick("aa_score"),
        coding_score=_pick("coding_score"),
        benchmarks=_benchmark_union_max(existing.benchmarks, incoming.benchmarks),
        evidence=_pick("evidence"),
        evidence_level=_pick("evidence_level"),
        confidence=_pick("confidence"),
        tier=_pick("tier"),
        pricing=_pick("pricing"),  # gap-fill; aggregated pricing handled at store-level
        _meta=StoreMeta(
            first_seen=existing._meta.first_seen or incoming._meta.first_seen,
            last_updated=max(
                [t for t in [existing._meta.last_updated, incoming._meta.last_updated] if t],
                default=incoming._meta.last_updated or existing._meta.last_updated,
            ),
            source_providers=sorted(set((existing._meta.source_providers or []) + (incoming._meta.source_providers or []))),
            source_evidence_levels=sorted(set((existing._meta.source_evidence_levels or []) + (incoming._meta.source_evidence_levels or []))),
            version=max(existing._meta.version, incoming._meta.version),
        ),
    )
    # Fix benchmarks provenance: ensure evidence_level/confidence source not lost
    # If incoming had stronger evidence, evidence should reflect that winner (handled by _pick)
    return merged

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
    "EVIDENCE_LEVEL_ORDER",
    "CACHEABLE_LEVELS",
    "FIELD_INCLUSION_MATRIX",
    "RECOMMENDED_STORE_PATH",
    "STORE_SCHEMA_DOC",
    "EXAMPLE_YAML_SNIPPET",
]
