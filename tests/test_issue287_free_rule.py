"""Issue #287 — differential corpus for the free-rule module (phase 2 of #285).

``llm_discovery.free_rule`` is additive: no production caller imports it yet.
This test is the gate for the migration (#288) and the deletion (#292). It
asserts, row by row, that the new predicate reproduces each of the nine
pre-consolidation free-rule call sites, so consolidating them cannot change
which models are free.

Call sites compared
-------------------
Discovery family — must agree exactly, provider included:
  * ``pipeline._is_free_model``         -> ``free_rule.is_free``
  * ``pipeline._has_free_name``         -> ``free_rule.has_free``
  * ``pipeline._split_by_free_rule``    -> ``free_rule.split``
  * ``pipeline._is_pricing_free``       -> the pricing branch inside ``is_free``
  * ``pipeline._is_access_tier_free``   -> the access-tier branch
  * ``pipeline._fetch_pricing_free_ids``-> ``free_rule.pricing_row_is_free``

Router — the three copies must agree:
  * ``gate._is_router_model_id``, ``policy_gate._is_router_model``,
    ``search_budget._is_router``        -> ``free_rule.is_router``

Keeper gate — characterized, not silently reconciled:
  * ``gate._is_free_model_id`` and gate floor 3 (``pricing_blended == 0``).

The gate is the one call site that genuinely disagrees: its regex is narrower
than the discovery rule, which is exactly the #285 defect (free on the
discovery path, paid inside the gate). The diverging rows are recorded
explicitly below and pinned, so #288 adopts the discovery answer knowingly
rather than by accident. No divergence is left implicit.
"""
from __future__ import annotations

import httpx
import pytest

from llm_discovery import free_rule, gate, pipeline, policy_gate, search_budget

# --------------------------------------------------------------------------
# Free predicate corpus: every provider branch plus the pricing/access-tier
# shapes. (label, model, provider, expected_is_free)
# --------------------------------------------------------------------------
FREE_CORPUS = [
    # kilo: isFree flag is authoritative on a real descriptor
    ("kilo isFree true", {"id": "kilo-efficient", "isFree": True}, "kilo", True),
    ("kilo isFree false", {"id": "kilo-efficient", "isFree": False}, "kilo", False),
    # navy_ai: premium identity check
    ("navy premium false", {"id": "navy-gpt-4", "premium": False}, "navy_ai", True),
    ("navy premium true", {"id": "navy-gpt-4", "premium": True}, "navy_ai", False),
    ("navy premium string false", {"id": "navy-gpt-4", "premium": "false"}, "navy_ai", False),
    ("navy premium zero", {"id": "navy-gpt-4", "premium": 0}, "navy_ai", False),
    # llm7: tier == turbo
    ("llm7 turbo", {"id": "llm7-dsv4", "tier": "turbo"}, "llm7", True),
    ("llm7 pro", {"id": "llm7-opus", "tier": "pro"}, "llm7", False),
    ("llm7 turbo without provider", {"id": "llm7-dsv4", "tier": "turbo"}, None, False),
    # agnes: -flash suffix
    ("agnes flash", {"id": "agnes-2.0-flash"}, "agnes", True),
    ("agnes non-flash", {"id": "agnes-2.0-pro"}, "agnes", False),
    # xkiro: access_tier == free (issue #266)
    ("xkiro access_tier free", {"id": "xkiro-minimax", "access_tier": "free"}, "xkiro", True),
    ("xkiro access_tier paid", {"id": "xkiro-gpt-5", "access_tier": "paid"}, "xkiro", False),
    ("xkiro access_tier premium", {"id": "xkiro-opus", "access_tier": "premium"}, "xkiro", False),
    # apinex: free/ prefix marker
    ("apinex free/ prefix", {"id": "free/claude-opus-4.6"}, "apinex", True),
    # suffix markers
    ("suffix :free", {"id": "gpt-4:free"}, None, True),
    ("suffix -free", {"id": "gpt-4-free"}, None, True),
    ("suffix _free", {"id": "gpt-4_free"}, None, True),
    ("suffix /free", {"id": "model/free"}, None, True),
    # plain paid
    ("plain paid", {"id": "gpt-4"}, None, False),
    ("paid premium-worded id", {"id": "premium-model"}, None, False),
    # pricing shapes
    ("pricing dict prompt/completion zero", {"id": "pz-dict", "pricing": {"prompt": 0, "completion": 0}}, None, True),
    ("pricing dict positive", {"id": "pz-paid", "pricing": {"prompt": 1.0, "completion": 2.0}}, None, False),
    ("pricing flat prompt zero", {"id": "pz-flat", "prompt": 0}, None, True),
    ("pricing blended key zero", {"id": "pz-blend", "pricing": {"price_1m_blended_3_to_1": 0}}, None, True),
    ("pricing ancillary request zero ignored", {"id": "pz-anc", "pricing": {"request": 0}}, None, False),
    ("pricing empty dict", {"id": "pz-empty", "pricing": {}}, None, False),
    ("pricing scalar zero", {"id": "pz-scalar", "pricing": 0}, None, False),
    # generic access tier
    ("access_tier free generic", {"id": "at-free", "access_tier": "free"}, None, True),
    ("access_tier paid generic", {"id": "at-paid", "access_tier": "paid"}, None, False),
]

