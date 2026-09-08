"""Scaffold tests for OmniRoute export generator (ticket 165)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import llm_discovery.omniroute_export as mod


def test_scaffold_payload_shape():
    payload = mod.generate_scaffold_payload()
    assert set(payload.keys()) == {"import", "combos", "meta"}
    assert payload["import"] == []
    assert isinstance(payload["combos"], list)
    assert len(payload["combos"]) == 3
    names = {c["name"] for c in payload["combos"]}
    assert names == {"flash", "max", "contributor_free"}
    for c in payload["combos"]:
        assert set(c.keys()) == {"name", "models", "strategy", "config"}
        assert c["models"] == []
        assert c["strategy"] == "reset-aware"
        assert c["config"] == {}
    assert payload["meta"] == {"scaffold": True, "version": 0}


def test_deterministic_output():
    a = json.dumps(mod.generate_scaffold_payload(), sort_keys=True)
    b = json.dumps(mod.generate_scaffold_payload(), sort_keys=True)
    assert a == b


def test_write_scaffold_files(tmp_path: Path):
    out = tmp_path / "derived"
    paths = mod.write_scaffold_files(out)
    assert paths["import"].exists()
    assert paths["combos"].exists()
    import_data = json.loads(paths["import"].read_text())
    combos_data = json.loads(paths["combos"].read_text())
    assert import_data == []
    assert len(combos_data) == 3
    # Deterministic: second write byte-identical
    first_import = paths["import"].read_bytes()
    first_combos = paths["combos"].read_bytes()
    mod.write_scaffold_files(out)
    assert paths["import"].read_bytes() == first_import
    assert paths["combos"].read_bytes() == first_combos


def test_cli_dry_run_exits_zero(tmp_path: Path):
    out = tmp_path / "derived"
    result = subprocess.run(
        [sys.executable, "-m", "llm_discovery.omniroute_export", "--dry-run", "--output-dir", str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert "import" in data and "combos" in data
    assert (out / "omniroute_import.json").exists()
    assert (out / "omniroute_combos.json").exists()


def test_cli_missing_inputs_nonzero(tmp_path: Path):
    missing_providers = tmp_path / "no_such_providers.yaml"
    missing_results = tmp_path / "no_such_results"
    result = subprocess.run(
        [sys.executable, "-m", "llm_discovery.omniroute_export",
         "--providers", str(missing_providers),
         "--results-dir", str(missing_results),
         "--output-dir", str(tmp_path / "out")],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "not found" in result.stderr.lower() or "hint" in result.stderr.lower()


def test_fixtures_exist():
    assert Path("tests/fixtures/omniroute/providers.yaml").exists()
    assert Path("tests/fixtures/omniroute/results/groq.yaml").exists()
    assert Path("tests/fixtures/omniroute/results/cerebras.yaml").exists()
    # baseUrl verbatim template preserved
    txt = Path("tests/fixtures/omniroute/providers.yaml").read_text()
    assert "${CLOUDFLARE_ACCOUNT_ID}" in txt

# === Ticket 166: Provider import file from config/providers.yaml ===

def test_build_import_entries_fixture_shape():
    rows = mod.build_import_entries(Path("tests/fixtures/omniroute/providers.yaml"))
    assert len(rows) == 3
    # sorted provider order
    assert [r["provider"] for r in rows] == ["cloudflare", "custom_openai", "groq"]
    for r in rows:
        assert set(r.keys()) >= {"provider", "name", "apiKey"}
        assert r["provider"] == r["name"]
        assert r["apiKey"].startswith("env:")
        # no raw key leaked — placeholder only, should not contain actual key value pattern beyond env: prefix
        assert "sk-" not in r["apiKey"]
        # baseUrl present for all fixture rows
        assert "baseUrl" in r
    # exact placeholder values
    by_provider = {r["provider"]: r for r in rows}
    assert by_provider["groq"]["apiKey"] == "env:GROQ_API_KEY"
    assert by_provider["groq"]["baseUrl"] == "https://api.groq.com/openai/v1"
    assert by_provider["custom_openai"]["apiKey"] == "env:CUSTOM_API_KEY"
    assert by_provider["custom_openai"]["baseUrl"] == "https://custom.example.com/v1"


def test_build_import_entries_baseurl_verbatim():
    rows = mod.build_import_entries(Path("tests/fixtures/omniroute/providers.yaml"))
    by_provider = {r["provider"]: r for r in rows}
    # cloudflare template left unresolved verbatim
    assert by_provider["cloudflare"]["baseUrl"] == "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1"
    assert "${CLOUDFLARE_ACCOUNT_ID}" in by_provider["cloudflare"]["baseUrl"]


def test_build_import_entries_unmapped_custom_emitted():
    rows = mod.build_import_entries(Path("tests/fixtures/omniroute/providers.yaml"))
    # custom_openai not in OmniRoute 352 registry but still emitted (OpenAI-compatible fallback)
    providers = {r["provider"] for r in rows}
    assert "custom_openai" in providers
    # no client-side registry validation — no error, no filtering
    assert len(rows) == 3


def test_build_import_entries_deterministic_idempotent():
    a = mod.build_import_entries(Path("tests/fixtures/omniroute/providers.yaml"))
    b = mod.build_import_entries(Path("tests/fixtures/omniroute/providers.yaml"))
    assert a == b
    # byte-identical via json dump with sorted keys
    ja = json.dumps(a, indent=2, sort_keys=True)
    jb = json.dumps(b, indent=2, sort_keys=True)
    assert ja == jb
    # ordering stable: alphabetical
    assert [r["provider"] for r in a] == sorted(r["provider"] for r in a)


def test_build_import_entries_real_providers_count(tmp_path: Path):
    # real config has 21 providers, each one row
    rows = mod.build_import_entries(Path("config/providers.yaml"))
    assert len(rows) == 21
    assert [r["provider"] for r in rows] == sorted(r["provider"] for r in rows)
    # spot check: cloudflare verbatim, groq present
    by_provider = {r["provider"]: r for r in rows}
    assert "${CLOUDFLARE_ACCOUNT_ID}" in by_provider["cloudflare"]["baseUrl"]
    assert by_provider["groq"]["baseUrl"] == "https://api.groq.com/openai/v1"


def test_generate_payload_import_snapshot(tmp_path: Path):
    payload = mod.generate_payload(Path("tests/fixtures/omniroute/providers.yaml"))
    assert "import" in payload and "combos" in payload
    assert len(payload["import"]) == 3
    # snapshot: exact expected import rows for fixture
    expected = [
        {
            "apiKey": "env:CLOUDFLARE_API_KEY",
            "baseUrl": "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1",
            "name": "cloudflare",
            "provider": "cloudflare",
        },
        {
            "apiKey": "env:CUSTOM_API_KEY",
            "baseUrl": "https://custom.example.com/v1",
            "name": "custom_openai",
            "provider": "custom_openai",
        },
        {
            "apiKey": "env:GROQ_API_KEY",
            "baseUrl": "https://api.groq.com/openai/v1",
            "name": "groq",
            "provider": "groq",
        },
    ]
    assert payload["import"] == expected
    # deterministic file write
    paths = mod.write_payload_files(payload, tmp_path)
    assert json.loads(paths["import"].read_text()) == expected
    first = paths["import"].read_bytes()
    mod.write_payload_files(payload, tmp_path)
    assert paths["import"].read_bytes() == first


def test_cli_dry_run_writes_import_file_fixture(tmp_path: Path):
    out = tmp_path / "derived"
    result = subprocess.run(
        [sys.executable, "-m", "llm_discovery.omniroute_export", "--dry-run",
         "--providers", "tests/fixtures/omniroute/providers.yaml",
         "--output-dir", str(out)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert len(data["import"]) == 3
    # file matches stdout, sorted keys, deterministic
    file_data = json.loads((out / "omniroute_import.json").read_text())
    assert file_data == data["import"]
    assert file_data[0]["provider"] == "cloudflare"
    assert "${CLOUDFLARE_ACCOUNT_ID}" in file_data[0]["baseUrl"]
    # no raw secrets logged
    assert "sk-" not in result.stdout and "sk-" not in result.stderr


# === Ticket 176: Connection provisioning ===


def test_custom_node_mapping():
    """Import-row builder maps nararouter/zai/agnes to custom OpenAI-compatible node ids."""
    rows = mod.build_import_entries(Path("config/providers.yaml"))
    by_name = {r["name"]: r for r in rows}
    assert by_name["nararouter"]["provider"] == "openai-compatible-nara"
    assert by_name["zai"]["provider"] == "openai-compatible-zai"
    assert by_name["agnes"]["provider"] == "openai-compatible-agnes"
    # connection names keep yaml names
    assert by_name["nararouter"]["name"] == "nararouter"
    assert by_name["zai"]["name"] == "zai"
    assert by_name["agnes"]["name"] == "agnes"


def test_opencode_zen_mapping():
    """opencode_zen maps to opencode-zen registry id (not free opencode)."""
    rows = mod.build_import_entries(Path("config/providers.yaml"))
    by_name = {r["name"]: r for r in rows}
    assert by_name["opencode_zen"]["provider"] == "opencode-zen"
    assert by_name["opencode_zen"]["name"] == "opencode_zen"


def test_untouched_providers_unchanged():
    """Providers without custom mapping keep their original id."""
    rows = mod.build_import_entries(Path("config/providers.yaml"))
    by_name = {r["name"]: r for r in rows}
    assert by_name["groq"]["provider"] == "groq"
    assert by_name["openrouter"]["provider"] == "openrouter"
    assert by_name["mistral"]["provider"] == "mistral"


def test_retired_provider_ids_exact():
    """Retired ids are exactly the four frozen registry + free opencode."""
    assert mod.RETIRED_PROVIDER_IDS == frozenset({"nara", "zai", "agnes", "opencode"})


def test_retired_ids_never_recreated():
    """Retired provider ids must not appear in import rows."""
    rows = mod.build_import_entries(Path("config/providers.yaml"))
    providers = {r["provider"] for r in rows}
    assert "nara" not in providers
    assert "zai" not in providers
    assert "agnes" not in providers
    assert "opencode" not in providers


def test_custom_node_row_shape():
    """Custom-node rows have correct shape (provider, name, apiKey, baseUrl)."""
    rows = mod.build_import_entries(Path("config/providers.yaml"))
    by_name = {r["name"]: r for r in rows}
    nara = by_name["nararouter"]
    assert set(nara.keys()) >= {"provider", "name", "apiKey", "baseUrl"}
    assert nara["provider"] == "openai-compatible-nara"
    assert nara["name"] == "nararouter"
    assert nara["apiKey"] == "env:NARAROUTER_API_KEY"
    assert nara["baseUrl"] == "https://router.bynara.id/v1"


def test_cloudflare_account_id_resolution():
    """Cloudflare accountId resolved from env at apply time."""
    rows = mod.build_import_entries(Path("config/providers.yaml"))
    by_name = {r["name"]: r for r in rows}
    cf = by_name["cloudflare"]
    # baseUrl template preserved in import file
    assert "${CLOUDFLARE_ACCOUNT_ID}" in cf["baseUrl"]
    # resolved at apply time
    resolved = mod.resolve_import_secrets(rows, env={"CLOUDFLARE_ACCOUNT_ID": "test-account-123"})
    cf_resolved = {r["name"]: r for r in resolved}["cloudflare"]
    assert cf_resolved["baseUrl"] == "https://api.cloudflare.com/client/v4/accounts/test-account-123/ai/v1"


def test_cloudflare_missing_env_warn_skip():
    """Missing CLOUDFLARE_ACCOUNT_ID leaves baseUrl unresolved (warn+skip at apply)."""
    rows = mod.build_import_entries(Path("config/providers.yaml"))
    resolved = mod.resolve_import_secrets(rows, env={})
    cf_resolved = {r["name"]: r for r in resolved}["cloudflare"]
    assert "${CLOUDFLARE_ACCOUNT_ID}" in cf_resolved["baseUrl"]


def test_idempotency_second_apply_zero_mutations():
    """Second apply with identical inputs performs zero connection mutations."""
    rows1 = mod.build_import_entries(Path("config/providers.yaml"))
    rows2 = mod.build_import_entries(Path("config/providers.yaml"))
    assert rows1 == rows2
    # byte-identical via json dump with sorted keys
    ja = json.dumps(rows1, indent=2, sort_keys=True)
    jb = json.dumps(rows2, indent=2, sort_keys=True)
    assert ja == jb


def test_redaction_no_secrets_in_summary():
    """No secret material in redacted rows."""
    rows = mod.build_import_entries(Path("config/providers.yaml"))
    redacted = mod.redact_rows(rows)
    for r in redacted:
        ak = r.get("apiKey", "")
        if ak and not ak.startswith("env:"):
            assert ak == "***"


def test_dry_run_emits_complete_plan():
    """Dry-run emits the complete connection provisioning plan with no network."""
    payload = mod.generate_payload(Path("config/providers.yaml"))
    assert "import" in payload
    assert len(payload["import"]) == 21
    # custom nodes present
    providers = {r["provider"] for r in payload["import"]}
    assert "openai-compatible-nara" in providers
    assert "openai-compatible-zai" in providers
    assert "openai-compatible-agnes" in providers
    assert "opencode-zen" in providers
    # retired ids absent
    assert "nara" not in providers
    assert "zai" not in providers
    assert "agnes" not in providers
    assert "opencode" not in providers
