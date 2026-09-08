"""Tests for DSH wiring to Bifrost Model Groups — issue #156."""

import json
from pathlib import Path

import pytest
import yaml


def test_cordis_patch_example_exists_and_valid():
    p = Path("config/dsh/cordis.patch.yml.example")
    assert p.exists(), "config/dsh/cordis.patch.yml.example missing"
    data = yaml.safe_load(p.read_text())
    assert isinstance(data, list), "cordis patch should be YAML array"
    assert len(data) >= 1
    entry = data[0]
    assert entry.get("id") == "llm-pi-ai"
    providers = entry.get("config", {}).get("providers", {})
    assert "bifrost-shim" in providers
    assert "bifrost-direct" in providers
    shim = providers["bifrost-shim"]
    direct = providers["bifrost-direct"]
    # shim must use :8081, direct :8080
    assert shim.get("baseUrl") == "http://localhost:8081/v1"
    assert direct.get("baseUrl") == "http://localhost:8080/v1"
    # apiKeyEnv dummy
    assert shim.get("apiKeyEnv") == "BIFROST_API_KEY"
    assert direct.get("apiKeyEnv") == "BIFROST_API_KEY"
    # models include flash/max/contributor_free
    shim_ids = {m.get("id") for m in shim.get("models", [])}
    assert {"flash", "max", "contributor_free"} <= shim_ids
    # no provider key leaked inline
    txt = p.read_text()
    assert "sk-" not in txt.lower()
    assert "api_key" not in txt.lower() or "apiKeyEnv" in txt


def test_settings_example_exists_and_valid():
    p = Path("config/dsh/settings.yaml.example")
    assert p.exists()
    data = yaml.safe_load(p.read_text())
    assert "llm-pi-ai" in data
    providers = data["llm-pi-ai"].get("providers", {})
    assert "bifrost-shim" in providers
    assert "bifrost-direct" in providers
    shim = providers["bifrost-shim"]
    assert shim.get("baseUrl") == "http://localhost:8081/v1"
    assert shim.get("apiKeyEnv") == "BIFROST_API_KEY"
    shim_ids = {m.get("id") for m in shim.get("models", [])}
    assert {"flash", "max", "contributor_free"} <= shim_ids
    direct = providers["bifrost-direct"]
    assert direct.get("baseUrl") == "http://localhost:8080/v1"
    txt = p.read_text()
    assert "sk-" not in txt.lower()


def test_dsh_wiring_doc_exists_and_covers_contract():
    p = Path("docs/dsh-bifrost-wiring.md")
    assert p.exists(), "docs/dsh-bifrost-wiring.md missing"
    txt = p.read_text()
    # must document both baseUrls
    assert "http://localhost:8081/v1" in txt
    assert "http://localhost:8080/v1" in txt
    # must document apiKeyEnv dummy
    assert "BIFROST_API_KEY" in txt
    assert "sk-bifrost-dummy" in txt
    # must mention both config paths
    assert "settings.yaml" in txt
    assert "cordis.patch.yml" in txt
    # must mention window.__DSH_BOOT__ and dump-config
    assert "window.__DSH_BOOT__" in txt
    assert "--dump-config" in txt
    # must mention no provider key in DSH
    assert "bifrost.env" in txt
    # must mention shim sidecar runner
    assert "run_sidecar.sh" in txt


def test_bifrost_deployment_links_to_dsh_wiring():
    txt = Path("docs/bifrost-deployment.md").read_text()
    assert "dsh-bifrost-wiring.md" in txt
    assert "config/dsh/" in txt


def test_no_secret_in_example_configs():
    for rel in ["config/dsh/cordis.patch.yml.example", "config/dsh/settings.yaml.example"]:
        txt = Path(rel).read_text()
        # ensure never inline secret env var value
        assert "env.VAR" not in txt or "BIFROST_API_KEY" in txt  # only dummy
        # config must not contain actual provider env names with values
        low = txt.lower()
        assert "ag_ex" not in low
        # basic yaml parse already done above, so just check no hardcoded keys
        assert "gsk_" not in low
