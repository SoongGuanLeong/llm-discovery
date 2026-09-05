"""Tests for bifrost generator pure transform."""

import pytest

from llm_discovery.bifrost.generator import (
    generate_bifrost_config,
    group_keeps_by_tier,
    load_keeps_from_results_dir,
    TIER_FLASH,
    TIER_MAX,
    TIER_CONTRIBUTOR_FREE,
    CLOUDFLARE_PROVIDER,
    CLOUDFLARE_API_KEY_VAR,
    CLOUDFLARE_ACCOUNT_ID_VAR,
)


class TestGroupKeepsByTier:
    def test_groups_by_pre_categorized_tier(self):
        keeps = [
            {"provider": "p1", "model_id": "m1", "tier": "flash"},
            {"provider": "p2", "model_id": "m2", "tier": "max"},
            {"provider": "p3", "model_id": "contributor-model", "tier": "contributor_free"},
        ]
        grouped = group_keeps_by_tier(keeps)
        assert len(grouped["flash"]) == 1
        assert len(grouped["max"]) == 1
        assert len(grouped["contributor_free"]) == 1

    def test_keep_all_no_dedup_across_providers(self):
        keeps = [
            {"provider": "p1", "model_id": "same-model", "tier": "flash"},
            {"provider": "p2", "model_id": "same-model", "tier": "flash"},
        ]
        grouped = group_keeps_by_tier(keeps)
        assert len(grouped["flash"]) == 2  # keep-all preserves both variants

    def test_strict_contributor_free_substring_filter(self):
        keeps = [
            {"provider": "p1", "model_id": "contributor-model", "tier": "contributor_free"},
            {"provider": "p2", "model_id": "free-model", "tier": "contributor_free"},
        ]
        grouped = group_keeps_by_tier(keeps, strict_contributor_free=True)
        assert len(grouped["contributor_free"]) == 1
        assert grouped["contributor_free"][0]["model_id"] == "contributor-model"

    def test_contributor_special_legacy_normalized(self):
        keeps = [
            {"provider": "p1", "model_id": "contributor-model", "tier": "contributor_special"},
        ]
        grouped = group_keeps_by_tier(keeps)
        assert len(grouped["contributor_free"]) == 1

    def test_non_tier_keeps_dropped(self):
        keeps = [
            {"provider": "p1", "model_id": "m1", "tier": "drop"},
            {"provider": "p2", "model_id": "m2", "tier": "error"},
            {"provider": "p3", "model_id": "m3", "tier": "uncertain"},
        ]
        grouped = group_keeps_by_tier(keeps)
        assert all(len(v) == 0 for v in grouped.values())


