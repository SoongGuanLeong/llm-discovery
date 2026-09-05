
"""
Reusable cross-provider model-info store — slim v2.

Slim Source of Truth holds only benchmarks, pricing, freshness.
Keys normalized via normalize_store_key.
File: data/model_info_store.json  {version: 2, models: {key: {benchmarks, pricing, _meta}}}
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
class ModelInfoRecord:
    benchmarks: BenchmarkSnapshot | None = None
    pricing: PricingSnapshot | None = None
    _meta: StoreMeta = field(default_factory=StoreMeta)

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmarks": self.benchmarks.to_dict() if self.benchmarks else {"scores": {}, "raw_benchmarks": []},
            "pricing": self.pricing.to_dict() if self.pricing else {"per_provider_overrides": {}},
            "_meta": self._meta.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelInfoRecord":
        if not isinstance(data, dict):
            data = {}
        # Compat: ignore dropped v1 keys, only read slim keys
        return cls(
            benchmarks=BenchmarkSnapshot.from_dict(data.get("benchmarks")),
            pricing=PricingSnapshot.from_dict(data.get("pricing")),
            _meta=StoreMeta.from_dict(data.get("_meta")),
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
        return cls(benchmarks=bench, pricing=pricing_snap, _meta=meta)

def _benchmark_union_max(existing: BenchmarkSnapshot | None, incoming: BenchmarkSnapshot | None) -> BenchmarkSnapshot:
    if not existing:
        return incoming or BenchmarkSnapshot()
    if not incoming:
        return existing
    merged_scores: dict[str, Any] = dict(existing.scores)
    for k, v in (incoming.scores or {}).items():
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


def merge_records(existing: ModelInfoRecord | None, incoming: ModelInfoRecord) -> ModelInfoRecord:
    if existing is None:
        return incoming
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
    first_seen_vals = [t for t in [existing._meta.first_seen, incoming._meta.first_seen] if t]
    last_vals = [t for t in [existing._meta.last_updated, incoming._meta.last_updated] if t]
    merged_meta = StoreMeta(
        first_seen=min(first_seen_vals) if first_seen_vals else (existing._meta.first_seen or incoming._meta.first_seen),
        last_updated=max(last_vals) if last_vals else (incoming._meta.last_updated or existing._meta.last_updated),
        version=2,
    )
    return ModelInfoRecord(
        benchmarks=_benchmark_union_max(existing.benchmarks, incoming.benchmarks),
        pricing=merged_pricing,
        _meta=merged_meta,
    )

STORE_SCHEMA_DOC = """
# data/model_info_store.json — committed snapshot (JSON, atomic write)
# Key: normalize_store_key(provider model_id)  -> {benchmarks, pricing, _meta}
# Slim v2: only benchmarks, pricing, _meta {first_seen, last_updated, version:2}
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
    "ModelInfoStore",
    "STORE_FILE_VERSION",
    "RECOMMENDED_STORE_PATH_OBJ",
    "RECOMMENDED_STORE_PATH",
    "STORE_SCHEMA_DOC",
]

STORE_FILE_VERSION: int = 2
DEFAULT_TTL_DAYS: int = 14
RECOMMENDED_STORE_PATH_OBJ: Path = Path(RECOMMENDED_STORE_PATH)

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
            "models": {k: v.to_dict() for k, v in self._data.items()},
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
        payload = {"version": STORE_FILE_VERSION, "models": {k: v.to_dict() for k, v in self._data.items()}}
        return dumps_compact(payload)

    def dumps_pretty(self) -> str:
        self._ensure_loaded()
        payload = {"version": STORE_FILE_VERSION, "models": {k: v.to_dict() for k, v in self._data.items()}}
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
