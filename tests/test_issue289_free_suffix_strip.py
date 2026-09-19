"""Issue #289 — migrate the free-suffix strip onto one shared helper.

Phase 2 of #285: the three identical ``re.sub(r"[:/_-]free$", "", v)`` calls in
``evidence_identity.canonical_key``, the free-marker strip in
``evidence_utils.clean_evidence``, the alias lookup in
``model_matching.resolve_model`` and a fourth verbatim copy in
``evidence_collector``'s models.dev fallback all call
``free_rule.strip_free_suffix``.

``canonical_key`` is the identity layer, so its output must be byte-identical:
the corpus below pins the store-key answers (including the provider-namespace
and stepfun rows) before and after the migration. The behaviours are asserted,
not the source text — the helper could move and these tests still hold.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from llm_discovery import free_rule
from llm_discovery.evidence_identity import canonical_key
from llm_discovery.evidence_utils import clean_evidence
from llm_discovery.model_matching import resolve_model

FREE_SUFFIX_MARKERS = (":free", "-free", "_free", "/free")


# --------------------------------------------------------------------------
# The helper itself
# --------------------------------------------------------------------------
@pytest.mark.parametrize("marker", FREE_SUFFIX_MARKERS)
def test_strip_free_suffix_strips_each_trailing_marker(marker):
    assert free_rule.strip_free_suffix("model" + marker) == "model"


@pytest.mark.parametrize("marker", FREE_SUFFIX_MARKERS)
def test_strip_free_suffix_is_case_insensitive(marker):
    assert free_rule.strip_free_suffix("Model" + marker.upper()) == "Model"


def test_strip_free_suffix_leaves_non_suffixes_alone():
    # The prefix form is not a suffix.
    assert free_rule.strip_free_suffix("free/model") == "free/model"
    # A marker in the middle is not a suffix.
    assert free_rule.strip_free_suffix("model-free-x") == "model-free-x"
    # "free" without a preceding separator is not a marker.
    assert free_rule.strip_free_suffix("modelfree") == "modelfree"
    assert free_rule.strip_free_suffix("model") == "model"
    assert free_rule.strip_free_suffix("") == ""


# --------------------------------------------------------------------------
# canonical_key — byte-identical over the alias / store-key corpus
# --------------------------------------------------------------------------
CANONICAL_CORPUS = [
    # provider namespaces (normalize_store_key corpus)
    ("openai/gpt-4o:free", "gpt-4o"),
    ("MiniMax/MiniMax-M3", "minimax-m3"),
    ("meta/muse-spark-1.2", "muse-spark-1.2"),
    ("a/gpt-4o:free", "gpt-4o"),
    ("b/gpt-4o", "gpt-4o"),
    ("quantized/model-free", "model"),
    ("provider/model/free", "model"),
    # free variants
    ("minimax-m3-free", "minimax-m3"),
    ("minimax-m3:free", "minimax-m3"),
    ("minimax-m3_free", "minimax-m3"),
    ("minimax-m3/free", "minimax-m3"),
    # stepfun -> step
    ("stepfun/step-2.5-free", "step-2.5"),
    ("stepfun-2.5-free", "step-2.5"),
    ("step/step-2.5", "step-2.5"),
    ("STEPFUN-2.5_FREE", "step-2.5"),
    # vendor prefixes and suffixes kept
    ("muse-spark-1.2-contributor-free", "muse-spark-1.2-contributor"),
    ("qwen3.8-flash-free", "qwen3.8-flash"),
    ("coding-glm-4.6-free", "glm-4.6"),
    ("xiaomi-mimo-v2.5-free", "mimo-v2.5"),
    ("nvidia-llama-3.3-70b-free", "llama-3.3-70b"),
    # alias corpus (test_evidence_identity_aliases)
    ("glm-5.3", "glm-5.3"),
    ("glm-5-3", "glm-5-3"),
    ("mimo-v2.5", "mimo-v2.5"),
    ("mimo-v2-5-0424", "mimo-v2-5-0424"),
    ("xiaomi-mimo-v2-5-0424", "mimo-v2-5-0424"),
    ("claude-3-5-sonnet", "claude-3-5-sonnet"),
    ("claude-sonnet-3-5", "claude-sonnet-3-5"),
    ("gemini-2.5-pro-preview-06-05", "gemini-2.5-pro-preview-06-05"),
    ("gpt-4", "gpt-4"),
    ("gpt-4o", "gpt-4o"),
    # markers that are not trailing are left for the later normalisation pass
    ("free/model", "model"),
    ("model-freedom", "model-freedom"),
    ("x-free-y", "x-free-y"),
    ("gpt-4-free-2", "gpt-4-free-2"),
    ("a-free/free", "a"),
    ("", ""),
]


@pytest.mark.parametrize(
    "model_id,expected", CANONICAL_CORPUS, ids=[c[0] or "<empty>" for c in CANONICAL_CORPUS]
)
def test_canonical_key_is_byte_identical(model_id, expected):
    assert canonical_key(model_id) == expected


# --------------------------------------------------------------------------
# The other two call sites
# --------------------------------------------------------------------------
@pytest.mark.parametrize("marker", FREE_SUFFIX_MARKERS)
def test_clean_evidence_strips_the_free_suffix(marker):
    assert clean_evidence([f"https://example.com/model{marker}"]) == ["https://example.com/model"]


def test_clean_evidence_still_drops_free_model_rule_noise():
    assert clean_evidence(["free-model-rule"]) == []
    assert clean_evidence(None) == []
    assert clean_evidence([]) == []


@pytest.mark.parametrize(
    "model_id", ["mimo-v2.5-free", "mimo-v2.5:free", "mimo-v2.5_free", "MIMO-V2.5-FREE"]
)
def test_alias_lookup_strips_the_free_suffix(model_id):
    aa = SimpleNamespace(models=[{"id": "aa-mimo", "slug": "mimo-v2-5-0424", "name": "Mimo"}])
    res = resolve_model(model_id, aa)
    assert res.aa_model is not None
    assert res.method == "alias_mimo-v2-5-0424"


# --------------------------------------------------------------------------
# A fourth verbatim copy: evidence_collector's models.dev fallback
# --------------------------------------------------------------------------
class _FakeModelsDev:
    def __init__(self, models):
        self._models = models

    def get_model(self, model_id):
        return self._models.get(model_id)


@pytest.mark.parametrize("marker", FREE_SUFFIX_MARKERS)
def test_evidence_collector_falls_back_to_the_stripped_id(marker):
    from llm_discovery.evidence_collector import EvidenceCollector

    models_dev = _FakeModelsDev(
        {"codestar-pro": {"id": "codestar-pro", "name": "CodeStar Pro", "description": "A coding model"}}
    )
    packet = EvidenceCollector("acme").collect(
        {"id": "codestar-pro" + marker}, None, models_dev, SimpleNamespace(aa_model=None)
    )
    assert [(c.source, c.claim) for c in packet.provider_claims] == [("models_dev", "A coding model")]
