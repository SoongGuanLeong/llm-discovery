"""Issue #291 — unify the sibling heuristic (phase 4 of #285).

This file records, before unification, what the two pre-unification
implementations answer over a corpus of (candidate id, sibling id) pairs:

  * ``pipeline._batch_has_older_kept_sibling`` — the same-batch post-pass
  * ``policy_gate._has_older_kept_sibling``    — the store/gate pass

The recorded answers are the characterization baseline. After unification both
paths go through ``llm_discovery.sibling``; see the post-unification test below.
"""
from __future__ import annotations


class _KeepStore:
    """Minimal ModelInfoStore stand-in: one record per key, every one kept."""

    def __init__(self, keys):
        self._data = {k: {"judge": {"decision": "keep"}} for k in keys}

    def _ensure_loaded(self):
        pass


# (candidate_id, sibling_id, store_answer, batch_answer) recorded from the two
# pre-unification implementations.
#
# Every row agrees except the parameter-size rows marked DIVERGE: there the
# batch copy took the first numeric run in the raw id (e.g. "3.3-70" for
# llama-3.3-70b), which treats a parameter size as a version component, while
# the store copy parsed the signature version ("3.3"). This is the divergence
# #291 reconciles.
CHARACTERIZATION_CORPUS = [
    ("agnes-3.0-flash", "agnes-2.5-flash", True, True),
    ("agnes-2.5-flash", "agnes-3.0-flash", False, False),
    ("llama-3.3-70b", "llama-3.3-8b", False, True),  # DIVERGE (parameter size)
    ("qwen2.5-72b", "qwen2.5-7b", False, True),  # DIVERGE (parameter size)
    ("granite-3.2-8b", "granite-3.2-2b", False, True),  # DIVERGE (parameter size)
    ("gpt-4.1", "gpt-4", False, False),
    ("claude-sonnet-4-5", "claude-sonnet-4", False, False),
    ("gemini-2.5-flash", "gemini-2.0-flash", True, True),
    ("step-2-16k", "step-1-8k", True, True),
    ("command-r-plus-08-2024", "command-r-plus-04-2024", True, True),
    ("jamba-1.5-large", "jamba-1.0-large", True, True),
    ("deepseek-v3-0324", "deepseek-v3-0121", True, True),
    ("gpt-4.1", "gpt-4.1", False, False),
    ("gemini-2.5-flash", "gemini-2.5-pro", False, False),
    ("mistral-7b", "mistral-8x7b", False, False),
    ("glm-4.5", "glm-4.5-air", False, False),
    ("o3-mini", "o1-mini", False, False),
    ("phi-4", "phi-3.5", False, False),
]


def test_characterization_records_the_two_current_implementations():
    """Freeze the pre-unification answers of both sibling predicates."""
    from llm_discovery import pipeline, policy_gate

    for candidate, sibling, store_answer, batch_answer in CHARACTERIZATION_CORPUS:
        store_got = policy_gate._has_older_kept_sibling(candidate, _KeepStore([sibling]))
        batch_got = pipeline._batch_has_older_kept_sibling(candidate, [{"provider_model_id": sibling}])
        assert store_got is store_answer, (candidate, sibling, "store", store_got, store_answer)
        assert batch_got is batch_answer, (candidate, sibling, "batch", batch_got, batch_answer)


def test_characterization_corpus_has_a_divergence_to_reconcile():
    """The corpus must actually exercise the divergence, or the record is vacuous."""
    diverging = [(c, s) for c, s, st, ba in CHARACTERIZATION_CORPUS if st != ba]
    assert diverging, "corpus does not cover the parser divergence #291 reconciles"
