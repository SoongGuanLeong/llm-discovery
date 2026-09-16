
"""
Reusable cross-provider model-info store — slim v2.

Slim Source of Truth holds only benchmarks, pricing, freshness.
Keys normalized via normalize_store_key.
File: data/model_info_store.json  {version: 2, models: {key: {benchmarks, pricing, _meta, facts, evidence_snapshot_hash, judgement, _meta}}} TTL 14d, pricing via facts re-average, Tier derived on read.
New shape (issue #224 contract) is canonical; legacy judge? is compat-read only (no tier). Tier is never persisted (derived on read).
Atomic tmp+rename, version header 2, compat read for v1 (ignore dropped keys).
"""

from __future__ import annotations

import json
import os
import re
import statistics
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECOMMENDED_STORE_PATH = "data/model_info_store.json"
RECOMMENDED_STORE_PATH_OBJ: Path = Path(RECOMMENDED_STORE_PATH)
STORE_FILE_VERSION: int = 2
DEFAULT_TTL_DAYS: int = 14
# Per-evidence TTL split (issue #221 supersedes ADR 0007 single 14d TTL; DEFAULT 14d for GC live-set scan - issue #224)
PRICING_TTL_DAYS: int = 7
PRICING_TTL_MIN_DAYS: int = 3
CATALOG_ROW_TTL_DAYS: int = 14
CATALOG_ROW_TTL_MIN_DAYS: int = 7
BENCHMARK_TTL_DAYS: int = 90
BENCHMARK_TTL_MIN_DAYS: int = 60

# ---------------------------------------------------------------------------
# Key normalization
# ---------------------------------------------------------------------------

def _normalize_model_id_stepfun(model_id: str) -> str:
    if model_id.startswith("stepfun-"):
        return "step-" + model_id[len("stepfun-"): ]
    if model_id.startswith("stepfun/"):
        return "step/" + model_id[len("stepfun/"): ]
    return model_id


def normalize_store_key(model_id: str) -> str:
    if not model_id:
        return ""
    raw = model_id.strip().lower()
    raw = re.sub(r"[:/_-]free$", "", raw)
    raw = _normalize_model_id_stepfun(raw)
    raw = re.sub(r"[:/_-]free$", "", raw)
    slug = raw.rsplit("/", 1)[-1]
    if ":" in slug:
        parts = slug.split(":")
        if parts[-1] == "free":
            slug = ":".join(parts[:-1])
    slug = _normalize_model_id_stepfun(slug)
    slug = re.sub(r"[:/_-]free$", "", slug)
    slug = slug.strip("-_./:")
    return slug


def normalized_key_with_matcher(model_id: str) -> str:
    try:
        from .model_matching import normalize_model_id as _mm_normalize
    except Exception:
        return normalize_store_key(model_id)
    slug = model_id.strip().rsplit("/", 1)[-1]
    canonical = _mm_normalize(slug)
    canonical = _normalize_model_id_stepfun(canonical)
    canonical = re.sub(r"[:/_-]free$", "", canonical)
    return canonical.strip("-_./:")

# ---------------------------------------------------------------------------
# Pricing aggregation
# ---------------------------------------------------------------------------

PRICING_OUTLIER_BLEND_THRESHOLD = 0.20
PRICING_OUTLIER_IO_THRESHOLD = 0.15
PRICING_OUTLIER_RATIO = 0.50


def is_pricing_outlier(candidate_blended: float, median_blended: float, candidate_io: float | None = None, median_io: float | None = None) -> bool:
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
    blended_vals = [o["blended"] for o in normed if o["blended"] is not None]
    if not blended_vals:
        return {
            "blended": None,
            "input": statistics.mean([o["input"] for o in normed if o["input"] is not None]) if any(o["input"] is not None for o in normed) else None,
            "output": statistics.mean([o["output"] for o in normed if o["output"] is not None]) if any(o["output"] is not None for o in normed) else None,
            "per_provider_overrides": {},
        }
    median_blended = statistics.median(blended_vals)
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
# Store schema — slim v2
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkSnapshot:
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
    first_seen: str | None = None
    last_updated: str | None = None
    version: int = 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "first_seen": self.first_seen,
            "last_updated": self.last_updated,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "StoreMeta":
        if not data:
            return cls()
        return cls(
            first_seen=data.get("first_seen"),
            last_updated=data.get("last_updated"),
            version=int(data.get("version", 2)),
        )


