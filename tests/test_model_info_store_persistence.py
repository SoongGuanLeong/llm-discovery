"""Persistence mechanics for model_info_store (issue #68) — slim v2."""
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
        rec = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 50}}), pricing=PricingSnapshot(blended=0.5), _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
        store.put("my-model", rec)
        raw = json.loads(p.read_text())
        assert raw["version"] == STORE_FILE_VERSION
        assert "models" in raw
        assert "my-model" in raw["models"]

    def test_atomic_write_no_partial(self, tmp_path):
        p = tmp_path / "sub" / "store.json"
        store = ModelInfoStore(p)
        rec = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 10}}), _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
        store.put("k1", rec)
        rec2 = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 20}}), _meta=StoreMeta(last_updated="2026-09-04T06:00:00+00:00"))
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
        rec = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 55}}), _meta=StoreMeta(last_updated=old))
        store.put("old-model", rec)
        assert store.get_if_fresh("old-model") is not None
        assert store.get_if_fresh("old-model", ttl_days=None) is not None
        assert store.get_if_fresh("old-model", ttl_days=90) is None
        assert store.get_if_fresh("old-model", ttl_days=300) is not None

    def test_merge_keeps_freshness(self, tmp_path):
        p = tmp_path / "s.json"
        store = ModelInfoStore(p)
        r1 = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 50}}), _meta=StoreMeta(first_seen="2026-09-04T01:00:00+00:00", last_updated="2026-09-04T01:00:00+00:00"))
        r2 = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 60}}), _meta=StoreMeta(first_seen="2026-09-04T02:00:00+00:00", last_updated="2026-09-04T02:00:00+00:00"))
        store.put("m", r1)
        store.put("m", r2)
        got = store.get_by_key("m")
        assert got._meta.first_seen == "2026-09-04T01:00:00+00:00"
        assert got._meta.last_updated == "2026-09-04T02:00:00+00:00"
        assert got._meta.version == 2


class TestConcurrency:
    def test_put_serializes_with_atomic_replace(self, tmp_path):
        p = tmp_path / "s.json"
        store = ModelInfoStore(p)
        for i in range(5):
            rec = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": float(i)}}), _meta=StoreMeta(last_updated=f"2026-09-04T0{i}:00:00+00:00"))
            store.put(f"k{i}", rec)
        assert store.size() == 5
        raw = json.loads(p.read_text())
        assert len(raw["models"]) == 5
        assert not list(p.parent.glob(".tmp-store-*"))

    def test_threadpool_puts(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor
        p = tmp_path / "s.json"
        def put_one(i):
            s = ModelInfoStore(p)
            rec = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": float(i)}}), _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
            s.put(f"model-{i % 3}", rec)
            return True
        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(put_one, range(10)))
        final = ModelInfoStore(p)
        assert final.size() == 3


class TestVersioning:
    def test_load_version_wrapped(self, tmp_path):
        p = tmp_path / "s.json"
        payload = {"version": 1, "models": {"k": {"benchmarks": {"scores": {"a": {"score": 10}}, "raw_benchmarks": []}, "pricing": {"per_provider_overrides": {}}, "_meta": {"version": 1, "first_seen": "2026-09-04T01:00:00+00:00", "last_updated": "2026-09-04T01:00:00+00:00"}}}}
        p.write_text(json.dumps(payload))
        s = ModelInfoStore(p)
        assert s.get_by_key("k").benchmarks.scores["a"]["score"] == 10
        assert s._file_version == 1

    def test_load_legacy_bare_dict(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"k": {"benchmarks": {"scores": {"a": {"score": 10}}, "raw_benchmarks": []}, "pricing": {"per_provider_overrides": {}}, "_meta": {"version": 1}}}))
        s = ModelInfoStore(p)
        assert s.get_by_key("k").benchmarks.scores["a"]["score"] == 10

    def test_load_missing_is_empty(self, tmp_path):
        s = ModelInfoStore(tmp_path / "nope.json")
        s.load()
        assert s.size() == 0


class TestReadPath:
    def test_lazy_load_on_get(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text(json.dumps({"version": 2, "models": {"gpt-4o": {"benchmarks": {"scores": {"a": {"score": 99}}, "raw_benchmarks": []}, "pricing": {"per_provider_overrides": {}}, "_meta": {"last_updated": "2026-09-04T05:00:00+00:00", "first_seen": "2026-09-04T05:00:00+00:00", "version": 2}}}}))
        s = ModelInfoStore(p)
        assert s._loaded is False
        assert s.get("gpt-4o").benchmarks.scores["a"]["score"] == 99
        assert s._loaded is True

    def test_normalized_lookup(self, tmp_path):
        p = tmp_path / "s.json"
        s = ModelInfoStore(p)
        rec = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 10}}), _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
        s.put("gpt-4o", rec)
        assert s.get("openai/gpt-4o:free") is not None
        assert s.get("GPT-4O") is not None
        assert s.get("groq/gpt-4o") is not None

    def test_slim_not_cached_weak(self, tmp_path):
        # slim store accepts any put (gate at pipeline), but ensure slim shape persists
        p = tmp_path / "s.json"
        s = ModelInfoStore(p)
        rec = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 10}}), _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
        s.put("weak", rec)
        assert s.size() == 1

    def test_contains_and_keys(self, tmp_path):
        p = tmp_path / "s.json"
        s = ModelInfoStore(p)
        rec = ModelInfoRecord(benchmarks=BenchmarkSnapshot(scores={"a": {"score": 1}}), _meta=StoreMeta(last_updated="2026-09-04T05:00:00+00:00"))
        s.put("b", rec)
        s.put("a", rec)
        assert "a" in s
        assert "openai/a:free" in s
        assert s.keys() == ["a", "b"]
        assert len(s) == 2

    def test_upsert_from_provider_record(self, tmp_path):
        p = tmp_path / "s.json"
        s = ModelInfoStore(p)
        prov = {"provider_model_id": "muse-spark-1.2", "benchmarks": {"scores": {}}, "pricing": {}}
        ok = s.upsert_from_provider_record("muse-spark-1.2-contributor:free", prov, provider="groq")
        assert ok is True
        assert s.get("muse-spark-1.2-contributor") is not None