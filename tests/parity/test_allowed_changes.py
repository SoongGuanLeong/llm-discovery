"""Meta-gate for the allowed-change registry (ADR 0010 #11)."""
from __future__ import annotations

from tests.parity.allowed_changes import ALLOWED, validate_anchors

VALID_COMMANDS = {
    "discover", "build", "refresh", "export.dry-run", "export.apply",
    "config.set-key", "providers.list", "doctor", "*",
}
VALID_ASPECTS = {"stdout", "stderr", "exit_code", "flag", "artifact", "env_var"}


def test_anchors_resolve_to_adr_headings():
    bad = validate_anchors()
    assert bad == [], f"anchors missing from ADR 0010: {bad}"


def test_registry_vocab_is_closed():
    for entry in ALLOWED:
        assert entry.command in VALID_COMMANDS, entry
        assert entry.aspect in VALID_ASPECTS, entry
        assert entry.adr.startswith("ADR-0010#"), entry
        assert entry.old and entry.new, entry


def test_no_duplicate_entries():
    keys = [(e.command, e.aspect, e.old, e.new) for e in ALLOWED]
    assert len(keys) == len(set(keys))