@dataclass
class PricingSnapshot:
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
        d["per_provider_overrides"] = self.per_provider_overrides
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
class JudgeSnapshot:
    evidence_level: str = "strong"
    evidence: list[str] = field(default_factory=list)
    confidence: float = 0.0
    coding: bool = True
    canonical_name: str | None = None
    tier: str | None = None
    decision: str = "keep"
    judge_model: str | None = None
    evidence_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "evidence_level": self.evidence_level,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "coding": self.coding,
            "decision": self.decision,
        }
        if self.canonical_name is not None:
            d["canonical_name"] = self.canonical_name
        # tier is derived on read (issue #224 contract) — never persisted
        if self.judge_model is not None:
            d["judge_model"] = self.judge_model
        if self.evidence_hash is not None:
            d["evidence_hash"] = self.evidence_hash
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None):
        if not data or not isinstance(data, dict):
            return None
        lvl = str(data.get("evidence_level", "strong")).strip().lower()
        return cls(
            evidence_level=lvl,
            evidence=list(data.get("evidence", [])),
            confidence=float(data.get("confidence", 0.0)) if data.get("confidence") is not None else 0.0,
            coding=bool(data.get("coding", True)),
            canonical_name=data.get("canonical_name"),
            tier=data.get("tier"),
            decision=str(data.get("decision", "keep")),
            judge_model=data.get("judge_model"),
            evidence_hash=data.get("evidence_hash"),
        )



@dataclass
class FactsSnapshot:
    """Raw evaluation inputs kept beside the slim v2 shape (issue #223 expand).

    aa_row_hash: deterministic AA-row identity hash,
    compute_evidence_hash(aa_score, None, pricing_blended, None) — the same AA
    row (score + blended pricing) hashes identically regardless of provider or
    timestamp, so it identifies the AA row, not a specific observation.
    pricing_observations: per-provider pricing observations
    [{provider, blended, input, output, ts}] accumulated across merges so a
    later aggregate_pricing can re-average (the old pricing snapshot
    collapses history). bench_raw: raw benchmark scores map, union-merged with
    the same conflict policy as benchmarks.scores.
    """

    aa_row_hash: str | None = None
    pricing_observations: list[dict[str, Any]] = field(default_factory=list)
    bench_raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.aa_row_hash is not None:
            d["aa_row_hash"] = self.aa_row_hash
        if self.pricing_observations:
            d["pricing_observations"] = list(self.pricing_observations)
        if self.bench_raw:
            d["bench_raw"] = self.bench_raw
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FactsSnapshot | None":
        if not data or not isinstance(data, dict):
            return None
        return cls(
            aa_row_hash=data.get("aa_row_hash"),
            pricing_observations=[o for o in data.get("pricing_observations", []) if isinstance(o, dict)],
            bench_raw=dict(data.get("bench_raw", {})),
        )


@dataclass
class JudgementSnapshot:
    """What the evaluation concluded (issue #223 expand) — recorded for EVERY
    record that has an evidence_level, not only strong/moderate. NO tier field:
    tier is derived on read via derive_tier (categorize) from fresh
    pricing/coding_score.
    """

    evidence_level: str = ""
    decision: str = "keep"
    confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    judge_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "evidence_level": self.evidence_level,
            "decision": self.decision,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }
        if self.judge_model is not None:
            d["judge_model"] = self.judge_model
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "JudgementSnapshot | None":
        if not data or not isinstance(data, dict):
            return None
        return cls(
            evidence_level=str(data.get("evidence_level", "")).strip().lower(),
            decision=str(data.get("decision", "keep")),
            confidence=float(data.get("confidence", 0.0)) if data.get("confidence") is not None else 0.0,
            evidence=list(data.get("evidence", [])),
            judge_model=data.get("judge_model"),
        )


