"""Free rule — one predicate for "is this model free?" and "is this a router?".

Phase 2 of #285 / issue #287 (the expand step). This module is additive: nothing
imports it yet. ``pipeline``, ``gate``, ``policy_gate`` and ``search_budget``
keep their own copies until #288 migrates them onto this interface and #292
deletes the copies. Behaviour here is absorbed verbatim from the call sites it
will replace, so the migration is a move, not a rule change.

The free rule is *provider-scoped*: :func:`is_free` takes the provider name as
an explicit input, so a fix for one provider cannot change another provider's
result. This is the seam ADR 0004 §5 anticipated when it deferred a
``free_rule: premium_flag`` per-provider toggle; the provider branches,
absorbed from ``pipeline._is_free_model``, are:

  * ``kilo``    — ``isFree is True`` on a real model descriptor is authoritative.
  * ``navy_ai`` — ``premium is False`` (identity check; the string ``"false"``
    and the integer ``0`` are not free).
  * ``llm7``    — ``tier == "turbo"``.
  * ``agnes``   — ``-flash`` in the id.
  * ``xkiro``   — ``access_tier == "free"`` (issue #266; the earlier keep-all
    exemption was withdrawn because xkiro's free credits are usable only on
    free-tier models).

Every other provider is generic: a :data:`FREE_MARKERS` id marker, zero pricing
in the recognised pricing shapes, or an ``access_tier`` of ``free``.

Documented divergence from the Keeper gate (#287)
-------------------------------------------------
``gate._is_free_model_id`` is one of the nine pre-consolidation free-rule copies
but uses a different, narrower predicate: a case-insensitive regex anchored at
the end of the id, ``(?:[:/_-]|^)free$``. It therefore misses the apinex
``free/`` prefix, non-terminal markers (``foo-free-bar``), and every
non-id signal (``isFree``, ``premium``, ``tier``, ``access_tier``, pricing); it
also matches ids the discovery rule rejects (``GPT-4:FREE``, bare ``free``)
because it lowercases. Its floor-3 pricing check reads only a blended price, so
it misses the prompt/completion/input/output shapes the discovery rule accepts.
That gap is why a model can be free on the discovery path and paid inside the
gate (#285 problem 2); ADR 0006 §3 records the gate's narrower free-marker
exception this widens. This module keeps the discovery semantics; the gate's
narrower set is recorded row by row in ``tests/test_issue287_free_rule.py`` and
closed by #288, which makes ``is_accurate_enough`` call :func:`is_free`. No
divergence is left implicit.

The pricing-endpoint signal is an input to the free rule, not a second rule:
:func:`pricing_row_is_free` is the per-row decision behind the
**Pricing-Endpoint Filter** term in ``CONTEXT.md``. The live-probe split is
network I/O, not a predicate, and stays in ``pipeline`` for the migration
issues to wire through.

This module is additive by design (the expand half of #285 Phase 2): it copies
the call sites' behaviour verbatim so #288 can migrate onto it and #292 can
delete the copies. ``FREE_MARKERS`` is therefore duplicated in ``pipeline``
until #292; the two must not be edited independently before then.
"""
from __future__ import annotations

from typing import Any

# "free/" is the prefix form (apinex: free/claude-opus-4.6); the rest are suffix forms.
FREE_MARKERS = (":free", "-free", "_free", "/free", "free/")

# Pricing keys that actually describe a per-token price. Ancillary fields
# (request, image, web_search) are 0 for many paid models (e.g. kilo), so they
# are deliberately excluded.
_PRICING_KEYS = (
    "prompt", "completion", "input", "output", "input_cache_read", "input_cache_write",
    "price", "prices", "cost", "price_1m_input_tokens", "price_1m_output_tokens",
    "price_1m_blended_3_to_1", "prompt_price", "completion_price", "input_price", "output_price",
)


def _is_pricing_free(model: dict[str, Any]) -> bool:
    """Generic pricing==0 free detection (no hardcoded model names).

    Only checks prompt/completion/input/output pricing, not ancillary
    fields like request/image/web_search which are 0 for many paid models
    (e.g. kilo). Covers prompt/input/output/blended and flattened prices.
    """
    pricing = model.get("pricing")
    if isinstance(pricing, dict):
        for k, v in pricing.items():
            if k not in _PRICING_KEYS and "prompt" not in k and "completion" not in k and "input" not in k and "output" not in k and "price" not in k:
                continue
            try:
                if float(v) == 0:
                    # Need to ensure it's actually prompt/completion/input/output, not request/image
                    # For kilo, efficient has prompt -1, free has 0, so this distinguishes
                    return True
            except (ValueError, TypeError):
                continue
    for _k in _PRICING_KEYS:
        val = model.get(_k)
        if val is not None:
            try:
                if float(val) == 0:
                    return True
            except (ValueError, TypeError):
                continue
        if isinstance(pricing, dict) and _k in pricing:
            try:
                if float(pricing[_k]) == 0:
                    return True
            except (ValueError, TypeError):
                continue
    return False


