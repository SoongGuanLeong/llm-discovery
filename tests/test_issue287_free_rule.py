"""Free-rule corpus — the behaviour contract for the single Free Rule.

``llm_discovery.free_rule`` is the one Free Rule definition. Issue #287 added
the module with a differential corpus against the nine pre-consolidation call
sites; #288 wired the callers onto it and #292 deleted the superseded copies, so
the corpus now pins ``free_rule``'s own answers rather than comparing copies.
The recorded answers are unchanged — the module was absorbed verbatim from the
call sites it replaced, so this remains the consolidation's acceptance gate.

Covered here
------------
* ``free_rule.is_free`` / ``has_free`` / ``split`` over every provider branch
  (kilo, navy_ai, llm7, agnes, xkiro, apinex) plus the pricing/access-tier
  shapes and the free-marker forms.
* ``free_rule.is_router`` over the router corpus, including the negative rows
  that isolate the auto+free branch.
* The Keeper gate's floor 3, which adopts the Free Rule (a model free on the
  discovery path is free inside the gate — the #285 defect). The remaining
  gate-vs-rule differences in the pricing table are the gate's
  "pricing present OR free" floor, not a second free rule.
* The Pricing-Endpoint Filter, whose per-row predicate lives in ``free_rule``
  while the network I/O stays in ``pipeline``.
"""
from __future__ import annotations

import httpx
import pytest

from llm_discovery import free_rule, gate, pipeline

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
# (model_id, expected_router). The negative rows isolate the auto+free
# branch: "auto" without "free" and "free" without "auto" are both not routers.
# Without these a mutation that drops either half of the branch survives.
ROUTER_CORPUS = [
    ("kilo-auto/free", True),
    ("openrouter/free", True),
    ("some-router-x", True),
    ("auto:free", True),
    ("myauto/free", True),
    ("ROUTER", True),
    ("gpt-4", False),
    ("", False),
    ("free/claude-opus-4.6", False),
    ("kilo-auto", False),
    ("auto-select", False),
    ("free-tier", False),
]

# (model_id, expected_free) — the discovery free-rule answer. Before #288 the
# gate disagreed on the PRE-288 rows; floor 3 now adopts this answer.
GATE_FREE_CORPUS = [
    ("gpt-4:free", True),
    ("gpt-4-free", True),
    ("gpt-4_free", True),
    ("model/free", True),
    ("gpt-4", False),
    ("premium-model", False),
    # PRE-288 DIVERGE — gate's regex was narrower: the apinex free/ prefix and
    # any non-terminal marker. Now free in the gate too.
    ("free/claude-opus-4.6", True),
    ("foo-free-bar", True),
    ("model:free/", True),
    ("free/", True),
    ("x-free-y", True),
    # PRE-288 DIVERGE — the old gate lowercased the id; markers are case-sensitive.
    ("GPT-4:FREE", False),
    ("FREE", False),
    ("free", False),
]

# The ids the pre-#288 gate predicate answered differently from the free rule.
# Pinned so the reconciliation is explicit: the gate must now agree on all.
_PRE_288_GATE_DIVERGENCE = {
    "free/claude-opus-4.6",
    "foo-free-bar",
    "model:free/",
    "free/",
    "x-free-y",
    "GPT-4:FREE",
    "FREE",
    "free",
}