@dataclass
class ModelInfoRecord:
    benchmarks: BenchmarkSnapshot | None = None
    pricing: PricingSnapshot | None = None
    _meta: StoreMeta = field(default_factory=StoreMeta)
    judge: JudgeSnapshot | None = None  # legacy compat (no tier, contract #224)
    # New shape (issue #224 contract): canonical; None only on very old v2 payloads
    facts: FactsSnapshot | None = None
    evidence_snapshot_hash: str | None = None
    judgement: JudgementSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "benchmarks": self.benchmarks.to_dict() if self.benchmarks else {"scores": {}, "raw_benchmarks": []},
            "pricing": self.pricing.to_dict() if self.pricing else {"per_provider_overrides": {}},
            "_meta": self._meta.to_dict(),
        }
        # Legacy judge is compat-read only (no tier, issue #224 contract); new code prefers judgement
        if self.judge is not None:
            jd = self.judge.to_dict()
            jd.pop("tier", None)
            d["judge"] = jd
        # New shape (issue #224 contract) is canonical
        if self.facts is not None:
            facts_d = self.facts.to_dict()
            if facts_d:
                d["facts"] = facts_d
        if self.evidence_snapshot_hash is not None:
            d["evidence_snapshot_hash"] = self.evidence_snapshot_hash
        if self.judgement is not None:
            d["judgement"] = self.judgement.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelInfoRecord":
        if not isinstance(data, dict):
            data = {}
        # Compat: ignore dropped v1 keys, only read slim keys; new keys
        # (issue #223) are read when present and absent on old v2 payloads
        # (fields stay None).
        return cls(
            benchmarks=BenchmarkSnapshot.from_dict(data.get("benchmarks")),
            pricing=PricingSnapshot.from_dict(data.get("pricing")),
            _meta=StoreMeta.from_dict(data.get("_meta")),
            judge=JudgeSnapshot.from_dict(data.get("judge")) if data.get("judge") else None,
            facts=FactsSnapshot.from_dict(data.get("facts")),
            evidence_snapshot_hash=data.get("evidence_snapshot_hash"),
            judgement=JudgementSnapshot.from_dict(data.get("judgement")),
        )

    @classmethod
    def from_provider_record(cls, rec: dict[str, Any], provider: str | None = None, evaluated_at: str | None = None) -> "ModelInfoRecord":
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
        meta = StoreMeta(first_seen=now, last_updated=now, version=2)
        judge_snap = None
        lvl = str(rec.get("evidence_level", "")).strip().lower()
        # Evidence inputs (AA + bench scores + pricing blended + claim URLs) —
        # shared by the judge snapshot's evidence_hash (issue #221) and the new
        # evidence_snapshot_hash (issue #223). Same derivation for both, so the
        # hashes always agree.
        _aa = None
        _bench_scores = None
        _pricing_blended = None
        _claim_urls: list[str] = []
        _evidence_hash = None
        try:
            _aa = rec.get("aa_score") or (rec.get("aa_model") or {}).get("score" if isinstance(rec.get("aa_model"), dict) else None)
            _bm = rec.get("benchmarks")
            _bench_scores = _bm.get("scores") if isinstance(_bm, dict) else rec.get("bench_scores")
            _pr = rec.get("pricing")
            if isinstance(_pr, dict):
                _pricing_blended = _pr.get("blended", _pr.get("price_1m_blended_3_to_1"))
            _claim_urls = [e for e in rec.get("evidence", []) if isinstance(e, str) and e.startswith("http")]
            # Issue #224 contract: evidence_snapshot_hash excludes pricing_blended so pricing re-average updates tier without LLM (hash unchanged)
            _evidence_hash = compute_evidence_hash(_aa, _bench_scores if isinstance(_bench_scores, dict) else None, None, _claim_urls)
        except Exception:
            _evidence_hash = None
        # Persist judge_llm result for strong+moderate — include evidence_hash (AA+bench+pricing+URLs) for per-evidence TTL (issue #221)
        if lvl in ("strong", "moderate"):
            try:
                judge_snap = JudgeSnapshot(
                    evidence_level=lvl,
                    evidence=list(rec.get("evidence", []))[:3],
                    confidence=float(rec.get("confidence", 0.0)) if rec.get("confidence") is not None else 0.0,
                    coding=bool(rec.get("coding", rec.get("is_coding", True))),
                    canonical_name=rec.get("canonical_name"),
                    decision=str(rec.get("decision", "keep")),
                    judge_model=rec.get("judge_model") or rec.get("_judge_model"),
                    evidence_hash=rec.get("evidence_hash") or _evidence_hash,
                )
            except Exception:
                judge_snap = None
        # New shape (issue #223 expand): judgement for EVERY record that has an
        # evidence_level — it records what the evaluation concluded (weak/none/
        # uncertain/error included). No tier: derived on read (derive_tier).
        judgement_snap = None
        if lvl:
            try:
                judgement_snap = JudgementSnapshot(
                    evidence_level=lvl,
                    decision=str(rec.get("decision", "keep")),
                    confidence=float(rec.get("confidence", 0.0)) if rec.get("confidence") is not None else 0.0,
                    evidence=list(rec.get("evidence", []))[:3],
                    judge_model=rec.get("judge_model") or rec.get("_judge_model"),
                )
            except Exception:
                judgement_snap = None
        # facts: raw inputs kept for later re-derivation (per-provider pricing
        # observations, raw bench scores, deterministic AA-row identity hash)
        facts_snap = None
        try:
            _obs: list[dict[str, Any]] = []
            if pricing_snap is not None and any(v is not None for v in (pricing_snap.blended, pricing_snap.input, pricing_snap.output)):
                _obs = [{"provider": provider, "blended": pricing_snap.blended, "input": pricing_snap.input, "output": pricing_snap.output, "ts": now}]
            _bench_raw = dict(bench.scores) if bench is not None and bench.scores else {}
            _aa_row_hash = None
            if _aa is not None:
                try:
                    _aa_row_hash = compute_evidence_hash(_aa, None, _pricing_blended, None)
                except Exception:
                    _aa_row_hash = None
            if _obs or _bench_raw or _aa_row_hash is not None:
                facts_snap = FactsSnapshot(aa_row_hash=_aa_row_hash, pricing_observations=_obs, bench_raw=_bench_raw)
        except Exception:
            facts_snap = None
        return cls(benchmarks=bench, pricing=pricing_snap, _meta=meta, judge=judge_snap, facts=facts_snap, evidence_snapshot_hash=_evidence_hash, judgement=judgement_snap)