def _is_access_tier_free(model: dict[str, Any]) -> bool:
    """Check access_tier == free (xkiro: access_tier free vs paid/premium)."""
    tier = model.get("access_tier")
    if isinstance(tier, str) and tier.strip().lower() == "free":
        return True
    return False


def is_free(model: dict[str, Any] | str, provider_name: str | None = None) -> bool:
    """Return True if model is free (provider-aware, no hardcoded model names).

    Generic: id contains any FREE_MARKERS (suffix :free/-free/_free//free or
    prefix free/) OR pricing == 0 OR access_tier == free.
    navy_ai: marker OR premium is False (identity check) OR pricing == 0.
    llm7: tier==turbo OR marker OR pricing == 0.
    agnes: marker OR -flash suffix OR pricing == 0.
    xkiro: access_tier == "free" OR pricing == 0. The earlier keep-all
    exemption was withdrawn (issue #266): xkiro's free credits are usable only
    on free-tier models, so paid/premium models must not be evaluated.
    All other providers (bai, bestvirtualgoods, vyceai, nvidia_nim,
    ollama_cloud, etc): marker OR pricing == 0 OR access_tier free — no hardcoded allowlists.
    Missing/None/string premium -> marker/pricing fallback. Str model -> marker-only.
    """
    if isinstance(model, dict):
        # kilo: isFree flag is authoritative when a real model descriptor is given
        if model.get("isFree") is True:
            return True
        model_id = str(model.get("id", ""))
        is_marker = any(marker in model_id for marker in FREE_MARKERS)
        if is_marker:
            return True

        # Provider-specific flags (non-name signals)
        if provider_name == "navy_ai" and model.get("premium") is False:
            return True
        if provider_name == "llm7" and model.get("tier") == "turbo":
            return True
        if provider_name == "agnes" and "-flash" in model_id:
            return True

        # Generic pricing == 0 — applies to ALL providers (replaces hardcoded BVG list and vyceai/xkiro scoping)
        if _is_pricing_free(model):
            return True
        if _is_access_tier_free(model):
            return True

        return False
    model_id = str(model)
    return any(marker in model_id for marker in FREE_MARKERS)


def has_free(models: list[dict[str, Any]], provider_name: str | None = None) -> bool:
    """Return True if any model qualifies as free under the provider rule."""
    return any(is_free(m, provider_name) for m in models)


def split(
    models: list[dict[str, Any]],
    provider_name: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split models into (keep, dropped) by the free rule (provider-aware).

    When no model is free the whole list is kept and nothing is dropped;
    otherwise only the free models are kept. The default provider_name=""
    is generic marker/pricing, matching ``is_free(m, None)``.
    """
    # Normalize provider_name for is_free (None vs "" both generic)
    pn = provider_name or None
    if not has_free(models, pn):
        return models, []
    free_models = [m for m in models if is_free(m, pn)]
    non_free = [m for m in models if m not in free_models]
    return free_models, non_free


def is_router(model_id: str | None) -> bool:
    """True for router meta-models (e.g. ``kilo-auto/free``, ``openrouter/free``).

    Routers delegate to free candidates, carry no coding benchmarks, and are
    always keep/flash. The union of the three pre-consolidation predicates
    (``gate._is_router_model_id``, ``policy_gate._is_router_model``,
    ``search_budget._is_router``): a ``router`` substring, or ``auto`` and
    ``free`` together. The id is stripped and lowercased, so whitespace-padded
    ids match; ``None``/empty is not a router.

    Those copies also carry an explicit allowlist of ``kilo-auto/free`` and
    ``openrouter/free``. It is dropped here because it is unreachable — the
    first matches ``auto``+``free`` and the second matches ``router`` — so the
    union is unchanged (verified over the router corpus and a brute-force id
    space). The three copies agree on every corpus row; the only difference is
    input handling — ``policy_gate._is_router_model`` would raise on ``None`` —
    and this function resolves that to ``False``. #288 removes the copies.
    """
    if not model_id:
        return False
    lower = str(model_id).strip().lower()
    if "router" in lower:
        return True
    if "auto" in lower and "free" in lower:
        return True
    return False


def pricing_row_is_free(row: dict[str, Any]) -> bool:
    """Per-row predicate behind the Pricing-Endpoint Filter.

    A console pricing row is free when its ``tags`` carry ``free`` (string or
    list) or its ``model_ratio`` is ``0``. This is the decision
    ``pipeline._fetch_pricing_free_ids`` makes inline today; it is one input to
    the free rule, not a separate rule.
    """
    if not isinstance(row, dict):
        return False
    tags = row.get("tags")
    tags_free = isinstance(tags, str) and "free" in tags.lower()
    tags_free = tags_free or (isinstance(tags, list) and any(isinstance(t, str) and "free" in t.lower() for t in tags))
    ratio = row.get("model_ratio")
    ratio_free = isinstance(ratio, (int, float)) and ratio == 0
    return bool(tags_free or ratio_free)
