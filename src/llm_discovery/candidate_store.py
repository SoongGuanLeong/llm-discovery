"""Candidate store -- weak/none evidence cache with long TTL (issue #222).

Weak/none models never pass the Accurate-Enough Gate (strong-only) and are
otherwise re-evaluated on every build. This store caches those "insufficient
evidence" results so a build with an identical evidence_hash skips the LLM.

Distinct from the slim v2 Source of Truth (data/model_info_store.json):
Candidates never become Keepers (is_accurate_enough still blocks them), so
they live in a separate file and must not pollute the Keeper store.

Reuse rule (issue #222):
  - same cache identity + identical evidence_hash + age <= CANDIDATE_TTL_DAYS -> hit
  - evidence_hash changed (evidence recovered/changed) -> miss, re-evaluate
  - a re-evaluation that yields strong/moderate deletes the stale entry

File: data/model_candidate_store.json
  {"version": 1, "candidates": {key: {model_id, evidence_hash, evidence_level, decision, tier, last_updated}}}
Atomic tmp+rename via model_info_store._atomic_write_json; fcntl lock +
disk merge under parallel provider threads (same pattern as ModelInfoStore).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .model_info_store import _atomic_write_json, normalize_store_key

# 60-90d TTL band per issue #222: long enough to stop per-build retry waste,
# short enough that recovered evidence is picked up promptly.
CANDIDATE_TTL_DAYS = 90
CANDIDATE_TTL_MIN_DAYS = 60
assert CANDIDATE_TTL_MIN_DAYS <= CANDIDATE_TTL_DAYS <= 90

CANDIDATE_STORE_VERSION = 1
RECOMMENDED_CANDIDATE_STORE_PATH = "data/model_candidate_store.json"


@dataclass
class CandidateRecord:
    """One cached weak/none evaluation, keyed by normalized cache identity."""

    model_id: str
    evidence_hash: str
    evidence_level: str = "weak"  # "weak" | "none"
    decision: str = "uncertain"
    tier: str = "uncertain"
    last_updated: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "model_id": self.model_id,
            "evidence_hash": self.evidence_hash,
            "evidence_level": self.evidence_level,
            "decision": self.decision,
            "tier": self.tier,
        }
        if self.last_updated is not None:
            d["last_updated"] = self.last_updated
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CandidateRecord | None":
        if not data or not isinstance(data, dict):
            return None
        return cls(
            model_id=str(data.get("model_id", "")),
            evidence_hash=str(data.get("evidence_hash", "")),
            evidence_level=str(data.get("evidence_level", "weak")).strip().lower(),
            decision=str(data.get("decision", "uncertain")).strip().lower(),
            tier=str(data.get("tier", "uncertain")).strip().lower(),
            last_updated=data.get("last_updated"),
        )


class CandidateCacheStats:
    """Thread-safe per-build candidate-cache hit/miss counter.

    build_all evaluates providers in parallel threads sharing one CandidateStore,
    so counters are locked; telemetry reads totals at build tail (issue #222 AC4).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def record_hit(self) -> None:
        with self._lock:
            self.hits += 1

    def record_miss(self) -> None:
        with self._lock:
            self.misses += 1

    def as_dict(self) -> dict[str, int]:
        with self._lock:
            return {"hits": self.hits, "misses": self.misses}


class CandidateStore:
    """Durable weak/none result store, separate from the slim v2 Keeper store.

    Keys are normalized cache identities (resolve_cache_identity output).
    get/put/delete are safe under concurrent provider threads (fcntl lock +
    disk merge, mirroring ModelInfoStore.put).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path: Path = Path(path) if path is not None else Path(RECOMMENDED_CANDIDATE_STORE_PATH)
        self._data: dict[str, CandidateRecord] = {}
        self._loaded: bool = False
        self.stats = CandidateCacheStats()

    def load(self) -> None:
        if not self.path.exists():
            self._data = {}
            self._loaded = True
            return
        try:
            raw = json.loads(self.path.read_text())
        except Exception:
            self._data = {}
            self._loaded = True
            return
        candidates_raw = raw.get("candidates", {}) if isinstance(raw, dict) else {}
        data: dict[str, CandidateRecord] = {}
        for k, v in (candidates_raw or {}).items():
            try:
                rec = CandidateRecord.from_dict(v)
                if rec is not None and rec.evidence_hash:
                    data[str(k)] = rec
            except Exception:
                continue
        self._data = data
        self._loaded = True

    def save(self) -> None:
        payload = {
            "version": CANDIDATE_STORE_VERSION,
            "candidates": {k: v.to_dict() for k, v in sorted(self._data.items())},
        }
        _atomic_write_json(self.path, payload)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _acquire(self):
        try:
            import fcntl

            self.path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(self.path.parent / ".candidate_store.lock", "w")
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            return fh
        except Exception:
            return None

    @staticmethod
    def _release(fh) -> None:
        if fh is not None:
            try:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                fh.close()
            except Exception:
                pass

    def _merge_disk(self) -> None:
        """Under lock, fold disk entries into memory (missing key or newer timestamp)."""
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            candidates_raw = raw.get("candidates", {}) if isinstance(raw, dict) else {}
        except Exception:
            return
        for k, v in (candidates_raw or {}).items():
            try:
                disk_rec = CandidateRecord.from_dict(v)
            except Exception:
                continue
            if disk_rec is None or not disk_rec.evidence_hash:
                continue
            k = str(k)
            mem_rec = self._data.get(k)
            if mem_rec is None:
                self._data[k] = disk_rec
            else:
                if (disk_rec.last_updated or "") > (mem_rec.last_updated or ""):
                    self._data[k] = disk_rec

    def get(self, store_key: str) -> CandidateRecord | None:
        self._ensure_loaded()
        key = normalize_store_key(store_key)
        if not key:
            return None
        return self._data.get(key)

    def put(self, store_key: str, record: CandidateRecord) -> None:
        self._ensure_loaded()
        key = normalize_store_key(store_key)
        if not key:
            return
        if record.last_updated is None:
            record.last_updated = datetime.now(UTC).isoformat()
        fh = self._acquire()
        try:
            self._merge_disk()
            self._data[key] = record
            self.save()
        finally:
            self._release(fh)

    def delete(self, store_key: str) -> bool:
        self._ensure_loaded()
        key = normalize_store_key(store_key)
        fh = self._acquire()
        try:
            self._merge_disk()
            if key in self._data:
                del self._data[key]
                self.save()
                return True
            return False
        finally:
            self._release(fh)

    def keys(self) -> list[str]:
        self._ensure_loaded()
        return sorted(self._data.keys())

    def size(self) -> int:
        self._ensure_loaded()
        return len(self._data)

    def __len__(self) -> int:
        return self.size()

    def __contains__(self, store_key: str) -> bool:
        return self.get(store_key) is not None
