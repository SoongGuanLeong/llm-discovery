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
tuple). Both call sites only ever pass the output of :func:`_signature_version`
or :func:`_direct_version`, which is digits and ``.``/``-`` separators only, so
the two behaviours were indistinguishable from the predicate; the choice fixes
this module's standalone contract and is pinned by
``test_version_tuple_reconciles_the_two_parsers``.

The candidate's version is taken from its signature first, then the raw id —
the store/gate semantics. Unifying on that order also removes a divergence the
characterization test surfaced: the old inline same-batch copy took the first
numeric run in the raw id, so it counted a parameter size as a version and
promoted e.g. ``llama-3.3-70b`` when ``llama-3.3-8b`` was kept. The store/gate
path was unchanged by #291 (verified over the real store); #294 later changes
it on the two rows named below. The characterization test records both.

Single-component versions and the mixed-arity rule (#294)
---------------------------------------------------------
The signature parser now accepts a one-component version (``gpt-4`` -> ``4``,
``o3-mini`` -> ``3``), so those ids are visible to the heuristic at all. The
raw-id fallback stays two-component, so a lone parameter size (``mistral-7b``,
``mistral-8x7b``) is never read as a version; only the signature is trusted for
a single-component version. (A two-component run in a signature-less id can
still be a family digit plus a parameter size — e.g. ``qwen2-72b-instruct`` ->
``2-72``. That pre-existing weakness is unchanged here and out of #294's
scope.)

The base strips *every* numeric run, so mixed-format ids share a base
(``gpt-4``/``gpt-4.1`` -> ``gpt``, ``phi-4``/``phi-3.5`` -> ``phi``,
``step-2-16k``/``step-1-8k`` -> ``step-k``). Two versions of different arity
are then compared on their shared prefix only: a one-component version makes
no comparable claim about the longer one, so ``gpt-4`` does not promote
``gpt-4.1`` (the #291 mixed-format answer, kept), while ``phi-3.5`` ``(3, 5)``
still promotes ``phi-4`` ``(4,)`` because the shared prefix already differs.
The delta versus the pre-#294 implementation is exactly two rows — ``phi-4`` /
``phi-3.5`` and ``o3-mini`` / ``o1-mini`` become ``True`` — and is pinned by
``test_behaviour_delta_is_exactly_the_single_component_rows``.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any

from .model_matching import ModelNormalizer

# A numeric run: a digit group, optionally joined by '.' or '-' to further
# digit groups (e.g. "4", "3.3", "3.3-70", "08-2024"). The signature parser and
# the base strip both accept a single component (#294).
_VERSION_RUN_RE = re.compile(r"\d+(?:[\.\-]\d+)*")

# The raw-id fallback keeps the stricter two-component form: a lone number in a
# raw id is far more likely a parameter size ("7b" in mistral-7b, "8x7b" in
# mistral-8x7b) than a version.
_NUM_VERSION_RE = re.compile(r"\d+(?:[\.\-]\d+)+")


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
    """Numeric version parsed from the model signature, or "".

    Accepts a single-component version ("4" for ``gpt-4``, "3" for
    ``o3-mini``) — #294. Only the signature is trusted to read a lone number
    as a version; the raw-id fallback below stays two-component.
    """
    try:
        version = ModelNormalizer.extract_signature(model_id).version
    except Exception:
        return ""
    m = _VERSION_RUN_RE.search(version or "")
    return m.group(0) if m else ""


def _direct_version(model_id: str) -> str:
    """First two-component-or-longer numeric run in the raw model id, or ""."""
    m = _NUM_VERSION_RE.search(model_id or "")
    return m.group(0) if m else ""


def _base_without_version(model_id: str) -> str:
    """Normalized family+variant base with every numeric run removed.

    Removing *every* numeric run (not just the signature token) is what makes
    mixed-format bases match: ``gpt-4`` and ``gpt-4.1`` both reduce to ``gpt``,
    ``phi-4`` and ``phi-3.5`` to ``phi``, and the context-length pair
    ``step-2-16k``/``step-1-8k`` to ``step-k``. Stripping only the exact
    signature token left the two ``step-*`` ids with divergent bases, a
    regression the #294 prototype hit.
    """
    norm = ModelNormalizer.normalize(model_id)
    base = _VERSION_RUN_RE.sub("", norm)
    return re.sub(r"-+", "-", base).strip("-")


def _keeper_is_older(keeper_ver: tuple[int, ...], candidate_ver: tuple[int, ...]) -> bool:
    """True when *keeper_ver* is a strictly older version than *candidate_ver*.

    Versions of different arity are compared on their shared prefix only (the
    #294 mixed-arity rule): ``gpt-4`` ``(4,)`` makes no comparable claim about
    ``gpt-4.1`` ``(4, 1)``, so it does not promote it. When the shared prefix
    already differs — ``phi-4`` ``(4,)`` vs ``phi-3.5`` ``(3, 5)`` — the
    comparison is unambiguous and the older sibling is detected. Both callers
    pass non-empty tuples, so the shared prefix has at least one component.
    """
    shared = min(len(keeper_ver), len(candidate_ver))
    return keeper_ver[:shared] < candidate_ver[:shared]


def _keeper_version(keeper_id: str) -> tuple[int, ...]:
    return version_tuple(_signature_version(keeper_id) or _direct_version(keeper_id))


def has_older_kept_sibling(model_id: str, keepers: Iterable[str]) -> bool:
    """True when any keeper is an older version of the same family/variant.

    ``keepers`` is any iterable of model-id strings. The candidate's version is
    taken from its signature first, then the raw id — the store/gate semantics
    #291 unified on. #294 then made single-component signature versions parse,
    so the gate path does now differ from the pre-#294 behaviour on exactly the
    rows named in the module docstring. Returns ``False`` when the candidate
    has no parseable version or no comparable base.
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
        if _keeper_is_older(keeper_ver, cur_ver):
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
            # Canonical ``judgement`` first, then the legacy ``judge`` snapshot —
            # the same precedence ``model_info_store.derive_tier`` uses.
            decision = None
            for field_name in ("judgement", "judge"):
                snap = getattr(rec, field_name, None)
                if snap is None and isinstance(rec, dict):
                    snap = rec.get(field_name)
                if snap is None:
                    continue
                decision = snap.get("decision") if isinstance(snap, dict) else getattr(snap, "decision", None)
                if decision:
                    break
            if not decision:
                decision = getattr(rec, "decision", None) if not isinstance(rec, dict) else rec.get("decision")
            if decision and str(decision).strip().lower() == "keep":
                yield key
    except Exception:
        return


def store_has_older_kept_sibling(model_id: str, store: Any | None) -> bool:
    """Store adapter: keeper ids come from a ``ModelInfoStore``."""
    return has_older_kept_sibling(model_id, _keeper_ids_from_store(store))