_IDS = [row[0] for row in FREE_CORPUS]

# (label, model, provider) batches for the split comparison.
SPLIT_BATCHES = [
    ("apinex", "apinex", [{"id": "free/kimi-k3"}, {"id": "grok-4.6"}, {"id": "paid-model"}]),
    ("navy mixed", "navy_ai", [
        {"id": "free-via-premium", "premium": False},
        {"id": "free-via-marker:free", "premium": True},
        {"id": "paid", "premium": True},
        {"id": "paid-no-premium"},
    ]),
    ("llm7 mixed", "llm7", [
        {"id": "llm7-turbo", "tier": "turbo"},
        {"id": "llm7-pro", "tier": "pro"},
    ]),
    ("agnes mixed", "agnes", [{"id": "agnes-2.0-flash"}, {"id": "agnes-2.0-pro"}]),
    ("xkiro mixed", "xkiro", [
        {"id": "xkiro-free", "access_tier": "free"},
        {"id": "xkiro-paid", "access_tier": "paid"},
    ]),
    ("generic mixed", "", [{"id": "a:free"}, {"id": "b"}, {"id": "c-free"}]),
    ("generic none free", "", [{"id": "a"}, {"id": "b"}]),
    ("generic all free", "", [{"id": "a:free"}, {"id": "b-free"}]),
]

# --------------------------------------------------------------------------
# Router corpus
# --------------------------------------------------------------------------
ROUTER_CORPUS = [
    "kilo-auto/free",
    "openrouter/free",
    "some-router-x",
    "auto:free",
    "myauto/free",
    "ROUTER",
    "gpt-4",
    "",
    "free/claude-opus-4.6",
    # Negative rows that isolate the auto+free branch: "auto" without "free"
    # and "free" without "auto" are both not routers. Without these a mutation
    # that drops either half of the branch survives the corpus.
    "kilo-auto",
    "auto-select",
    "free-tier",
]

# --------------------------------------------------------------------------
# Keeper-gate characterization. (model_id, gate answer, free_rule answer)
# --------------------------------------------------------------------------
GATE_FREE_CORPUS = [
    ("gpt-4:free", True, True),
    ("gpt-4-free", True, True),
    ("gpt-4_free", True, True),
    ("model/free", True, True),
    ("gpt-4", False, False),
    ("premium-model", False, False),
    # DIVERGE — gate's regex is narrower than the discovery rule (#288 closes
    # this): the apinex free/ prefix and any non-terminal marker.
    ("free/claude-opus-4.6", False, True),
    ("foo-free-bar", False, True),
    ("model:free/", False, True),
    ("free/", False, True),
    ("x-free-y", False, True),
    # DIVERGE — gate lowercases the id; free_rule markers are case-sensitive.
    ("GPT-4:FREE", True, False),
    ("FREE", True, False),
    ("free", True, False),
]

