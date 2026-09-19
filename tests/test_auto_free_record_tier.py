"""Regression: auto-free providers (bazaarlink) always produce keep + tier flash.

bazaarlink has no /models enumeration; `pipeline.discover_provider` /
`discover_single` short-circuit `discovery_strategy == "bazaarlink"` to a single
synthetic `auto:free` record. That record used to claim `tier: "max"`, which both
put a routing fallback in the strategic-reserve band and contradicted the router
override in `policy_gate` (`_is_router_model` forces flash). It is now flash.

Seams: EvaluatorCoordinator._auto_free_record (the only definition),
ProviderBatchWriter.write (issue #247 gate demotion bypass for routers/auto-free).
"""
from llm_discovery.evaluator import EvaluatorCoordinator
from llm_discovery.policy_gate import _is_router_model


def _auto_free(provider_name="bazaarlink"):
    return EvaluatorCoordinator(
        provider_name=provider_name,
        aa=None,
        models_dev=None,
        evaluator=None,
        min_score=24.0,
        max_score=45.0,
    )._auto_free_record(provider_name)


def test_auto_free_record_is_keep_flash():
    rec = _auto_free()

    assert rec["provider_model_id"] == "auto:free"
    assert rec["decision"] == "keep"
    assert rec["tier"] == "flash"
    # Keeper-level evidence is deliberate: tier is the routing band, not evidence strength.
    assert rec["evidence_level"] == "strong"


def test_pipeline_auto_free_record_matches():
    rec = _auto_free()

    assert rec["decision"] == "keep"
    assert rec["tier"] == "flash"


def test_auto_free_id_takes_router_band():
    # The synthetic id satisfies the router predicate, so gate and pipeline agree on flash.
    assert _is_router_model("auto:free") is True


def test_write_keeps_auto_free_record():
    """Regression: write-time Accurate-Enough Gate must not demote auto:free.

    2026-09-18 run demoted bazaarlink's synthetic auto:free record to the
    uncertain bucket (`gate_reason: coding_score is null`), contradicting the
    keep+flash contract. Routers/auto-free are routing fallbacks, not coding
    Keepers: ADR 0006 tags them separately instead of gating them.
    """
    from llm_discovery.results import ProviderBatchWriter

    rec = _auto_free()
    rec["pricing"] = None
    out = ProviderBatchWriter().write(
        {"provider": "bazaarlink", "keep": [rec], "drop": [], "error": []},
        "bazaarlink",
    )
    import yaml

    payload = yaml.safe_load(out.read_text())
    assert payload["keep"], "auto:free demoted out of keep"
    kept = payload["keep"][0]
    assert kept["model_id"] == "auto:free"
    assert kept["decision"] == "keep"
    assert "gate_reason" not in kept


def test_write_keeps_other_router_ids():
    """Router-band ids (kilo-auto/free, openrouter/free) pass the gate too."""
    from llm_discovery.results import ProviderBatchWriter

    for mid in ("kilo-auto/free", "openrouter/free"):
        rec = {
            "provider_model_id": mid,
            "decision": "keep",
            "tier": "flash",
            "confidence": 1.0,
            "evidence_level": "strong",
            "evidence": ["Router model: always keep (routing meta-model)"],
            "pricing": None,
        }
        out = ProviderBatchWriter().write(
            {"provider": "kilo_ai", "keep": [rec], "drop": [], "error": []},
            "kilo_ai",
        )
        import yaml

        payload = yaml.safe_load(out.read_text())
        assert payload["keep"], f"{mid} demoted out of keep"
        assert payload["keep"][0]["model_id"] == mid
        assert payload["keep"][0]["decision"] == "keep"
