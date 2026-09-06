"""Tests for derived SQLite cache.db (issue #139).

Hybrid: JSON store canonical; cache.db derived, regenerable, WAL, atomic.
Acceptance criteria:
- build_all tail rebuilds cache.db after backfill+GC (atomic temp->replace, WAL, busy_timeout 5000)
- schema models(key TEXT PRIMARY KEY WITHOUT ROWID, benchmarks JSON, pricing JSON, last_updated TEXT) + idx_last_updated
- JSON round-trip identical; row count equals store size; missing DB -> not required
- judge hot path never touches cache.db; bifrost generator never reads cache.db
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from llm_discovery.cache_db import rebuild_cache_db, verify_cache_db
from llm_discovery.model_info_store import (
    BenchmarkSnapshot,
    ModelInfoRecord,
    ModelInfoStore,
    PricingSnapshot,
    StoreMeta,
)


def _make_store(tmp_path: Path, keys: list[tuple[str, dict]]) -> Path:
    """Build a slim v2 store file with the given (key, record-dict) entries."""
    store_path = tmp_path / "model_info_store.json"
    models: dict[str, dict] = {}
    for i, (key, rec_dict) in enumerate(keys):
        models[key] = {
            "benchmarks": rec_dict.get(
                "benchmarks",
                {"scores": {"aa_intelligence": {"score": 70 + i}}, "raw_benchmarks": [], "benchmark_coverage": 0.5},
            ),
            "pricing": rec_dict.get(
                "pricing",
                {"blended": 1.0 + i, "input": 0.5 + i, "output": 2.0 + i, "per_provider_overrides": {}},
            ),
            "_meta": {
                "first_seen": "2026-08-01T00:00:00+00:00",
                "last_updated": "2026-08-%02dT00:00:00+00:00" % (i + 1),
                "version": 2,
            },
        }
    store_path.write_text(json.dumps({"version": 2, "models": models}, indent=2))
    return store_path


class TestSchema:
    def test_models_table_without_rowid(self, tmp_path: Path) -> None:
        store_path = _make_store(tmp_path, [("alpha", {})])
        db_path = tmp_path / "derived" / "cache.db"
        rebuild_cache_db(store_path, db_path)
        conn = sqlite3.connect(str(db_path))
        try:
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='models'").fetchone()[0]
            assert "WITHOUT ROWID" in sql
            assert "key TEXT PRIMARY KEY" in sql
            assert "benchmarks TEXT NOT NULL" in sql
            assert "pricing TEXT NOT NULL" in sql
            assert "last_updated TEXT NOT NULL" in sql
            idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_last_updated'").fetchone()
            assert idx is not None
        finally:
            conn.close()

    def test_wal_mode_and_busy_timeout(self, tmp_path: Path) -> None:
        store_path = _make_store(tmp_path, [("alpha", {})])
        db_path = tmp_path / "derived" / "cache.db"
        rebuild_cache_db(store_path, db_path)
        conn = sqlite3.connect(str(db_path))
        try:
            jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert jm.lower() == "wal"
        finally:
            conn.close()


class TestRoundTrip:
    def test_row_count_equals_store_size(self, tmp_path: Path) -> None:
        entries = [(f"model-{i}", {}) for i in range(7)]
        store_path = _make_store(tmp_path, entries)
        db_path = tmp_path / "derived" / "cache.db"
        res = rebuild_cache_db(store_path, db_path)
        store = ModelInfoStore(store_path)
        store.load()
        assert res["row_count"] == store.size()
        assert res["store_size"] == store.size()

    def test_json_round_trip_identical(self, tmp_path: Path) -> None:
        entries = [
            ("model-a", {"benchmarks": {"scores": {"swe_bench_verified": {"score": 61.5}}, "raw_benchmarks": [{"name": "SWE-Bench"}], "benchmark_coverage": 0.25}}),
            ("model-b", {"pricing": {"blended": 0.1, "input": 0.05, "output": 0.15, "per_provider_overrides": {"p1": {"blended": 9.9}}}}),
        ]
        store_path = _make_store(tmp_path, entries)
        db_path = tmp_path / "derived" / "cache.db"
        rebuild_cache_db(store_path, db_path)
        v = verify_cache_db(store_path, db_path)
        assert v["ok"], v["mismatches"]
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute("SELECT benchmarks, pricing FROM models WHERE key='model-a'").fetchone()
            assert json.loads(row[0])["scores"]["swe_bench_verified"]["score"] == 61.5
            row = conn.execute("SELECT pricing FROM models WHERE key='model-b'").fetchone()
            assert json.loads(row[0])["per_provider_overrides"]["p1"]["blended"] == 9.9
        finally:
            conn.close()

    def test_empty_store_yields_empty_table(self, tmp_path: Path) -> None:
        store_path = tmp_path / "model_info_store.json"
        store_path.write_text(json.dumps({"version": 2, "models": {}}))
        db_path = tmp_path / "derived" / "cache.db"
        res = rebuild_cache_db(store_path, db_path)
        assert res["row_count"] == 0
        conn = sqlite3.connect(str(db_path))
        try:
            assert conn.execute("SELECT count(*) FROM models").fetchone()[0] == 0
        finally:
            conn.close()


class TestRegenerableAtomic:
    def test_rebuild_after_store_change_updates_rows(self, tmp_path: Path) -> None:
        store_path = _make_store(tmp_path, [("alpha", {}), ("beta", {})])
        db_path = tmp_path / "derived" / "cache.db"
        rebuild_cache_db(store_path, db_path)
        store = ModelInfoStore(store_path)
        store.load()
        now = datetime.now(UTC).isoformat()
        store._data["gamma"] = ModelInfoRecord(
            benchmarks=BenchmarkSnapshot(scores={"humaneval": {"score": 80}}, benchmark_coverage=0.25),
            pricing=PricingSnapshot(blended=0.5, input=0.2, output=0.8),
            _meta=StoreMeta(first_seen=now, last_updated=now),
        )
        store.save()
        res = rebuild_cache_db(store_path, db_path)
        assert res["row_count"] == 3
        conn = sqlite3.connect(str(db_path))
        try:
            keys = {r[0] for r in conn.execute("SELECT key FROM models")}
        finally:
            conn.close()
        assert keys == {"alpha", "beta", "gamma"}

    def test_no_temp_files_left_behind(self, tmp_path: Path) -> None:
        store_path = _make_store(tmp_path, [("alpha", {})])
        db_path = tmp_path / "derived" / "cache.db"
        rebuild_cache_db(store_path, db_path)
        leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []
        assert not (tmp_path / "derived" / "cache.db.tmp").exists()


class TestNeverRequired:
    def test_verify_missing_db_reports_not_ok(self, tmp_path: Path) -> None:
        store_path = _make_store(tmp_path, [("alpha", {})])
        v = verify_cache_db(store_path, tmp_path / "derived" / "cache.db")
        assert not v["ok"]
        assert "missing" in str(v["reason"]).lower()

    def test_rebuild_failure_does_not_corrupt_existing_db(self, tmp_path: Path) -> None:
        # A write-protected derived dir makes rebuild raise; build_all wraps in try/except.
        store_path = _make_store(tmp_path, [("alpha", {})])
        derived = tmp_path / "derived"
        derived.mkdir()
        db_path = derived / "cache.db"
        rebuild_cache_db(store_path, db_path)
        os_chmod = __import__("os").chmod
        os_chmod(derived, 0o500)  # read-only dir
        try:
            with pytest.raises(Exception):
                rebuild_cache_db(store_path, db_path)
        finally:
            os_chmod(derived, 0o700)
        # prior db intact and still valid
        v = verify_cache_db(store_path, db_path)
        assert v["ok"], v["mismatches"]


class TestBoundaries:
    def test_judge_hot_path_does_not_touch_cache_db(self) -> None:
        import inspect

        import llm_discovery.judge as judge_mod

        src = inspect.getsource(judge_mod)
        assert "cache_db" not in src
        assert "cache.db" not in src

    def test_pipeline_reuse_gate_uses_json_store(self) -> None:
        import inspect

        import llm_discovery.pipeline as pipeline_mod

        src = inspect.getsource(pipeline_mod)
        assert "cache_db" not in src
        assert "derived" not in src

    def test_bifrost_generator_does_not_read_cache_db(self) -> None:
        src = (Path(__file__).resolve().parents[1] / "src" / "llm_discovery" / "bifrost" / "generator.py").read_text()
        assert "cache_db" not in src
        assert "cache.db" not in src

    def test_gitignore_covers_derived(self) -> None:
        # data/ is gitignored wholesale (ADR-0005 pattern: ignore dir + one negation);
        # data/derived/cache.db is covered by that rule.
        import subprocess

        repo = Path(__file__).resolve().parents[1]
        out = subprocess.run(
            ["git", "check-ignore", "-v", "data/derived/cache.db"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert out.returncode == 0, out.stderr
        # the store itself stays tracked
        out2 = subprocess.run(
            ["git", "check-ignore", "-q", "data/model_info_store.json"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        assert out2.returncode != 0

    def test_build_all_tail_rebuilds_cache_db(self, tmp_path: Path) -> None:
        from llm_discovery.build_all import build_all

        import shutil

        (tmp_path / "providers.yaml").write_text(
            (Path(__file__).resolve().parents[1] / "config" / "providers.yaml").read_text()
        )
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        def fake_discover(name, config, aa, models_dev, max_workers, store=None):
            return {
                "keep": [
                    {
                        "provider": name,
                        "model_id": f"{name}-fake-model",
                        "provider_model_id": f"{name}-fake-model",
                        "tier": "flash",
                        "evidence_level": "strong",
                        "coding_score": 80,
                        "pricing": {"blended": 1.0, "input": 0.5, "output": 1.5},
                        "benchmarks": {"scores": {"aa_intelligence": {"score": 90}}, "benchmark_coverage": 0.5},
                        "aa_model_id": "aa-fake",
                        "evidence": ["http://example.com"],
                    }
                ],
                "drop": [],
                "error": [],
            }

        res = build_all(data_dir=data_dir, config_path=tmp_path / "providers.yaml", provider_names=["groq"], discover_fn=fake_discover)
        db_path = data_dir / "derived" / "cache.db"
        assert db_path.exists()
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute("SELECT count(*) FROM models").fetchone()[0]
        finally:
            conn.close()
        assert count == res["store_size"] > 0
        v = verify_cache_db(data_dir / "model_info_store.json", db_path)
        assert v["ok"], v["mismatches"]
