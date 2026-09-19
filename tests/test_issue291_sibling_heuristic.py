"""Issue #291 — unify the sibling heuristic (phase 4 of #285); issue #294 —
single-component versions.

The characterization baseline below was recorded, before unification, from the
two implementations this change collapses:

  * ``pipeline._batch_has_older_kept_sibling`` — the same-batch post-pass
  * ``policy_gate._has_older_kept_sibling``    — the store/gate pass

Both now go through ``llm_discovery.sibling``. The corpus is the record that
the unification is not a silent behaviour change.

#294 then deliberately changes two of those answers (single-component
signature versions now parse) and adds coverage for single-component ids and
the ``step-*`` context-length family. ``BEHAVIOUR_DELTA`` names the changed
rows; the rest of the historical corpus is asserted unchanged, and no row may
regress from ``True`` to ``False``.
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
# Every row agrees except the three parameter-size rows marked DIVERGE: there
# the batch copy took the first numeric run in the raw id (e.g. "3.3-70" for
# llama-3.3-70b), which mistakes a parameter size for a version component,
# while the store copy parsed the signature version ("3.3"). #291 reconciles
# the divergence to the store/gate answer — a larger-parameter sibling of the
# same version is not an *older* version.
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

# #294 — the rows whose answer changes versus the historical record above, and
# their new answers. Single-component signature versions now parse, so a
# single-component keeper/version participates in the comparison. Only rows
# where the shared prefix already differs flip; the mixed-format ``gpt-4.1`` /
# ``gpt-4`` row keeps its historical ``False`` (the shared prefix is equal).
BEHAVIOUR_DELTA = {
    ("phi-4", "phi-3.5"): True,  # (4,) vs (3, 5): shared prefix 4 > 3
    ("o3-mini", "o1-mini"): True,  # (3,) vs (1,): same family "o", variant "mini"
}

# Rows #294 adds: single-component ids, the ``step-*`` context-length family,
# and the mixed-arity rule. (candidate, keeper, expected).
ADDITIONAL_CORPUS = [
    ("gpt-5", "gpt-4", True),  # single-component keeper now parses
    ("gpt-4", "gpt-5", False),
    ("gpt-4.1", "gpt-4", False),  # mixed-arity: shared prefix (4,) == (4,)
    ("phi-3.5", "phi-4", False),
    ("step-2-16k", "step-1-8k", True),  # context-length family, no regression
    ("step-1-8k", "step-2-16k", False),
    ("claude-sonnet-4-5", "claude-sonnet-4", False),  # keeper has no version
    ("mistral-7b", "mistral-8x7b", False),  # raw-id fallback stays narrow
    ("mistral-8x7b", "mistral-7b", False),
    ("llama-3.3-70b", "llama-3.3-8b", False),  # parameter size not a version
    ("gpt-5", "gpt-5", False),
]


def test_characterization_corpus_covers_the_divergence():
    """The record must exercise the divergence, or reconciling it is vacuous."""
    assert any(store != batch for _, _, store, batch in CHARACTERIZATION_CORPUS)


def test_behaviour_delta_is_exactly_the_single_component_rows():
    """#294 changes these answers and no others."""
    from llm_discovery import sibling

    changed = {
        (candidate, keeper)
        for candidate, keeper, store_answer, _batch in CHARACTERIZATION_CORPUS
        if sibling.has_older_kept_sibling(candidate, [keeper]) is not store_answer
    }
    assert changed == set(BEHAVIOUR_DELTA)


def test_no_historical_row_regresses_from_true_to_false():
    """A row the gate used to promote must still be promoted (#294 criterion)."""
    from llm_discovery import sibling

    for candidate, keeper, store_answer, _batch in CHARACTERIZATION_CORPUS:
        if store_answer:
            assert sibling.has_older_kept_sibling(candidate, [keeper]) is True, (candidate, keeper)


def test_contract_holds_on_the_historical_corpus():
    """Historical answers, overridden by the stated #294 delta."""
    from llm_discovery import sibling

    for candidate, keeper, store_answer, _batch in CHARACTERIZATION_CORPUS:
        expected = BEHAVIOUR_DELTA.get((candidate, keeper), store_answer)
        got = sibling.has_older_kept_sibling(candidate, [keeper])
        assert got is expected, (candidate, keeper, got, expected)


def test_additional_corpus_rows():
    """Single-component ids, the step-* family, and the mixed-arity rule."""
    from llm_discovery import sibling

    for candidate, keeper, expected in ADDITIONAL_CORPUS:
        got = sibling.has_older_kept_sibling(candidate, [keeper])
        assert got is expected, (candidate, keeper, got, expected)