# (pricing, gate floor-3 free?, free_rule free?)
GATE_PRICING_CORPUS = [
    ({"prompt": 0, "completion": 0}, False, True),   # DIVERGE: gate reads blended only
    ({"price_1m_blended_3_to_1": 0}, True, True),
    ({"blended": 0}, True, False),                   # DIVERGE: gate's blended alias
    ({"request": 0}, False, False),
    ({}, False, False),
    (0, True, False),                                # DIVERGE: scalar pricing
    ({"prompt": 1.0}, False, False),
]


def test_corpus_covers_every_provider_branch():
    seen = {provider for _, _, provider, _ in FREE_CORPUS if provider}
    assert {"kilo", "navy_ai", "llm7", "agnes", "xkiro", "apinex"} <= seen


def test_corpus_covers_every_free_marker():
    ids = " ".join(str(model.get("id", "")) for _, model, _, _ in FREE_CORPUS)
    for marker in free_rule.FREE_MARKERS:
        assert marker in ids, marker


@pytest.mark.parametrize("label,model,provider,expected", FREE_CORPUS, ids=_IDS)
def test_is_free_matches_the_discovery_call_site(label, model, provider, expected):
    assert free_rule.is_free(model, provider) is expected
    assert pipeline._is_free_model(model, provider) is expected


@pytest.mark.parametrize("label,model,provider,expected", FREE_CORPUS, ids=_IDS)
def test_has_free_matches_the_discovery_call_site(label, model, provider, expected):
    assert free_rule.has_free([model], provider) is expected
    assert pipeline._has_free_name([model], provider) is expected


@pytest.mark.parametrize("label,model,provider,expected", FREE_CORPUS, ids=_IDS)
def test_split_matches_the_discovery_call_site(label, model, provider, expected):
    new_keep, new_drop = free_rule.split([model], provider or "")
    old_keep, old_drop = pipeline._split_by_free_rule([model], provider or "")
    assert [m["id"] for m in new_keep] == [m["id"] for m in old_keep]
    assert [m["id"] for m in new_drop] == [m["id"] for m in old_drop]


@pytest.mark.parametrize("label,model,provider,expected", FREE_CORPUS, ids=_IDS)
def test_pricing_and_access_tier_branches_match(label, model, provider, expected):
    assert free_rule._is_pricing_free(model) is pipeline._is_pricing_free(model)
    assert free_rule._is_access_tier_free(model) is pipeline._is_access_tier_free(model)


@pytest.mark.parametrize("label,provider,models", SPLIT_BATCHES, ids=[b[0] for b in SPLIT_BATCHES])
def test_split_matches_the_discovery_call_site_on_batches(label, provider, models):
    new_keep, new_drop = free_rule.split(list(models), provider)
    old_keep, old_drop = pipeline._split_by_free_rule(list(models), provider)
    assert [m["id"] for m in new_keep] == [m["id"] for m in old_keep]
    assert [m["id"] for m in new_drop] == [m["id"] for m in old_drop]


def test_split_returns_all_when_nothing_is_free():
    models = [{"id": "a"}, {"id": "b"}]
    keep, dropped = free_rule.split(models)
    assert keep == models
    assert dropped == []
    assert free_rule.split([]) == ([], [])