def _scores_union_max(existing_scores: dict[str, Any], incoming_scores: dict[str, Any]) -> dict[str, Any]:
    """Union two benchmark score maps; on conflict keep the higher score.

    Same conflict policy for benchmarks.scores (old shape) and
    facts.bench_raw (issue #223 expand).
    """
    merged_scores: dict[str, Any] = dict(existing_scores or {})
    for k, v in (incoming_scores or {}).items():
        if k not in merged_scores:
            merged_scores[k] = v
        else:
            try:
                ev = merged_scores[k]
                e_score = ev.get("score") if isinstance(ev, dict) else getattr(ev, "score", 0)
                i_score = v.get("score") if isinstance(v, dict) else getattr(v, "score", 0)
                if float(i_score) > float(e_score):
                    merged_scores[k] = v
            except Exception:
                pass
    return merged_scores


def _benchmark_union_max(existing: BenchmarkSnapshot | None, incoming: BenchmarkSnapshot | None) -> BenchmarkSnapshot:
    if not existing:
        return incoming or BenchmarkSnapshot()
    if not incoming:
        return existing
    merged_scores = _scores_union_max(existing.scores, incoming.scores)
    seen = {str(b) for b in (existing.raw_benchmarks or [])}
    merged_raw = list(existing.raw_benchmarks or [])
    for b in (incoming.raw_benchmarks or []):
        if str(b) not in seen:
            merged_raw.append(b)
            seen.add(str(b))
    bc = None
    if existing.benchmark_coverage is not None or incoming.benchmark_coverage is not None:
        vals = [v for v in [existing.benchmark_coverage, incoming.benchmark_coverage] if v is not None]
        bc = max(vals) if vals else None  # type: ignore
    cws = None
    if existing.coverage_with_supplements is not None or incoming.coverage_with_supplements is not None:
        vals = [v for v in [existing.coverage_with_supplements, incoming.coverage_with_supplements] if v is not None]
        cws = max(vals) if vals else None  # type: ignore
    return BenchmarkSnapshot(scores=merged_scores, raw_benchmarks=merged_raw, benchmark_coverage=bc, coverage_with_supplements=cws)  # type: ignore


