"""Issue #51 - NaraRouter free-plan allowlist, against production code.

Retargeted from the retired prototype adapter: the two allowlist tests now
exercise ``llm_discovery.discovery.get_nararouter_free_allowlist`` directly,
monkeypatching the ``httpx`` boundary (production calls ``httpx.get`` inline
with no injectable parameter, so httpx is the correct seam). The vendor-suffix
normalization tests and the snapshot-equals-expected assertion already import
production modules and are unchanged.

No network access: every test either stubs ``httpx.get`` or reads the in-code
snapshot. Tests that depended on the gitignored ``data/nararouter_raw.json``
capture were removed — their evidence lives in the issue #51 research document
and in git history.
"""
from llm_discovery.discovery import (
    NARAROUTER_FREE_SNAPSHOT,
    get_nararouter_free_allowlist,
)
from llm_discovery.model_matching import normalize_model_id

EXPECTED_TRUE_FREE = {
    "agnes-2.0-flash", "agnes-2.5-flash", "laguna-s-2.1", "minimax-m3-free",
    "mistral-large", "mistral-medium-3-5", "muse-spark-1.2-contributor-free",
    "qwen3.8-27b", "stepfun-3.7-flash",
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class TestNormalizeStripsVendorSuffixes:
    """Issue #50: normalize must strip free markers before alias lookup."""

    def test_strips_free_marker(self):
        for raw in ["minimax-m3-free", "mimo-v2.5-free", "mimo-v2.5:free", "mimo-v2.5_free"]:
            assert "free" not in normalize_model_id(raw)

    def test_keeps_version_dots(self):
        # qwen3.8-27b -> qwen-3.8-27b : dot in 3.8 preserved (version dot, not split)
        n = normalize_model_id("qwen3.8-27b")
        assert "qwen" in n and "27b" in n and "." in n

    def test_contributor_only_free_stripped(self):
        # public normalizer strips -free but not -contributor (matcher handles contributor)
        n = normalize_model_id("muse-spark-1.2-contributor-free")
        assert "free" not in n
        assert "contributor" in n


class TestNaraRouterFreeFilter:
    def test_allowlist_falls_back_to_snapshot_when_fetch_fails(self, monkeypatch):
        # Production swallows network/parse errors and returns the in-code
        # snapshot (== EXPECTED_TRUE_FREE). No network access.
        def _boom(*args, **kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr("llm_discovery.discovery.httpx.get", _boom)
        assert get_nararouter_free_allowlist() == EXPECTED_TRUE_FREE

    def test_allowlist_parses_free_plan_models(self, monkeypatch, tmp_path):
        # Parsing logic, deterministically exercised with a synthetic plans
        # payload: pick the plan whose code == "free" and take its models.
        # chdir keeps the best-effort audit artifact out of the repo tree.
        monkeypatch.chdir(tmp_path)
        payload = {
            "data": [
                {"code": "plus", "models": ["plus-model"]},
                {"code": "free", "models": ["foo-free", "bar-free"]},
            ]
        }
        monkeypatch.setattr(
            "llm_discovery.discovery.httpx.get",
            lambda *args, **kwargs: _FakeResponse(payload),
        )
        assert get_nararouter_free_allowlist() == {"foo-free", "bar-free"}

    def test_snapshot_matches_free_plan(self):
        assert NARAROUTER_FREE_SNAPSHOT == EXPECTED_TRUE_FREE