def test_provider_branch_is_scoped_to_its_provider():
    """A fix for one provider must not change another provider's result."""
    assert free_rule.is_free({"id": "m", "premium": False}, "navy_ai") is True
    assert free_rule.is_free({"id": "m", "tier": "turbo"}, "llm7") is True
    assert free_rule.is_free({"id": "m-flash"}, "agnes") is True
    for other in ("kilo", "xkiro", "agnes", "llm7", "groq", None, ""):
        assert free_rule.is_free({"id": "m", "premium": False}, other) is False
    for other in ("kilo", "xkiro", "agnes", "navy_ai", "groq", None, ""):
        assert free_rule.is_free({"id": "m", "tier": "turbo"}, other) is False
    for other in ("kilo", "xkiro", "llm7", "navy_ai", "groq", None, ""):
        assert free_rule.is_free({"id": "m-flash"}, other) is False


# --------------------------------------------------------------------------
# Router call sites
# --------------------------------------------------------------------------
@pytest.mark.parametrize("model_id", ROUTER_CORPUS)
def test_is_router_matches_every_router_call_site(model_id):
    expected = free_rule.is_router(model_id)
    assert gate._is_router_model_id(model_id) is expected
    assert policy_gate._is_router_model(model_id) is expected
    assert search_budget._is_router(model_id) is expected


def test_is_router_matches_the_gate_on_none_and_empty():
    assert free_rule.is_router(None) is False
    assert gate._is_router_model_id(None) is False
    assert free_rule.is_router("") is False
    assert gate._is_router_model_id("") is False


def test_router_corpus_covers_the_router_shapes():
    # exact router ids, the generic "router" substring, and auto+free
    assert free_rule.is_router("openrouter/free") is True
    assert free_rule.is_router("some-router-x") is True
    assert free_rule.is_router("auto:free") is True
    assert free_rule.is_router("gpt-4") is False


def test_router_auto_branch_requires_both_auto_and_free():
    """The auto+free branch is a conjunction, not either half alone.

    Pins each side independently so a mutation dropping one half fails here:
    "auto" alone and "free" alone are not routers, together they are.
    """
    for auto_only in ("kilo-auto", "auto-select", "autocomplete"):
        assert free_rule.is_router(auto_only) is False, auto_only
    for free_only in ("free-tier", "model-free", "free/"):
        assert free_rule.is_router(free_only) is False, free_only
    for both in ("auto:free", "myauto/free", "auto-free"):
        assert free_rule.is_router(both) is True, both


# --------------------------------------------------------------------------
# Keeper-gate characterization (the documented divergence)
# --------------------------------------------------------------------------
def test_gate_free_id_characterization_matches_the_recorded_gate_answers():
    for model_id, gate_answer, _ in GATE_FREE_CORPUS:
        assert gate._is_free_model_id(model_id) is gate_answer, model_id


def test_free_rule_matches_the_recorded_answers():
    for model_id, _, rule_answer in GATE_FREE_CORPUS:
        assert free_rule.is_free(model_id, None) is rule_answer, model_id


def test_free_rule_agrees_with_the_gate_on_every_non_diverging_row():
    for model_id, gate_answer, rule_answer in GATE_FREE_CORPUS:
        if gate_answer == rule_answer:
            assert free_rule.is_free(model_id, None) is gate_answer, model_id


def test_gate_free_id_divergence_is_exactly_the_documented_set():
    """Pins which ids the two predicates disagree on, so a change is loud."""
    diverging = {mid for mid, gate_answer, rule_answer in GATE_FREE_CORPUS if gate_answer != rule_answer}
    assert diverging == {
        "free/claude-opus-4.6",
        "foo-free-bar",
        "model:free/",
        "free/",
        "x-free-y",
        "GPT-4:FREE",
        "FREE",
        "free",
    }


def _gate_floor3_record(pricing):
    """A record that passes every gate floor except (possibly) floor 3."""
    return {
        "model_id": "plain-model",
        "evidence_level": "strong",
        "coding_score": 10.0,
        "pricing": pricing,
        "aa_model_id": "aa-x",
        "benchmarks": {
            "scores": {
                "aa_intelligence": {"score": 60},
                "swe_bench_verified": {"score": 60},
                "livecodebench": {"score": 60},
                "humaneval": {"score": 60},
            }
        },
        "evidence": ["https://example.com/x"],
    }


