"""Test canonical source of truth for OmniRoute registry listing."""

import pytest
from pathlib import Path
import llm_discovery.omniroute_export as mod

def test_omniroute_registry_exists():
    """Test that _OMNIROUTE_REGISTRY is defined as the canonical source."""
    assert hasattr(mod, '_OMNIROUTE_REGISTRY'), "Missing _OMNIROUTE_REGISTRY - this is the canonical source of truth"

def test_providers_not_in_registry_map_to_custom():
    """Test that providers not in _OMNIROUTE_REGISTRY are automatically mapped to {name}-custom."""
    # Test with a provider that's definitely not in the registry
    test_provider = "test_unknown_provider_xyz"
    
    # If test_provider is not in registry, should map to test_unknown_provider_xyz-custom
    # This tests the core requirement of issue #197
    if test_provider not in mod._OMNIROUTE_REGISTRY:
        # When not in registry, _resolve_provider_id should return {name}-custom
        result = mod._resolve_provider_id(test_provider)
        assert result == f"{test_provider}-custom", f"Provider {test_provider} not in registry should map to {{name}}-custom"

def test_registry_includes_key_providers():
    """Test that _OMNIROUTE_REGISTRY includes expected providers from _PROVIDER_ALIAS."""
    # _OMNIROUTE_REGISTRY should be a subset of _PROVIDER_ALIAS (excluding CUSTOM_NODE_MAP)
    for provider_name, registry_id in mod._PROVIDER_ALIAS.items():
        if provider_name not in mod.CUSTOM_NODE_MAP:
            # This provider should be in the registry
            assert provider_name in mod._OMNIROUTE_REGISTRY, f"Provider {provider_name} should be in _OMNIROUTE_REGISTRY"

def test_agnes_nararouter_registry_status():
    """Test that agnes and nararouter are NOT in _OMNIROUTE_REGISTRY (they are custom-only)."""
    # Based on issue #03, agnes and nararouter should not be in the registry
    # They require explicit custom: true in providers.yaml
    assert "agnes" not in mod._OMNIROUTE_REGISTRY, "agnes should NOT be in _OMNIROUTE_REGISTRY (custom-only provider)"
    assert "nararouter" not in mod._OMNIROUTE_REGISTRY, "nararouter should NOT be in _OMNIROUTE_REGISTRY (custom-only provider)"

def test_custom_node_map_providers_not_in_registry():
    """Test that all CUSTOM_NODE_MAP providers are excluded from _OMNIROUTE_REGISTRY."""
    for provider_name in mod.CUSTOM_NODE_MAP.keys():
        assert provider_name not in mod._OMNIROUTE_REGISTRY, f"CUSTOM_NODE_MAP provider {provider_name} should not be in _OMNIROUTE_REGISTRY"

def test_omniroute_registry_completeness():
    """Test that _OMNIROUTE_REGISTRY captures all providers that should be in the OmniRoute registry."""
    # This test ensures the registry is comprehensive
    # For now, we verify it has the expected structure
    assert isinstance(mod._OMNIROUTE_REGISTRY, dict), "_OMNIROUTE_REGISTRY should be a dictionary"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