# (pricing, gate floor-3 ok, free_rule free). The gate floor is
# "pricing present OR free", so blended/scalar zero pass via pricing-present
# even though the free rule rejects them (pinned by the last test below). The
# prompt/completion row is the #288 reconciliation: paid in the gate before,
# free on the discovery path, now free in both.
GATE_PRICING_CORPUS = [
    ({"prompt": 0, "completion": 0}, True, True),
    ({"price_1m_blended_3_to_1": 0}, True, True),
    ({"blended": 0}, True, False),
    ({"request": 0}, False, False),
    ({}, False, False),
    (0, True, False),
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
def test_is_free_matches_the_recorded_answer(label, model, provider, expected):
    assert free_rule.is_free(model, provider) is expected


@pytest.mark.parametrize("label,model,provider,expected", FREE_CORPUS, ids=_IDS)
def test_has_free_matches_the_recorded_answer(label, model, provider, expected):
    assert free_rule.has_free([model], provider) is expected


@pytest.mark.parametrize("label,model,provider,expected", FREE_CORPUS, ids=_IDS)
def test_split_matches_the_recorded_answer(label, model, provider, expected):
    """Paired with a known-paid sentinel: a free row keeps only itself.

    The sentinel is never free for any provider branch (no marker, no pricing,
    no access_tier/premium/tier), so ``any_free`` is True iff the corpus row is
    free. When nothing is free the whole list is kept (documented behaviour).
    """
    sentinel = {"id": "sentinel-paid"}
    keep, dropped = free_rule.split([dict(model), sentinel], provider or "")
    if expected:
        assert [m["id"] for m in keep] == [model["id"]]
        assert [m["id"] for m in dropped] == ["sentinel-paid"]
    else:
        assert [m["id"] for m in keep] == [model["id"], "sentinel-paid"]
        assert dropped == []


@pytest.mark.parametrize("label,provider,models", SPLIT_BATCHES, ids=[b[0] for b in SPLIT_BATCHES])
def test_split_partitions_on_the_is_free_predicate(label, provider, models):
    """When something is free the split partitions; when nothing is, all is kept."""
    pn = provider or None
    any_free = any(free_rule.is_free(m, pn) for m in models)
    keep, dropped = free_rule.split(list(models), provider)
    if not any_free:
        assert keep == models
        assert dropped == []
        return
    assert all(free_rule.is_free(m, pn) for m in keep)
    assert all(not free_rule.is_free(m, pn) for m in dropped)
    assert sorted(m["id"] for m in keep + dropped) == sorted(m["id"] for m in models)


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
@pytest.mark.parametrize("model_id,expected", ROUTER_CORPUS, ids=[repr(r[0]) for r in ROUTER_CORPUS])
def test_is_router_matches_the_recorded_answer(model_id, expected):
    assert free_rule.is_router(model_id) is expected


def test_is_router_is_false_on_none_and_empty():
    assert free_rule.is_router(None) is False
    assert free_rule.is_router("") is False


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
# Keeper-gate reconciliation (#288)
# --------------------------------------------------------------------------
def test_free_rule_matches_the_recorded_answers():
    for model_id, rule_answer in GATE_FREE_CORPUS:
        assert free_rule.is_free(model_id, None) is rule_answer, model_id


def _gate_floor3_record(pricing, model_id: str = "plain-model", **extra):
    """A record that passes every gate floor except (possibly) floor 3."""
    rec = {
        "model_id": model_id,
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
    rec.update(extra)
    return rec


def test_gate_free_floor_adopts_the_free_rule_on_every_recorded_id():
    """Floor 3 no longer answers for itself: gate == discovery free rule."""
    for model_id, expected in GATE_FREE_CORPUS:
        rec = _gate_floor3_record(None, model_id=model_id)
        ok, reason = gate.is_accurate_enough(rec)
        assert ok is expected, model_id
        assert reason == ("" if expected else "pricing missing and not free"), model_id


def test_pre_288_gate_divergence_is_closed():
    """Every id the old gate predicate disagreed on now agrees with the rule."""
    for model_id in _PRE_288_GATE_DIVERGENCE:
        rec = _gate_floor3_record(None, model_id=model_id)
        assert gate.is_accurate_enough(rec)[0] is free_rule.is_free(model_id, None), model_id


def test_gate_free_floor_sees_id_markers_the_old_predicate_missed():
    """The apinex free/ prefix and non-terminal markers are free in the gate."""
    for model_id in ("free/claude-opus-4.6", "foo-free-bar", "x-free-y"):
        assert gate.is_accurate_enough(_gate_floor3_record(None, model_id=model_id))[0] is True, model_id


def test_gate_free_floor_is_provider_scoped():
    """A model free for its provider on discovery is free inside the gate."""
    rec = _gate_floor3_record(None, model_id="navy-gpt-4", premium=False)
    assert gate.is_accurate_enough(rec, "navy_ai")[0] is True
    assert gate.is_accurate_enough(rec, "groq")[0] is False
    # results.py / backfill.py callers pass only the record: the provider may
    # come from the record itself.
    assert gate.is_accurate_enough({**rec, "provider": "navy_ai"})[0] is True


@pytest.mark.parametrize(
    "pricing,gate_ok,rule_free",
    GATE_PRICING_CORPUS,
    ids=[repr(row[0]) for row in GATE_PRICING_CORPUS],
)
def test_gate_pricing_floor_characterization(pricing, gate_ok, rule_free):
    ok, reason = gate.is_accurate_enough(_gate_floor3_record(pricing))
    assert ok is gate_ok
    assert reason == ("" if gate_ok else "pricing missing and not free")
    assert free_rule.is_free({"id": "plain-model", "pricing": pricing}, None) is rule_free


def test_gate_pricing_floor_adopts_the_free_rule_when_pricing_is_absent():
    """The prompt/completion shape the old gate missed is now free (#288)."""
    pricing = {"prompt": 0, "completion": 0}
    assert free_rule.is_free({"id": "plain-model", "pricing": pricing}, None) is True
    assert gate.is_accurate_enough(_gate_floor3_record(pricing))[0] is True
    assert gate.is_accurate_enough(_gate_floor3_record({"prompt": 1.0}))[0] is False


def test_gate_floor3_is_pricing_present_or_free_not_free_alone():
    """blended/scalar zero pass floor 3 via "pricing present", not the free rule.

    Pinned so the two axes are not conflated: the free rule rejects them, the
    gate still admits them because the floor is a disjunction.
    """
    for pricing in ({"blended": 0}, 0):
        assert free_rule.is_free({"id": "plain-model", "pricing": pricing}, None) is False
        assert gate.is_accurate_enough(_gate_floor3_record(pricing))[0] is True


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