@pytest.mark.parametrize(
    "pricing,gate_free,rule_free",
    GATE_PRICING_CORPUS,
    ids=[repr(row[0]) for row in GATE_PRICING_CORPUS],
)
def test_gate_pricing_floor_characterization(pricing, gate_free, rule_free):
    ok, reason = gate.is_accurate_enough(_gate_floor3_record(pricing))
    assert ok is gate_free
    assert reason == ("" if gate_free else "pricing missing and not free")
    assert free_rule.is_free({"id": "plain-model", "pricing": pricing}, None) is rule_free


def test_gate_pricing_divergence_is_exactly_the_documented_set():
    diverging = {repr(pricing) for pricing, gate_free, rule_free in GATE_PRICING_CORPUS if gate_free != rule_free}
    assert diverging == {repr({"prompt": 0, "completion": 0}), repr({"blended": 0}), repr(0)}


# --------------------------------------------------------------------------
# Pricing-endpoint signal (Pricing-Endpoint Filter)
# --------------------------------------------------------------------------
class _FakePricingResponse:
    def __init__(self, payload, status_code: int = 200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _stub_pricing(monkeypatch, rows):
    payload = {"auto_groups": ["default"], "data": rows}

    def fake_get(url, **kwargs):
        return _FakePricingResponse(payload)

    monkeypatch.setattr(httpx, "get", fake_get)


ENDPOINT_ROWS = [
    ("prov/tagged-free", {"model_name": "prov/tagged-free", "tags": "free", "model_ratio": 0.4}),
    ("prov/list-free", {"model_name": "prov/list-free", "tags": ["new", "free"], "model_ratio": 0.2}),
    ("prov/ratio-zero", {"model_name": "prov/ratio-zero", "tags": None, "model_ratio": 0}),
    ("prov/paid", {"model_name": "prov/paid", "tags": None, "model_ratio": 0.15}),
    ("prov/untagged", {"model_name": "prov/untagged"}),
]


def test_pricing_row_predicate_matches_the_endpoint_call_site(monkeypatch):
    rows = [row for _, row in ENDPOINT_ROWS]
    _stub_pricing(monkeypatch, rows)
    ids = pipeline._fetch_pricing_free_ids("https://api.example.com/v1")
    assert ids == {"prov/tagged-free", "prov/list-free", "prov/ratio-zero"}
    for name, row in ENDPOINT_ROWS:
        assert free_rule.pricing_row_is_free(row) is (name in ids), name


def test_pricing_row_predicate_needs_no_name_but_the_fetcher_does(monkeypatch):
    """The predicate needs no name; the fetcher also requires a usable name."""
    assert free_rule.pricing_row_is_free({"tags": "free"}) is True
    assert free_rule.pricing_row_is_free({"model_ratio": 0}) is True
    assert free_rule.pricing_row_is_free("not-a-row") is False
    _stub_pricing(monkeypatch, [{"tags": "free", "model_ratio": 0.5}])
    assert pipeline._fetch_pricing_free_ids("https://api.example.com/v1") == set()


def test_pricing_endpoint_split_still_agrees_with_the_row_predicate(monkeypatch):
    rows = [row for _, row in ENDPOINT_ROWS]
    _stub_pricing(monkeypatch, rows)
    models = [{"id": name} for name, _ in ENDPOINT_ROWS]
    split = pipeline._split_free_by_pricing_endpoint("https://api.example.com/v1", models, "bvg")
    assert split is not None
    free, paid = split
    assert [m["id"] for m in free] == ["prov/tagged-free", "prov/list-free", "prov/ratio-zero"]
    assert [m["id"] for m in paid] == ["prov/paid", "prov/untagged"]