def test_batch_adapter_reconciles_the_parameter_size_divergence():
    """The same-batch adapter now returns the gate answer on the DIVERGE rows."""
    from llm_discovery import sibling

    for candidate, keeper, store_answer, batch_answer in CHARACTERIZATION_CORPUS:
        if store_answer == batch_answer:
            continue
        got = sibling.batch_has_older_kept_sibling(candidate, [{"provider_model_id": keeper}])
        assert got is store_answer, (candidate, keeper, got, store_answer)


def test_version_tuple_reconciles_the_two_parsers():
    """One stated parser behaviour: a non-empty non-numeric part contributes 0.

    The former ``pipeline._ver_tuple`` skipped such parts (so "2.5-flash" was
    (2, 5)); the former ``policy_gate._version_tuple`` appended 0. This pins the
    chosen behaviour explicitly rather than leaving it implicit.
    """
    from llm_discovery.sibling import version_tuple

    assert version_tuple("") == ()
    assert version_tuple("2.5") == (2, 5)
    assert version_tuple("2.5-3") == (2, 5, 3)
    assert version_tuple("2--5") == (2, 5)
    # non-numeric parts contribute 0 (the reconciled behaviour)
    assert version_tuple("2.5-flash") == (2, 5, 0)
    assert version_tuple("v3.0") == (0, 0)
    assert version_tuple("abc") == (0,)
    assert version_tuple("1.0-rc1") == (1, 0, 0)


def test_batch_adapter_reads_provider_model_id_then_model_id():
    from llm_discovery.sibling import batch_has_older_kept_sibling

    keep = [{"model_id": "agnes-2.5-flash"}]
    assert batch_has_older_kept_sibling("agnes-3.0-flash", keep) is True
    keep = [{"provider_model_id": "agnes-2.5-flash", "model_id": "wrong"}]
    assert batch_has_older_kept_sibling("agnes-3.0-flash", keep) is True


def test_store_adapter_only_counts_kept_records():
    from llm_discovery.sibling import store_has_older_kept_sibling

    store = _KeepStore(["agnes-2.5-flash"])
    assert store_has_older_kept_sibling("agnes-3.0-flash", store) is True
    store._data["agnes-2.5-flash"]["judge"]["decision"] = "drop"
    assert store_has_older_kept_sibling("agnes-3.0-flash", store) is False


def test_store_adapter_reads_judgement_before_legacy_judge():
    """Canonical ``judgement`` is read first; ``judge`` and a top-level
    ``decision`` are fallbacks for older record shapes. Both object and
    dictionary records are supported."""
    from types import SimpleNamespace

    from llm_discovery.sibling import store_has_older_kept_sibling

    class _Store:
        def __init__(self, data):
            self._data = data

        def _ensure_loaded(self):
            pass

    # new shape: judgement only, no legacy judge snapshot
    store = _Store({"agnes-2.5-flash": {"judgement": {"decision": "keep"}}})
    assert store_has_older_kept_sibling("agnes-3.0-flash", store) is True

    # canonical judgement wins over the legacy judge snapshot
    store = _Store({"agnes-2.5-flash": {"judgement": {"decision": "drop"}, "judge": {"decision": "keep"}}})
    assert store_has_older_kept_sibling("agnes-3.0-flash", store) is False

    # legacy judge-only record still counts (older shapes)
    store = _Store({"agnes-2.5-flash": {"judge": {"decision": "keep"}}})
    assert store_has_older_kept_sibling("agnes-3.0-flash", store) is True

    # object records go through the same path
    store = _Store({"agnes-2.5-flash": SimpleNamespace(judgement=SimpleNamespace(decision="keep"))})
    assert store_has_older_kept_sibling("agnes-3.0-flash", store) is True

    # top-level decision fallback when neither snapshot carries one
    store = _Store({"agnes-2.5-flash": {"decision": "keep"}})
    assert store_has_older_kept_sibling("agnes-3.0-flash", store) is True


def test_store_adapter_tolerates_missing_store_and_records():
    from llm_discovery.sibling import store_has_older_kept_sibling

    assert store_has_older_kept_sibling("agnes-3.0-flash", None) is False
    assert store_has_older_kept_sibling("agnes-3.0-flash", _KeepStore([])) is False


def test_predicate_false_without_parseable_version_or_base():
    from llm_discovery.sibling import has_older_kept_sibling

    assert has_older_kept_sibling("", ["agnes-2.5-flash"]) is False
    assert has_older_kept_sibling("just-a-name", ["agnes-2.5-flash"]) is False
    assert has_older_kept_sibling("agnes-3.0-flash", []) is False


def test_categorize_sibling_promotion_is_preserved():
    """The has_older_kept_sibling=True promotion to flash still happens."""
    from llm_discovery.categorize import categorize_model

    tier = categorize_model(
        coding=True,
        aa_score=None,
        coding_score=None,
        model_id="agnes-3.0-flash",
        has_older_kept_sibling=True,
    )
    assert tier == "flash"
