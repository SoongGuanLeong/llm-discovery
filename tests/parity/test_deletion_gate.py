"""Deletion gate shape test (#272 delivers list; #273 deletes)."""
from __future__ import annotations

from pathlib import Path

from tests.parity.deletable import DELETABLE, UI_COUPLED_TESTS


def test_coupled_files_exist_and_import_ui_server():
    for rel in UI_COUPLED_TESTS:
        text = (Path.cwd() / rel).read_text(encoding="utf-8")
        assert "ui.server" in text, rel


def test_deletable_list_covers_issue_scope():
    assert "tests/test_186_key_providers.py" in DELETABLE
    for name in ("test_187_sse_dryrun.py", "test_188_apply.py", "test_189_build.py", "test_190_open_guide.py"):
        assert f"tests/ui/{name}" in DELETABLE