def _freshest(existing_val: Any, incoming_val: Any, existing_ts: str | None, incoming_ts: str | None) -> Any:
    """Freshest-wins selection for snapshot fields (judge/judgement/hashes):
    incoming wins when present, unless existing is strictly fresher."""
    if incoming_val is None:
        return existing_val
    if existing_val is None:
        return incoming_val
    try:
        if (existing_ts or "") > (incoming_ts or ""):
            return existing_val
    except Exception:
        return incoming_val
    return incoming_val


def _pricing_observations_union(existing_obs: list[dict[str, Any]], incoming_obs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union per-provider pricing observations; same provider -> latest ts wins."""
    merged: dict[str | None, dict[str, Any]] = {}
    for o in list(existing_obs or []) + list(incoming_obs or []):
        if not isinstance(o, dict):
            continue
        key = o.get("provider")
        prev = merged.get(key)
        if prev is None or str(o.get("ts", "")) >= str(prev.get("ts", "")):
            merged[key] = dict(o)
    return list(merged.values())


def _facts_union(existing: FactsSnapshot | None, incoming: FactsSnapshot | None, existing_ts: str | None, incoming_ts: str | None) -> FactsSnapshot | None:
    """Merge facts (issue #223): per-provider obs union, bench_raw union-max,
    aa_row_hash freshest."""
    if existing is None:
        return incoming
    if incoming is None:
        return existing
    return FactsSnapshot(
        aa_row_hash=_freshest(existing.aa_row_hash, incoming.aa_row_hash, existing_ts, incoming_ts),
        pricing_observations=_pricing_observations_union(existing.pricing_observations, incoming.pricing_observations),
        bench_raw=_scores_union_max(existing.bench_raw, incoming.bench_raw),
    )


def merge_records(existing: ModelInfoRecord | None, incoming: ModelInfoRecord) -> ModelInfoRecord:
    if existing is None:
        return incoming
    # Pricing: prefer facts pricing_observations (per-provider history) for re-average; fallback to snapshots
    merged_pricing = None
    obs_list: list[dict[str, Any]] = []
    for facts_snap in (existing.facts, incoming.facts):
        if facts_snap is not None and facts_snap.pricing_observations:
            obs_list.extend(list(facts_snap.pricing_observations))
    if obs_list:
        try:
            agg = aggregate_pricing(obs_list)
            if agg:
                merged_pricing = PricingSnapshot(blended=agg.get('blended'), input=agg.get('input'), output=agg.get('output'), per_provider_overrides=agg.get('per_provider_overrides', {}))
            else:
                merged_pricing = existing.pricing
        except Exception:
            merged_pricing = existing.pricing or incoming.pricing
    else:
        snap_obs = []
        for snap in (existing.pricing, incoming.pricing):
            if snap:
                obs = snap.to_dict() if hasattr(snap, 'to_dict') else dict(snap)
                snap_obs.append(obs)
        if len(snap_obs) >= 2:
            try:
                agg = aggregate_pricing(snap_obs)
                if agg:
                    merged_pricing = PricingSnapshot(blended=agg.get('blended'), input=agg.get('input'), output=agg.get('output'), per_provider_overrides=agg.get('per_provider_overrides', {}))
                else:
                    merged_pricing = existing.pricing
            except Exception:
                merged_pricing = existing.pricing or incoming.pricing
        elif len(snap_obs) == 1:
            merged_pricing = existing.pricing or incoming.pricing
        else:
            merged_pricing = None
    first_seen_vals = [t for t in [existing._meta.first_seen, incoming._meta.first_seen] if t]
    last_vals = [t for t in [existing._meta.last_updated, incoming._meta.last_updated] if t]
    merged_meta = StoreMeta(
        first_seen=min(first_seen_vals) if first_seen_vals else (existing._meta.first_seen or incoming._meta.first_seen),
        last_updated=max(last_vals) if last_vals else (incoming._meta.last_updated or existing._meta.last_updated),
        version=2,
    )
    # Judge: keep most recent strong (incoming wins if present, else keep
    # existing); prefer fresher last_updated when both have judge
    merged_judge = _freshest(existing.judge, incoming.judge, existing._meta.last_updated, incoming._meta.last_updated)
    # New shape (issue #223 expand): merge beside the old keys, same freshness policy
    merged_facts = _facts_union(existing.facts, incoming.facts, existing._meta.last_updated, incoming._meta.last_updated)
    merged_judgement = _freshest(existing.judgement, incoming.judgement, existing._meta.last_updated, incoming._meta.last_updated)
    merged_evidence_hash = _freshest(existing.evidence_snapshot_hash, incoming.evidence_snapshot_hash, existing._meta.last_updated, incoming._meta.last_updated)
    return ModelInfoRecord(
        benchmarks=_benchmark_union_max(existing.benchmarks, incoming.benchmarks),
        pricing=merged_pricing,
        _meta=merged_meta,
        judge=merged_judge,
        facts=merged_facts,
        evidence_snapshot_hash=merged_evidence_hash,
        judgement=merged_judgement,
    )


def derive_tier(
    record: ModelInfoRecord,
    *,
    min_score: float = 24.0,
    max_score: float = 45.0,
    model_id: str | None = None,
    coding_score: float | None = None,
    aa_score: float | None = None,
) -> str:
    """Derive the tier token for a stored record on read — tier is not persisted
    in the new shape (issue #223 expand).

    Inputs derived from the record: pricing blended from record.pricing,
    judge decision from record.judgement (falling back to the legacy
    record.judge), coding from record.judge (the new judgement shape does not
    persist coding). *coding_score*/*aa_score* come from the caller (fresh
    catalog row) because they are not part of the slim store. Returns the
    categorize.categorize_model band ("max" | "flash" | "contributor_free" |
    "drop" | "uncertain" | "error"); "uncertain" when score inputs are missing.
    """
    from .categorize import categorize_model  # lazy import to avoid a cycle

    decision = "keep"
    if record.judgement is not None and record.judgement.decision:
        decision = str(record.judgement.decision)
    elif record.judge is not None and record.judge.decision:
        decision = str(record.judge.decision)
    coding = True
    if record.judge is not None:
        coding = bool(record.judge.coding)
    pricing_blended = record.pricing.blended if record.pricing is not None else None
    return categorize_model(
        coding,
        aa_score,
        min_score=min_score,
        max_score=max_score,
        judge_decision=decision,
        model_id=model_id,
        coding_score=coding_score,
        pricing_blended=pricing_blended,
    )


STORE_SCHEMA_DOC = """
# data/model_info_store.json — committed snapshot (JSON, atomic write)
# Key: normalize_store_key(provider model_id)
#     -> {benchmarks, pricing, _meta, facts, evidence_snapshot_hash, judgement, _meta}  (judge? legacy compat only, no tier)
# Contract shape (#224): facts + evidence_snapshot_hash (AA+bench+URLs, no pricing) + judgement (no tier); tier derived on read via derive_tier; GC 14d share-aware
"""

__all__ = [
    "normalize_store_key",
    "normalized_key_with_matcher",
    "is_pricing_outlier",
    "aggregate_pricing",
    "merge_records",
    "ModelInfoRecord",
    "BenchmarkSnapshot",
    "PricingSnapshot",
    "StoreMeta",
    "FactsSnapshot",
    "JudgementSnapshot",
    "derive_tier",
    "ModelInfoStore",
    "STORE_FILE_VERSION",
    "RECOMMENDED_STORE_PATH_OBJ",
    "RECOMMENDED_STORE_PATH",
    "STORE_SCHEMA_DOC",
]

STORE_FILE_VERSION: int = 2
DEFAULT_TTL_DAYS: int = 14
RECOMMENDED_STORE_PATH_OBJ: Path = Path(RECOMMENDED_STORE_PATH)

def compute_evidence_hash(aa_score: float | None, bench_scores: dict | None, pricing_blended: float | None, claim_urls: list | None) -> str:
    import hashlib, json
    payload = {
        "aa_score": round(float(aa_score), 2) if aa_score is not None else None,
        "bench_scores": {k: round(float(v.get("score", v) if isinstance(v, dict) else float(v)), 2) for k, v in (bench_scores or {}).items()} if bench_scores else {},
        "pricing_blended": round(float(pricing_blended), 4) if pricing_blended is not None else None,
        "claim_urls": sorted([str(u) for u in (claim_urls or []) if u]),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def is_pricing_stale(last_updated: str | None) -> bool:
    return is_stale(last_updated, PRICING_TTL_DAYS)

def is_benchmark_stale(last_updated: str | None) -> bool:
    return is_stale(last_updated, BENCHMARK_TTL_DAYS)

def is_catalog_row_stale(last_updated: str | None) -> bool:
    return is_stale(last_updated, CATALOG_ROW_TTL_DAYS)

def is_judgement_stale(cached_hash: str | None, current_hash: str | None) -> bool:
    if not cached_hash or not current_hash:
        return True
    return cached_hash != current_hash

def is_stale(last_updated: str | None, ttl_days: int | None = None) -> bool:
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
    def __init__(self, path: str | Path | None = None) -> None:
        self.path: Path = Path(path) if path is not None else RECOMMENDED_STORE_PATH_OBJ
        self._data: dict[str, ModelInfoRecord] = {}
        self._loaded: bool = False
        self._file_version: int = STORE_FILE_VERSION

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
        if isinstance(raw, dict) and "models" in raw:
            self._file_version = int(raw.get("version", STORE_FILE_VERSION))
            models_raw = raw.get("models", {})
        elif isinstance(raw, dict):
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
            "models": {k: v.to_dict() for k, v in sorted(self._data.items())},
        }
        _atomic_write_json(self.path, payload)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

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

    def put(self, store_key: str, record: ModelInfoRecord) -> None:
        self._ensure_loaded()
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
        # Slim v2: accept any provider record, no gating here (gate at pipeline)
        rec = ModelInfoRecord.from_provider_record(provider_record, provider=provider, evaluated_at=evaluated_at)
        self.put_for_model(provider_model_id, rec)
        return True

    def merge_from_dict(self, models_dict: dict[str, dict[str, Any]]) -> int:
        count = 0
        for k, v in (models_dict or {}).items():
            try:
                rec = ModelInfoRecord.from_dict(v) if isinstance(v, dict) else v
                self.put(str(k), rec)
                count += 1
            except Exception:
                continue
        return count

    def dumps_compact(self) -> str:
        self._ensure_loaded()
        payload = {"version": STORE_FILE_VERSION, "models": {k: v.to_dict() for k, v in sorted(self._data.items())}}
        return dumps_compact(payload)

    def dumps_pretty(self) -> str:
        self._ensure_loaded()
        payload = {"version": STORE_FILE_VERSION, "models": {k: v.to_dict() for k, v in sorted(self._data.items())}}
        return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

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
        self._ensure_loaded()
        if store_key in self._data:
            del self._data[store_key]
            self.save()
            return True
        return False

    def gc(self, live_keys: set[str], ttl_days: int | None = None) -> int:
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
