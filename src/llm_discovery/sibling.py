"""Sibling heuristic — one version parser, one older-kept-sibling predicate.

Phase 4 of #285 / issue #291. The predicate answers "is *model_id* a newer
version of the same family/variant as some already-kept model?". Two adapters
feed it an iterable of keeper ids:

  * :func:`batch_has_older_kept_sibling` — keeper ids from evaluation records in
    ``result["keep"]`` (the same-batch pass in ``pipeline.discover_provider``).
  * :func:`store_has_older_kept_sibling` — keeper ids from a ``ModelInfoStore``
    (the store pass in ``policy_gate``).

Version-parser reconciliation (#291)
------------------------------------
The two pre-unification parsers disagreed on non-numeric parts:
``policy_gate._version_tuple`` appended ``0`` for a non-empty non-numeric part;
``pipeline._ver_tuple`` skipped it. This module keeps the append-``0``
behaviour — a non-empty non-numeric part contributes ``0`` — because it is the
more defensive contract (any non-empty version string yields a non-empty
tuple). Both call sites only ever pass the output of :func:`_numeric_version`,
which is digits and ``.``/``-`` separators only, so the two behaviours were
indistinguishable from the predicate; the choice fixes this module's standalone
contract and is pinned by
``test_version_tuple_reconciles_the_two_parsers``.

The candidate's version is taken from its signature first, then the raw id —
the store/gate semantics. Unifying on that order also removes a divergence the
characterization test surfaced: the old inline same-batch copy took the first
numeric run in the raw id, so it counted a parameter size as a version and
promoted e.g. ``llama-3.3-70b`` when ``llama-3.3-8b`` was kept. The store/gate
path is unchanged (verified over the real store); the characterization test
records this as the one reconciled difference.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

from .model_matching import ModelNormalizer

# A numeric version run: one or more digit groups joined by '.' or '-'
# (e.g. "3.3", "3.3-70", "08-2024").
_NUM_VERSION_RE = re.compile(r"\d+(?:[\.\-]\d+)+")


def _numeric_version(text: str) -> str:
    """First numeric version run in *text*, or "" when there is none."""
    m = _NUM_VERSION_RE.search(text or "")
    return m.group(0) if m else ""


def version_tuple(version: str) -> tuple[int, ...]:
    """Parse a version string into a comparable tuple.

    Split on ``.``/``-``; each part contributes its leading integer, and a
    non-empty non-numeric part contributes ``0`` (the reconciled behaviour
    described in the module docstring). ``""`` yields ``()``.
    """
    if not version:
        return ()
    out: list[int] = []
    for part in re.split(r"[.\-]", version):
        m = re.match(r"(\d+)", part)
        if m:
            out.append(int(m.group(1)))
        elif part:
            out.append(0)
    return tuple(out)


def _signature_version(model_id: str) -> str:
    """Numeric version parsed from the model signature, or ""."""
    try:
        return _numeric_version(ModelNormalizer.extract_signature(model_id).version)
    except Exception:
        return ""


def _direct_version(model_id: str) -> str:
    """First numeric run in the raw model id, or ""."""
    return _numeric_version(model_id)


def _base_without_version(model_id: str) -> str:
    """Normalized family+variant base with the numeric version removed."""
    norm = ModelNormalizer.normalize(model_id)
    base = _NUM_VERSION_RE.sub("", norm)
    return re.sub(r"-+", "-", base).strip("-")


def _keeper_version(keeper_id: str) -> tuple[int, ...]:
    return version_tuple(_signature_version(keeper_id) or _direct_version(keeper_id))


def has_older_kept_sibling(model_id: str, keepers: Iterable[str]) -> bool:
    """True when any keeper is an older version of the same family/variant.

    ``keepers`` is any iterable of model-id strings. The candidate's version is
    taken from its signature first, then the raw id (the store/gate semantics),
    so the gate path is unchanged by the unification. Returns ``False`` when the
    candidate has no parseable version or no comparable base.
    """
    if not model_id:
        return False
    cur_num = _signature_version(model_id) or _direct_version(model_id)
    if not cur_num:
        return False
    cur_ver = version_tuple(cur_num)
    if not cur_ver:
        return False
    cur_base = _base_without_version(model_id)
    if not cur_base:
        return False
    for keeper_id in keepers:
        if not keeper_id:
            continue
        if _base_without_version(keeper_id) != cur_base:
            continue
        keeper_ver = _keeper_version(keeper_id)
        if not keeper_ver:
            continue
        if keeper_ver < cur_ver:
            return True
    return False


def _keeper_ids_from_evaluations(keep: Iterable[dict[str, Any]]) -> Iterator[str]:
    for evaluation in keep:
        mid = evaluation.get("provider_model_id") or evaluation.get("model_id") or ""
        if mid:
            yield mid


def batch_has_older_kept_sibling(model_id: str, keep: Iterable[dict[str, Any]]) -> bool:
    """Same-batch adapter: keeper ids come from ``result["keep"]`` records."""
    return has_older_kept_sibling(model_id, _keeper_ids_from_evaluations(keep))


def _keeper_ids_from_store(store: Any | None) -> Iterator[str]:
    """Store keys whose record decision is ``keep``."""
    if not store:
        return
    try:
        if hasattr(store, "_ensure_loaded"):
            store._ensure_loaded()
        data = getattr(store, "_data", None)
        if not data:
            return
        for key, rec in list(data.items()):
            judge = getattr(rec, "judge", None)
            if judge is None and isinstance(rec, dict):
                judge = rec.get("judge")
            decision = None
            if judge is not None:
                decision = judge.get("decision") if isinstance(judge, dict) else getattr(judge, "decision", None)
            if decision is None:
                decision = getattr(rec, "decision", None) if not isinstance(rec, dict) else rec.get("decision")
            if decision and str(decision).strip().lower() == "keep":
                yield key
    except Exception:
        return


def store_has_older_kept_sibling(model_id: str, store: Any | None) -> bool:
    """Store adapter: keeper ids come from a ``ModelInfoStore``."""
    return has_older_kept_sibling(model_id, _keeper_ids_from_store(store))
