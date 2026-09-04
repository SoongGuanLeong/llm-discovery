"""Issue #51 (TDD) - NaraRouter corrected pipeline, offline.

Pins the invariants the issue demands and guards the #50/#52 fixes against
regression. Runs on captured data (no network, no secrets).
"""
import yaml

from llm_discovery.discovery import NARAROUTER_FREE_SNAPSHOT
from llm_discovery.model_matching import normalize_model_id
from llm_discovery.pipeline import _split_by_free_rule
from scripts.nararouter_issue51_prototype import (
    allowlist_from_plans,
    load_captured_raw,
    mapping_matrix,
    run_corrected_pipeline,
    before_filter,
    build_caches,
    write_after_yaml,
)

EXPECTED_TRUE_FREE = {
    "agnes-2.0-flash", "agnes-2.5-flash", "laguna-s-2.1", "minimax-m3-free",
    "mistral-large", "mistral-medium-3-5", "muse-spark-1.2-contributor-free",
    "qwen3.8-27b", "stepfun-3.7-flash",
}
PAID_GATED = {
    "deepseek-v4-flash-free", "glm-5.3-flash-free", "glm-5.3-free",
    "mimo-v2.5-free", "muse-spark-1.3-contributor-free", "qwen3.8-flash-free",
}
# issue #51 target vendor families -> canonical AA slugs
EXPECTED_AA = {
    "mimo-v2.5-free": "mimo-v2-5-0424",
    "minimax-m3-free": "minimax-m3",
    "muse-spark-1.2-contributor-free": "muse-spark-1-2",
    "qwen3.8-27b": "qwen3-8-27b",
}


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
    def test_allowlist_equals_free_plan(self):
        assert allowlist_from_plans() == EXPECTED_TRUE_FREE

    def test_snapshot_matches_free_plan(self):
        assert NARAROUTER_FREE_SNAPSHOT == EXPECTED_TRUE_FREE

    def test_raw_has_59_models_8_free_suffixed(self):
        raw = load_captured_raw()
        assert len(raw) == 59
        free = [m["id"] for m in raw if m["id"].endswith("-free")]
        assert len(free) == 8  # 2 true-free + 6 paid-gated

    def test_corrected_filter_keeps_9_true_free_excludes_6_paid_gated(self):
        raw = load_captured_raw()
        allow = allowlist_from_plans()
        kept = {m["id"] for m in raw if m["id"] in allow}
        assert kept == EXPECTED_TRUE_FREE
        excluded = {m["id"] for m in raw if m["id"].endswith("-free") and m["id"] not in allow}
        assert excluded == PAID_GATED

    def test_legacy_filter_keeps_paid_gated_included(self):
        """Before #52, _split_by_free_rule kept ALL free-marker ids (incl paid-gated)."""
        raw = load_captured_raw()
        kept, dropped = before_filter(raw)
        assert set(PAID_GATED).issubset(set(kept))
        assert {"agnes-2.0-flash", "laguna-s-2.1", "mistral-large", "qwen3.8-27b"}.issubset(set(dropped))


class TestNaraRouterResolution:
    def test_resolution_matrix(self):
        aa, md, cache = build_caches()
        raw = load_captured_raw()
        rows = {r["provider_id"]: r for r in mapping_matrix(raw, aa, md, cache)}
        for pid, expected_aa in EXPECTED_AA.items():
            row = rows[pid]
            assert row["aa_slug"] == expected_aa, (pid, row)
            assert row["aa_score"] is not None and row["aa_score"] >= 24
            assert row["method"] != "unresolved"

    def test_paid_gated_models_also_resolve_correctly(self):
        aa, md, cache = build_caches()
        raw = load_captured_raw()
        rows = {r["provider_id"]: r for r in mapping_matrix(raw, aa, md, cache)}
        assert rows["glm-5.3-free"]["aa_slug"] == "glm-5-3"
        assert rows["glm-5.3-flash-free"]["aa_slug"] == "glm-5-3-flash"
        assert rows["deepseek-v4-flash-free"]["aa_slug"] == "deepseek-v4-flash"
        assert rows["qwen3.8-flash-free"]["aa_slug"] == "qwen3-8-flash-next"

    def test_no_unresolved_for_named_families(self):
        aa, md, cache = build_caches()
        raw = load_captured_raw()
        rows = mapping_matrix(raw, aa, md, cache)
        for r in rows:
            if r["method"] == "unresolved":
                # unresolved only acceptable for models genuinely absent from AA
                assert r["provider_id"].startswith("agnes") or r["provider_id"].startswith("stepfun")


class TestPipelineEndToEndDeterministic:
    def test_run_produces_9_buckets(self):
        raw = load_captured_raw()
        allow = allowlist_from_plans()
        aa, md, cache = build_caches()
        buckets = run_corrected_pipeline(raw, aa, md, cache, allow)
        total = sum(len(v) for v in buckets.values())
        assert total == 9
        keep_ids = {r["provider_model_id"] for r in buckets["keep"]}
        assert "minimax-m3-free" in keep_ids
        assert "muse-spark-1.2-contributor-free" in keep_ids
        assert "qwen3.8-27b" in keep_ids  # ADR 0003 vision exception: coding-capable + cheap bypasses deterministic drop
        drop_ids = {r["provider_model_id"] for r in buckets["drop_llm"]}
        assert "qwen3.8-27b" not in drop_ids
        # regression: pure non-coding still dropped
        assert "agnes-2.0-flash" in drop_ids
        for rec in buckets["keep"]:
            if rec.get("aa_model_id") is not None:
                assert rec.get("aa_score") is not None

    def test_after_yaml_schema_matches_before(self):
        from scripts.nararouter_issue51_prototype import OUT_DIR
        raw = load_captured_raw()
        allow = allowlist_from_plans()
        aa, md, cache = build_caches()
        buckets = run_corrected_pipeline(raw, aa, md, cache, allow)
        path = write_after_yaml(buckets)
        data = yaml.safe_load(path.read_text())
        assert set(data.keys()) == {"provider", "evaluated_at", "keep", "drop_llm", "error"}
        assert data["provider"] == "nararouter"
        assert len(data["keep"]) + len(data["drop_llm"]) + len(data["error"]) == 9