class TestGenerateBifrostConfig:
    def make_keeps(self):
        return [
            {"provider": "groq", "model_id": "llama-3.1-70b", "tier": "max"},
            {"provider": "groq", "model_id": "llama-3.1-8b", "tier": "flash"},
            {"provider": "cerebras", "model_id": "llama-3.1-70b", "tier": "max"},
            {"provider": "bazaarlink", "model_id": "auto:free", "tier": "max"},
            {"provider": "contrib", "model_id": "contributor-model", "tier": "contributor_free"},
        ]

    def make_catalog(self):
        return {
            "groq": {"base_url": "https://api.groq.com/openai/v1", "secret": "GROQ_API_KEY"},
            "cerebras": {"base_url": "https://api.cerebras.ai/v1", "secret": "CEREBRAS_API_KEY"},
            "bazaarlink": {"base_url": "https://api.bazaarlink.ai/v1", "secret": "BAZAARLINK_API_KEY"},
            "contrib": {"base_url": "https://api.contrib.ai/v1", "secret": "CONTRIB_API_KEY"},
            "cloudflare": {
                "base_url": "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1",
                "secret": "CLOUDFLARE_API_KEY",
            },
        }

    def test_basic_generation_with_all_keys(self):
        keeps = self.make_keeps()
        catalog = self.make_catalog()
        env = {"GROQ_API_KEY", "CEREBRAS_API_KEY", "BAZAARLINK_API_KEY", "CONTRIB_API_KEY"}

        result = generate_bifrost_config(keeps, catalog, env)

        config = result["config"]
        shim_map = result["shim_map"]
        skipped = result["skipped"]
        tier_counts = result["tier_counts"]
        empty_tiers = result["empty_tiers"]

        assert config["version"] == 2
        assert config["config_store"]["enabled"] is False
        assert "groq" in config["providers"]
        assert "cerebras" in config["providers"]
        assert "bazaarlink" in config["providers"]
        assert "contrib" in config["providers"]

        # Check provider structure
        groq_cfg = config["providers"]["groq"]
        assert groq_cfg["keys"][0]["value"] == "env.GROQ_API_KEY"
        assert groq_cfg["keys"][0]["weight"] == 1.0
        assert groq_cfg["network_config"]["max_retries"] == 3
        assert groq_cfg["custom_provider_config"]["base_provider_type"] == "openai"
        # Order: flash tier processed first, then max
        assert groq_cfg["keys"][0]["models"] == ["llama-3.1-8b", "llama-3.1-70b"]

        # Tier counts
        assert tier_counts["max"] == 3  # groq + cerebras + bazaarlink
        assert tier_counts["flash"] == 1  # groq
        assert tier_counts["contributor_free"] == 1  # contrib

        assert empty_tiers == []
        assert skipped == ["cloudflare"]  # missing both cloudflare vars

    def test_missing_env_key_skips_provider(self):
        keeps = self.make_keeps()
        catalog = self.make_catalog()
        env = {"GROQ_API_KEY"}  # only groq has key

        result = generate_bifrost_config(keeps, catalog, env)

        assert "groq" in result["config"]["providers"]
        assert "cerebras" not in result["config"]["providers"]
        assert "cerebras" in result["skipped"]
        assert "bazaarlink" in result["skipped"]
        assert "contrib" in result["skipped"]

    def test_cloudflare_dual_var_gate_both_required(self):
        keeps = [
            {"provider": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b", "tier": "flash"},
        ]
        catalog = {
            "cloudflare": {
                "base_url": "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1",
                "secret": "CLOUDFLARE_API_KEY",
            },
        }

        # Only API key present
        result = generate_bifrost_config(keeps, catalog, {"CLOUDFLARE_API_KEY"})
        assert "cloudflare" in result["skipped"]
        assert "cloudflare" not in result["config"]["providers"]

        # Only account ID present
        result = generate_bifrost_config(keeps, catalog, {"CLOUDFLARE_ACCOUNT_ID"})
        assert "cloudflare" in result["skipped"]

        # Both present
        result = generate_bifrost_config(keeps, catalog, {"CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID"})
        assert "cloudflare" in result["config"]["providers"]
        assert "cloudflare" not in result["skipped"]

    def test_empty_tier_detected(self):
        keeps = [
            {"provider": "groq", "model_id": "llama-3.1-70b", "tier": "max"},
        ]
        catalog = {"groq": {"base_url": "https://api.groq.com/openai/v1", "secret": "GROQ_API_KEY"}}
        env = {"GROQ_API_KEY"}

        result = generate_bifrost_config(keeps, catalog, env)

        assert result["tier_counts"]["flash"] == 0
        assert result["tier_counts"]["max"] == 1
        assert result["tier_counts"]["contributor_free"] == 0
        assert set(result["empty_tiers"]) == {"flash", "contributor_free"}

    def test_provider_with_no_keeps_omitted_not_skipped(self):
        keeps = [{"provider": "groq", "model_id": "m1", "tier": "max"}]
        catalog = {
            "groq": {"base_url": "https://api.groq.com/openai/v1", "secret": "GROQ_API_KEY"},
            "cerebras": {"base_url": "https://api.cerebras.ai/v1", "secret": "CEREBRAS_API_KEY"},
        }
        env = {"GROQ_API_KEY", "CEREBRAS_API_KEY"}

        result = generate_bifrost_config(keeps, catalog, env)

        assert "groq" in result["config"]["providers"]
        assert "cerebras" not in result["config"]["providers"]
        assert "cerebras" not in result["skipped"]  # not a missing-key skip

    def test_unknown_provider_in_keeps_skipped(self):
        keeps = [{"provider": "unknown", "model_id": "m1", "tier": "max"}]
        catalog = {"groq": {"base_url": "https://api.groq.com/openai/v1", "secret": "GROQ_API_KEY"}}
        env = {"GROQ_API_KEY"}

        result = generate_bifrost_config(keeps, catalog, env)

        assert "unknown" in result["skipped"]
        assert "unknown" not in result["config"]["providers"]

    def test_shim_map_only_includes_emitted_providers(self):
        keeps = [
            {"provider": "groq", "model_id": "m1", "tier": "max"},
            {"provider": "cerebras", "model_id": "m2", "tier": "max"},
        ]
        catalog = {
            "groq": {"base_url": "https://api.groq.com/openai/v1", "secret": "GROQ_API_KEY"},
            "cerebras": {"base_url": "https://api.cerebras.ai/v1", "secret": "CEREBRAS_API_KEY"},
        }
        env = {"GROQ_API_KEY"}  # cerebras missing

        result = generate_bifrost_config(keeps, catalog, env)

        assert result["shim_map"]["max"] == ["m1"]  # only groq models
        assert "m2" not in result["shim_map"]["max"]

    def test_strict_contributor_free_filters_zero_price(self):
        keeps = [
            {"provider": "p1", "model_id": "contributor-model", "tier": "contributor_free"},
            {"provider": "p2", "model_id": "free-model-zero-price", "tier": "contributor_free"},
        ]
        catalog = {
            "p1": {"base_url": "https://api.p1/v1", "secret": "P1_KEY"},
            "p2": {"base_url": "https://api.p2/v1", "secret": "P2_KEY"},
        }
        env = {"P1_KEY", "P2_KEY"}

        result = generate_bifrost_config(keeps, catalog, env, strict_contributor_free=True)

        assert len(result["shim_map"]["contributor_free"]) == 1
        assert result["shim_map"]["contributor_free"][0] == "contributor-model"

    def test_dedup_within_provider_preserves_order(self):
        keeps = [
            {"provider": "groq", "model_id": "model-a", "tier": "max"},
            {"provider": "groq", "model_id": "model-b", "tier": "max"},
            {"provider": "groq", "model_id": "model-a", "tier": "max"},  # duplicate
        ]
        catalog = {"groq": {"base_url": "https://api.groq.com/openai/v1", "secret": "GROQ_API_KEY"}}
        env = {"GROQ_API_KEY"}

        result = generate_bifrost_config(keeps, catalog, env)

        models = result["config"]["providers"]["groq"]["keys"][0]["models"]
        assert models == ["model-a", "model-b"]  # order preserved, deduped


class TestLoadKeepsFromResultsDir:
    def test_loads_keeps_from_yaml_files(self, tmp_path):
        # Create test YAML files
        (tmp_path / "p1.yaml").write_text("""
provider: p1
keep:
  - model_id: m1
    tier: flash
  - model_id: m2
    tier: max
drop_llm: []
error: []
""")
        (tmp_path / "p2.yaml").write_text("""
provider: p2
keep:
  - model_id: m3
    tier: contributor_free
drop_llm: []
error: []
""")

        keeps = load_keeps_from_results_dir(tmp_path)

        assert len(keeps) == 3
        providers = {k["provider"] for k in keeps}
        assert providers == {"p1", "p2"}

    def test_contributor_special_normalized_at_load(self, tmp_path):
        (tmp_path / "p1.yaml").write_text("""
provider: p1
keep:
  - model_id: m1
    tier: contributor_special
drop_llm: []
error: []
""")

        keeps = load_keeps_from_results_dir(tmp_path)
        assert keeps[0]["tier"] == "contributor_free"


class TestIntegrationScenarios:
    def test_fixture_126_provider_variants_kept(self):
        """Fixture with provider variants across duplicates — all retained (keep-all)."""
        keeps = []
        providers = [f"p{i}" for i in range(10)]
        for prov in providers:
            keeps.append({"provider": prov, "model_id": "shared-model", "tier": "max"})
            keeps.append({"provider": prov, "model_id": f"{prov}-unique", "tier": "flash"})
        # Add 6 more
        for i in range(6):
            keeps.append({"provider": f"extra{i}", "model_id": f"extra{i}", "tier": "max"})

        catalog = {}
        for k in keeps:
            catalog[k["provider"]] = {"base_url": f"https://{k['provider']}.ai/v1", "secret": f"{k['provider'].upper()}_KEY"}
        env = {f"{k['provider'].upper()}_KEY" for k in keeps}

        result = generate_bifrost_config(keeps, catalog, env)
        total = sum(result["tier_counts"].values())
        # 10 providers * 2 models each + 6 = 26 keeps total
        # But all providers have keys so all should be emitted
        assert total == 26
        assert result["tier_counts"]["max"] == 16  # 10 shared + 6 extra
        assert result["tier_counts"]["flash"] == 10  # 10 unique

    def test_free_vs_paid_tiers_handled(self):
        """Free models can be in max or flash, paid models respected by tier."""
        keeps = [
            {"provider": "freeprov", "model_id": "free-model", "tier": "max"},
            {"provider": "paidprov", "model_id": "paid-model", "tier": "max"},
            {"provider": "freeprov2", "model_id": "free-flash", "tier": "flash"},
        ]
        catalog = {k["provider"]: {"base_url": f"https://{k['provider']}.ai/v1", "secret": f"{k['provider'].upper()}_KEY"} for k in keeps}
        env = {f"{k['provider'].upper()}_KEY" for k in keeps}

        result = generate_bifrost_config(keeps, catalog, env)

        assert result["tier_counts"]["max"] == 2
        assert result["tier_counts"]["flash"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])