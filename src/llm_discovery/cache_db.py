"""Derived SQLite cache.db for DBeaver/SQL inspection (issue #139).

Hybrid strategy: the JSON store remains the Source of Truth (slim v2,
committed). This module generates the Derived Cache, a regenerable
SQLite file at data/derived/cache.db, at build_all tail. WAL-enabled,
atomic temp->replace, never required for correctness.

Background: docs/adr/0005-model-info-store-persistence.md rejects SQLite
as the store format; docs/research/issue-137-judge-seam.md adopts the
hybrid (JSON canonical, SQLite derived) for ad-hoc SQL inspection only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


CACHE_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS models(
  key TEXT PRIMARY KEY,
  benchmarks TEXT NOT NULL,
  pricing TEXT NOT NULL,
  last_updated TEXT NOT NULL
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_last_updated ON models(last_updated);
""".strip()


def _record_to_row(key: str, record: Any) -> tuple[str, str, str, str]:
    """Serialize one slim-v2 ModelInfoRecord into a models-table row.

    Uses ModelInfoRecord.to_dict(), the same shape persisted in the store
    file, so the JSON columns round-trip identical to the store.
    """
    data = record.to_dict()
    benchmarks_json = json.dumps(data["benchmarks"], ensure_ascii=False, sort_keys=True)
    pricing_json = json.dumps(data["pricing"], ensure_ascii=False, sort_keys=True)
    last_updated = data["_meta"].get("last_updated") or ""
    return key, benchmarks_json, pricing_json, last_updated


def rebuild_cache_db(
    store_path: str | Path = "data/model_info_store.json",
    db_path: str | Path = "data/derived/cache.db",
) -> dict[str, Any]:
    """Rebuild the derived cache.db from the JSON store, atomically.

    Builds a temp DB in the target directory (WAL, busy_timeout 5000),
    verifies row count equals store size, then os.replace over the target.
    Returns {"row_count", "store_size", "db_path"}. Raises on failure; the
    target is left untouched and build_all wraps the call warn-only.
    """
    from .model_info_store import ModelInfoStore

    store_path = Path(store_path)
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    store = ModelInfoStore(store_path)
    store.load()
    rows: list[tuple[str, str, str, str]] = []
    for key in store.keys():
        record = store.get_by_key(key)
        if record is not None:
            rows.append(_record_to_row(key, record))

    # Drop stale WAL side files from a previous generation BEFORE the
    # replace. The derived DB is disposable: if a live reader (DBeaver)
    # holds the old files, its open file descriptors stay valid (POSIX)
    # and it simply reconnects to the new file on next open. Leaving the
    # stale WAL in place would let the next opener replay foreign frames
    # into the new file.
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)

    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(db_path.parent), prefix=".cache-db-", suffix=".tmp")
        os.close(fd)
        tmp_path = Path(tmp_name)

        conn = sqlite3.connect(str(tmp_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.executescript(CACHE_DB_SCHEMA)
            if rows:
                conn.executemany(
                    "INSERT OR REPLACE INTO models(key, benchmarks, pricing, last_updated) VALUES (?,?,?,?)",
                    rows,
                )
            conn.commit()
            row_count = conn.execute("SELECT count(*) FROM models").fetchone()[0]
        finally:
            # Clean close checkpoints and removes the temp WAL side files.
            conn.close()

        if row_count != store.size():
            raise RuntimeError(f"cache.db row count {row_count} != store size {store.size()}")

        tmp_path.replace(db_path)
        return {"row_count": row_count, "store_size": store.size(), "db_path": str(db_path)}
    finally:
        # Success: tmp already replaced (no-op). Failure: remove leftovers.
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
            for suffix in ("-wal", "-shm"):
                Path(str(tmp_path) + suffix).unlink(missing_ok=True)


def verify_cache_db(
    store_path: str | Path = "data/model_info_store.json",
    db_path: str | Path = "data/derived/cache.db",
) -> dict[str, Any]:
    """Verify cache.db matches the store: row count, per-key JSON, last_updated.

    Byte-compares the stored JSON against the canonical serialization
    (_record_to_row), so any drift is reported. Returns
    {"ok", "mismatches", "row_count", "store_size"}.
    """
    from .model_info_store import ModelInfoStore

    store_path = Path(store_path)
    db_path = Path(db_path)
    store = ModelInfoStore(store_path)
    store.load()
    if not db_path.exists():
        return {
            "ok": False,
            "reason": "missing db",
            "mismatches": ["missing db"],
            "row_count": 0,
            "store_size": store.size(),
        }
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000;")
    try:
        db_rows = {
            row[0]: (row[1], row[2], row[3])
            for row in conn.execute("SELECT key, benchmarks, pricing, last_updated FROM models")
        }
    finally:
        conn.close()

    mismatches: list[str] = []
    for key in store.keys():
        record = store.get_by_key(key)
        if record is None:
            continue
        if key not in db_rows:
            mismatches.append(f"missing key {key}")
            continue
        _, expected_benchmarks, expected_pricing, expected_last_updated = _record_to_row(key, record)
        stored_benchmarks, stored_pricing, stored_last_updated = db_rows[key]
        if stored_benchmarks != expected_benchmarks:
            mismatches.append(f"benchmarks mismatch {key}")
        if stored_pricing != expected_pricing:
            mismatches.append(f"pricing mismatch {key}")
        if stored_last_updated != expected_last_updated:
            mismatches.append(f"last_updated mismatch {key}")
    extra_keys = set(db_rows) - set(store.keys())
    if extra_keys:
        mismatches.append(f"extra keys in db: {sorted(extra_keys)}")
    return {
        "ok": not mismatches,
        "mismatches": mismatches,
        "row_count": len(db_rows),
        "store_size": store.size(),
    }
