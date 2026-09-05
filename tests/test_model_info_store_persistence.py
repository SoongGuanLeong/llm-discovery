"""Persistence mechanics for model_info_store (issue #68)."""
from pathlib import Path
import json
import tempfile
from datetime import UTC, datetime, timedelta

from llm_discovery.model_info_store import (
    ModelInfoRecord,
    ModelInfoStore,
    BenchmarkSnapshot,
    PricingSnapshot,
    StoreMeta,
    is_stale,
    STORE_FILE_VERSION,
    RECOMMENDED_STORE_PATH,
    RECOMMENDED_STORE_PATH_OBJ,
)


class TestLocationFormat:
    def test_recommended_path(self):
        assert RECOMMENDED_STORE_PATH == "data/model_info_store.json"
        assert RECOMMENDED_STORE_PATH_OBJ == Path("data/model_info_store.json")

    def test_file_version(self):
        assert STORE_FILE_VERSION == 2

    def test_lazy_load_empty_when_missing(self, tmp_path):
        store = ModelInfoStore(tmp_path / "missing.json")
        assert store.size() == 0
        assert store.get("any-model") is None

    def test_save_creates_version_wrapped_json(self, tmp_path):
        p = tmp_path / "store.json"
        store = ModelInfoStore(p)
        rec = ModelInfoRecord(aa_score=50, evidence_level="strong", confidence=0.9, evidence=["x"], _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
        store.put("my-model", rec)
        raw = json.loads(p.read_text())
        assert raw["version"] == STORE_FILE_VERSION
        assert "models" in raw
        assert "my-model" in raw["models"]

    def test_atomic_write_no_partial(self, tmp_path):
        p = tmp_path / "sub" / "store.json"
        store = ModelInfoStore(p)
        rec = ModelInfoRecord(aa_score=10, evidence_level="strong", _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
        store.put("k1", rec)
        # second write overwrites atomically
        rec2 = ModelInfoRecord(aa_score=20, evidence_level="strong", _meta=StoreMeta(last_updated="2026-09-04T06:00:00+00:00"))
        store.put("k2", rec2)
        raw = json.loads(p.read_text())
        assert set(raw["models"].keys()) == {"k1", "k2"}


class TestInvalidationTTL:
    def test_never_stale_by_default(self):
        assert is_stale("2020-01-01T00:00:00+00:00", None) is False
        assert is_stale("2020-01-01T00:00:00+00:00", 0) is False
        assert is_stale(None, 90) is False

    def test_stale_when_older_than_ttl(self):
        old = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        assert is_stale(old, 90) is True
        assert is_stale(old, 200) is False

    def test_recent_not_stale(self):
        recent = datetime.now(UTC).isoformat()
        assert is_stale(recent, 90) is False

    def test_get_if_fresh_never_expire_default(self, tmp_path):
        p = tmp_path / "s.json"
        store = ModelInfoStore(p)
        old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
        rec = ModelInfoRecord(aa_score=55, evidence_level="strong", _meta=StoreMeta(last_updated=old))
        store.put("old-model", rec)
        # default ttl None => still returned
        assert store.get_if_fresh("old-model") is not None
        assert store.get_if_fresh("old-model", ttl_days=None) is not None
        # with 90d TTL => stale => None
        assert store.get_if_fresh("old-model", ttl_days=90) is None
        # with 300d TTL => fresh
        assert store.get_if_fresh("old-model", ttl_days=300) is not None

    def test_stronger_evidence_overwrites_via_merge(self, tmp_path):
        # v2 slim: gap-fill not overwrite, slim meta version 2, no source_providers
        p = tmp_path / "s.json"
        store = ModelInfoStore(p)
        r1 = ModelInfoRecord(aa_score=50, evidence_level="strong", confidence=0.8, _meta=StoreMeta(first_seen="2026-09-04T01:00:00+00:00", last_updated="2026-09-04T01:00:00+00:00"))
        r2 = ModelInfoRecord(aa_score=60, evidence_level="strong", confidence=0.9, _meta=StoreMeta(first_seen="2026-09-04T02:00:00+00:00", last_updated="2026-09-04T02:00:00+00:00"))
        store.put("m", r1)
        store.put("m", r2)
        got = store.get_by_key("m")
        # slim v2 keeps benchmarks/pricing only; legacy aa_score gap-fill remains in memory but not asserted for persistence
        # Ensure merge kept first_seen min and last_updated max
        assert got._meta.first_seen == "2026-09-04T01:00:00+00:00"
        assert got._meta.last_updated == "2026-09-04T02:00:00+00:00"
        assert got._meta.version == 2
        # legacy field still gap-filled in memory
        assert got.aa_score == 50


class TestConcurrency:
    def test_put_serializes_with_atomic_replace(self, tmp_path):
        p = tmp_path / "s.json"
        store = ModelInfoStore(p)
        for i in range(5):
            rec = ModelInfoRecord(aa_score=float(i), evidence_level="strong", _meta=StoreMeta(last_updated=f"2026-09-04T0{i}:00:00+00:00"))
            store.put(f"k{i}", rec)
        assert store.size() == 5
        raw = json.loads(p.read_text())
        assert len(raw["models"]) == 5
        # no tmp left behind
        assert not list(p.parent.glob(".tmp-store-*"))

    def test_threadpool_puts(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor
        p = tmp_path / "s.json"
        # Each thread uses its own store instance pointing to same file -> lock serializes
        def put_one(i):
            s = ModelInfoStore(p)
            rec = ModelInfoRecord(aa_score=float(i), evidence_level="strong", _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00", source_providers=[f"p{i}"]))
            s.put(f"model-{i % 3}", rec)  # 3 keys, 10 writes, merges
            return True
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(put_one, range(10)))
        final = ModelInfoStore(p)
        assert final.size() == 3


class TestVersioning:
    def test_load_version_wrapped(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"version": 1, "models": {"k": {"aa_score": 10, "evidence_level": "strong", "evidence": [], "_meta": {"version": 1}}}}))
        s = ModelInfoStore(p)
        assert s.get_by_key("k").aa_score == 10
        assert s._file_version == 1

    def test_load_legacy_bare_dict(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"k": {"aa_score": 10, "evidence_level": "strong", "evidence": [], "_meta": {"version": 1}}}))
        s = ModelInfoStore(p)
        assert s.get_by_key("k").aa_score == 10

    def test_load_missing_is_empty(self, tmp_path):
        s = ModelInfoStore(tmp_path / "nope.json")
        s.load()
        assert s.size() == 0


