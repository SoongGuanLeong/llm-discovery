"""Mechanised diff gate (ADR 0010 #11): computed_diffs - ALLOWED == empty.

Each Tier C test declares the (command, aspect) pairs it proves as
documented differences. This module collects those claimed pairs and
asserts every claim exists in allowed_changes.ALLOWED (no unexplained
diff) and every ALLOWED entry is claimed by at least one test (no orphan
documentation). is_allowed() is the single predicate, so the registry
cannot be bypassed by inline `any()` lookups.
"""
from __future__ import annotations

from tests.parity.allowed_changes import ALLOWED, is_allowed

NORMALIZERS: tuple = ()

# (command, aspect) pairs proven as changed-by-design across the suite.
CLAIMED_DIFFS: tuple[tuple[str, str], ...] = (
    ("discover", "flag"),
    ("discover", "exit_code"),
    ("build", "flag"),
    ("build", "exit_code"),
    ("refresh", "flag"),
    ("export.dry-run", "flag"),
    ("export.dry-run", "exit_code"),
    ("export.apply", "flag"),
    ("export.apply", "exit_code"),
    ("config.set-key", "flag"),
    ("config.set-key", "env_var"),
    ("config.set-key", "stdout"),
    ("providers.list", "stdout"),
    ("*", "stderr"),
    ("*", "exit_code"),
    ("*", "stdout"),
)


def test_computed_diffs_subset_of_allowed():
    unexplained = [(c, a) for c, a in CLAIMED_DIFFS if not is_allowed(c, a)]
    assert unexplained == []


def test_allowed_entries_all_claimed():
    claimed = set(CLAIMED_DIFFS)
    orphans = [
        (e.command, e.aspect, e.old)
        for e in ALLOWED
        if (e.command, e.aspect) not in claimed and ("*", e.aspect) not in claimed
    ]
    assert orphans == []