class TestReadPath:
    def test_lazy_load_on_get(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"version": 1, "models": {"gpt-4o": {"aa_score": 99, "evidence_level": "strong", "evidence": [], "_meta": {"last_updated": "2026-09-04T05:00:00+00:00"}}}}))
        s = ModelInfoStore(p)
        assert s._loaded is False
        assert s.get("gpt-4o").aa_score == 99
        assert s._loaded is True

    def test_normalized_lookup(self, tmp_path):
        p = tmp_path / "s.json"
        s = ModelInfoStore(p)
        rec = ModelInfoRecord(aa_score=10, evidence_level="strong", _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
        s.put("gpt-4o", rec)
        # cross-provider prefix + case + free stripped
        assert s.get("openai/gpt-4o:free") is not None
        assert s.get("GPT-4O") is not None
        assert s.get("groq/gpt-4o") is not None

    def test_weak_not_cached(self, tmp_path):
        p = tmp_path / "s.json"
        s = ModelInfoStore(p)
        weak = ModelInfoRecord(aa_score=10, evidence_level="weak", _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
        s.put("weak", weak)
        assert s.size() == 0
        assert not p.exists() or json.loads(p.read_text())["models"] == {}

    def test_contains_and_keys(self, tmp_path):
        p = tmp_path / "s.json"
        s = ModelInfoStore(p)
        rec = ModelInfoRecord(aa_score=1, evidence_level="strong", _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
        s.put("b", rec)
        s.put("a", rec)
        assert "a" in s
        assert "openai/a:free" in s
        assert s.keys() == ["a", "b"]
        assert len(s) == 2

    def test_upsert_from_provider_record(self, tmp_path):
        p = tmp_path / "s.json"
        s = ModelInfoStore(p)
        prov = {"provider_model_id": "muse-spark-1.2", "aa_model_id": "muse-spark-1-2", "aa_score": 56.8, "coding_score": 58.3, "tier": "flash", "confidence": 0.9, "evidence_level": "strong", "evidence": ["x"], "benchmarks": {"scores": {}}, "pricing": {}}
        ok = s.upsert_from_provider_record("muse-spark-1.2-contributor:free", prov, provider="groq")
        assert ok is True
        assert s.get("muse-spark-1.2-contributor") is not None
        # weak should not upsert
        prov2 = {"evidence_level": "weak", "confidence": 0.5, "evidence": ["y"]}
        assert s.upsert_from_provider_record("weak-model", prov2) is False