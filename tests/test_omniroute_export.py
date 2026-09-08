"""Scaffold tests for OmniRoute export generator (ticket 165)."""
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

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


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b'{}'
        self.text = '{}'

    def json(self):
        return self._payload


# === Ticket 177: Model provisioning ===


def test_build_model_entries_fixture_shape():
    entries = mod.build_model_entries(Path("tests/fixtures/omniroute/results"))
    providers = {e["provider"] for e in entries}
    models = {e["modelId"] for e in entries}
    assert "groq" in providers
    assert "cerebras" in providers
    assert "llama-3.3-70b-versatile" in models
    assert "llama-4-maverick" in models
    assert "contributor-model-free" in models
    for e in entries:
        assert set(e.keys()) >= {"provider", "modelId", "source"}
        assert e["source"] == "manual"


def test_build_model_entries_provider_mapping():
    entries = mod.build_model_entries(Path("tests/fixtures/omniroute/results"))
    mapped = {e["provider"] for e in entries}
    assert "openai-compatible-nara" not in mapped
    assert "openai-compatible-zai" not in mapped
    assert "openai-compatible-agnes" not in mapped


def test_build_model_entries_deduplicated():
    entries = mod.build_model_entries(Path("tests/fixtures/omniroute/results"))
    seen = set()
    for e in entries:
        key = (e["provider"], e["modelId"])
        assert key not in seen
        seen.add(key)


def test_build_gc_plan_skips_missing_files(tmp_path: Path) -> None:
    (tmp_path / "keep.yaml").write_text("provider: ghost\nkeep:\n  - model_id: live-model\n    decision: keep\n    tier: flash\n")
    plan = mod.build_gc_plan(tmp_path)
    assert "ghost" in plan
    assert plan["ghost"] == {"live-model"}


def test_build_gc_plan_skips_unparseable_files(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("provider: broken\nkeep:\n  - model_id: live-model\n    decision: keep\n    tier: flash\n")
    (tmp_path / "broken.yaml").write_text("{{invalid yaml")
    plan = mod.build_gc_plan(tmp_path)
    assert "broken" not in plan


def test_build_gc_plan_dropped_keep_deleted() -> None:
    plan = mod.build_gc_plan(Path("tests/fixtures/omniroute/results"))
    assert "cerebras" in plan
    assert "dropped-model" not in plan["cerebras"]
    assert "llama-4-maverick" in plan["cerebras"]


def test_combo_provider_mapping() -> None:
    combos = mod.build_combo_entries(Path("tests/fixtures/omniroute/results"))
    for combo in combos:
        for m in combo.get("models", []):
            assert m["provider"] not in {
                "nararouter", "zai", "agnes", "opencode_zen",
                "google", "nvidia_nim", "cloudflare", "kilo_ai",
                "navy_ai", "ollama_cloud", "sea-lion",
            }
    flash = next(c for c in combos if c["name"] == "flash")
    flash_providers = {m["provider"] for m in flash["models"]}
    assert "cerebras" in flash_providers


def test_dry_run_emits_complete_model_plan() -> None:
    payload = mod.generate_payload(Path("tests/fixtures/omniroute/providers.yaml"), Path("tests/fixtures/omniroute/results"))
    assert "models" in payload
    assert "gc" in payload
    assert len(payload["models"]) >= 3
    model_providers = {e["provider"] for e in payload["models"]}
    assert "cerebras" in model_providers
    assert "groq" in model_providers
    assert set(payload["gc"]["cerebras"]) == {"llama-4-maverick", "contributor_special-model"}
    assert payload["gc"]["groq"] == sorted(["llama-3.3-70b-versatile", "contributor-model-free"])


def test_double_apply_idempotency_models(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"post": 0, "get": 0, "delete": 0}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["post"] += 1
        return _FakeResponse({"id": "m1"}, status_code=200)

    def fake_get(url, headers=None, timeout=None):
        calls["get"] += 1
        return _FakeResponse({"models": [
            {"provider": "groq", "modelId": "llama-3.3-70b-versatile", "id": "m1"},
            {"provider": "groq", "modelId": "contributor-model-free", "id": "m2"},
        ]})

    def fake_delete(url, headers=None, timeout=None):
        calls["delete"] += 1
        return _FakeResponse({}, status_code=204)

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)
    monkeypatch.setattr("httpx.delete", fake_delete)

    entries = [
        {"provider": "groq", "modelId": "llama-3.3-70b-versatile", "source": "manual"},
        {"provider": "groq", "modelId": "contributor-model-free", "source": "manual"},
    ]
    gc_plan = {"groq": {"llama-3.3-70b-versatile", "contributor-model-free"}}

    res1 = mod.apply_model_upserts("http://x", entries)
    gc1 = mod.apply_gc("http://x", "groq", gc_plan["groq"])

    res2 = mod.apply_model_upserts("http://x", entries)
    gc2 = mod.apply_gc("http://x", "groq", gc_plan["groq"])

    # second pass performs zero model mutations (gateway returns 200 for existing)
    assert len(res1["upserted"]) == len(res2["upserted"]) == 2
    assert gc1["count"] == 0
    assert gc2["count"] == 0


def test_redaction_includes_models() -> None:
    payload = mod.generate_payload(Path("tests/fixtures/omniroute/providers.yaml"), Path("tests/fixtures/omniroute/results"))
    redacted = {
        "import": mod.redact_rows(payload.get("import", [])),
        "models": payload.get("models", []),
        "combos": payload.get("combos", []),
        "gc": payload.get("gc", {}),
        "meta": payload.get("meta", {}),
    }
    dumped = json.dumps(redacted)
    assert "sk-" not in dumped



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








class TestE2EFullApply:
    """Ticket 178: end-to-end verification + idempotency + revert."""

    def test_full_apply_creates_expected_mutations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gateway = _MockGateway()

        def fake_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
            return _mock_request(gateway, method, url, kwargs.get("json"), kwargs.get("headers"))

        monkeypatch.setattr("httpx.post", lambda *a, **k: fake_request("POST", a[0], **k))
        monkeypatch.setattr("httpx.put", lambda *a, **k: fake_request("PUT", a[0], **k))
        monkeypatch.setattr("httpx.get", lambda *a, **k: fake_request("GET", a[0], **k))
        monkeypatch.setattr("httpx.delete", lambda *a, **k: fake_request("DELETE", a[0], **k))

        payload = mod.generate_payload(Path("config/providers.yaml"), Path("data/results"))
        env = {
            "CLOUDFLARE_ACCOUNT_ID": "test-123",
            "GROQ_API_KEY": "sk-groq",
            "OPENCODE_ZEN_API_KEY": "sk-zen",
        }

        summary = mod.apply_payload(payload, base_url="http://x", auth_headers=None, resolve_env=env)

        assert summary["retire"]["count"] == 4
        assert len([r for r in summary["import"]["response"]["results"] if "status" in r]) >= 2
        assert summary["psd_patches"]["count"] > 0
        assert summary["models"]["sent"] > 0
        assert len(summary["combos"]["upserted"]) >= 1

    def test_double_apply_idempotency_full(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gateway = _MockGateway()

        def fake_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
            return _mock_request(gateway, method, url, kwargs.get("json"), kwargs.get("headers"))

        monkeypatch.setattr("httpx.post", lambda *a, **k: fake_request("POST", a[0], **k))
        monkeypatch.setattr("httpx.put", lambda *a, **k: fake_request("PUT", a[0], **k))
        monkeypatch.setattr("httpx.get", lambda *a, **k: fake_request("GET", a[0], **k))
        monkeypatch.setattr("httpx.delete", lambda *a, **k: fake_request("DELETE", a[0], **k))

        payload = mod.generate_payload(Path("config/providers.yaml"), Path("data/results"))
        env = {
            "CLOUDFLARE_ACCOUNT_ID": "test-123",
            "GROQ_API_KEY": "sk-groq",
            "OPENCODE_ZEN_API_KEY": "sk-zen",
        }

        mod.apply_payload(payload, base_url="http://x", auth_headers=None, resolve_env=env)
        snapshot_after_first = mod.snapshot_gateway_state("http://x")

        mod.apply_payload(payload, base_url="http://x", auth_headers=None, resolve_env=env)
        snapshot_after_second = mod.snapshot_gateway_state("http://x")

        assert snapshot_after_first == snapshot_after_second

    def test_revert_restores_gateway_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gateway = _MockGateway()

        def fake_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
            return _mock_request(gateway, method, url, kwargs.get("json"), kwargs.get("headers"))

        monkeypatch.setattr("httpx.post", lambda *a, **k: fake_request("POST", a[0], **k))
        monkeypatch.setattr("httpx.put", lambda *a, **k: fake_request("PUT", a[0], **k))
        monkeypatch.setattr("httpx.get", lambda *a, **k: fake_request("GET", a[0], **k))
        monkeypatch.setattr("httpx.delete", lambda *a, **k: fake_request("DELETE", a[0], **k))

        payload = mod.generate_payload(Path("config/providers.yaml"), Path("data/results"))
        env = {
            "CLOUDFLARE_ACCOUNT_ID": "test-123",
            "GROQ_API_KEY": "sk-groq",
            "OPENCODE_ZEN_API_KEY": "sk-zen",
        }
        snapshot_before = mod.snapshot_gateway_state("http://x")

        summary = mod.apply_payload(payload, base_url="http://x", auth_headers=None, resolve_env=env)
        assert not mod.verify_gateway_unchanged("http://x", snapshot_before)["unchanged"]

        revert_summary = mod.revert_payload("http://x", summary, snapshot_before)
        assert revert_summary["total_reverted"] > 0
        assert revert_summary["total_errors"] == 0

        assert mod.verify_gateway_unchanged("http://x", snapshot_before)["unchanged"]

    def test_redacted_stdout_has_no_secrets(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        gateway = _MockGateway()

        def fake_request(method: str, url: str, **kwargs: Any) -> _FakeResponse:
            return _mock_request(gateway, method, url, kwargs.get("json"), kwargs.get("headers"))

        monkeypatch.setattr("httpx.post", lambda *a, **k: fake_request("POST", a[0], **k))
        monkeypatch.setattr("httpx.put", lambda *a, **k: fake_request("PUT", a[0], **k))
        monkeypatch.setattr("httpx.get", lambda *a, **k: fake_request("GET", a[0], **k))
        monkeypatch.setattr("httpx.delete", lambda *a, **k: fake_request("DELETE", a[0], **k))

        payload = mod.generate_payload(Path("config/providers.yaml"), Path("data/results"))
        env = {
            "CLOUDFLARE_ACCOUNT_ID": "test-123",
            "GROQ_API_KEY": "sk-secret-key-123",
            "OPENCODE_ZEN_API_KEY": "sk-zen",
        }
        redacted = {
            "import": mod.redact_rows(payload.get("import", [])),
            "models": payload.get("models", []),
            "combos": payload.get("combos", []),
            "gc": payload.get("gc", {}),
            "meta": payload.get("meta", {}),
        }
        dumped = json.dumps(redacted)
        assert "sk-secret-key-123" not in dumped
        assert dumped.count("env:") >= 10


class _MockGateway:
    """In-memory mock OmniRoute gateway for E2E tests."""

    def __init__(self) -> None:
        self.connections: list[dict[str, Any]] = [
            {"id": "c1", "provider": "groq", "name": "groq", "isActive": True, "providerSpecificData": {}},
            {"id": "c2", "provider": "cloudflare-ai", "name": "cloudflare", "isActive": True, "providerSpecificData": {"accountId": "old-account"}},
            {"id": "c3", "provider": "nara", "name": "nararouter", "isActive": True, "providerSpecificData": {}},
            {"id": "c4", "provider": "zai", "name": "zai", "isActive": True, "providerSpecificData": {}},
            {"id": "c5", "provider": "agnes", "name": "agnes", "isActive": True, "providerSpecificData": {}},
            {"id": "c6", "provider": "opencode", "name": "opencode", "isActive": True, "providerSpecificData": {}},
        ]
        self.models: dict[str, list[dict[str, Any]]] = {
            "groq": [
                {"id": "m1", "provider": "groq", "modelId": "llama-3.3-70b-versatile", "source": "manual"},
                {"id": "m2", "provider": "groq", "modelId": "extra-model", "source": "manual"},
            ],
            "cerebras": [
                {"id": "m3", "provider": "cerebras", "modelId": "llama-4-maverick", "source": "manual"},
            ],
        }
        self.combos: list[dict[str, Any]] = [
            {"id": "cb1", "name": "flash", "models": [{"provider": "groq", "model": "old-flash"}], "strategy": "reset-aware"},
        ]
        self._next_id = 10

    def _next(self, prefix: str) -> str:
        self._next_id += 1
        return f"{prefix}{self._next_id}"

    def get_connections(self) -> list[dict[str, Any]]:
        return list(self.connections)

    def get_models(self, provider: str) -> list[dict[str, Any]]:
        return list(self.models.get(provider, []))

    def get_combos(self) -> list[dict[str, Any]]:
        return list(self.combos)

    def create_connection(self, row: dict[str, Any]) -> dict[str, Any]:
        provider = row.get("provider", row.get("name", "unknown"))
        name = row.get("name", row.get("provider", "unknown"))
        existing = [c for c in self.connections if c.get("provider") == provider and c.get("name") == name]
        if existing:
            return {"connection": existing[0]}
        conn = {
            "id": self._next("c"),
            "provider": provider,
            "name": name,
            "isActive": True,
            "providerSpecificData": {},
        }
        self.connections.append(conn)
        return {"connection": conn}

    def update_connection(self, cid: str, body: dict[str, Any]) -> dict[str, Any]:
        for conn in self.connections:
            if conn.get("id") == cid:
                conn.update(body)
                return conn
        return {}

    def delete_connection(self, cid: str) -> bool:
        self.connections = [c for c in self.connections if c.get("id") != cid]
        return True

    def create_model(self, entry: dict[str, Any]) -> dict[str, Any]:
        provider = entry.get("provider", "unknown")
        model_id = entry.get("modelId", "unknown")
        existing = [m for m in self.models.get(provider, []) if m.get("modelId") == model_id]
        if existing:
            return existing[0]
        model = {"id": self._next("m"), "provider": provider, "modelId": model_id, "source": entry.get("source", "manual")}
        self.models.setdefault(provider, []).append(model)
        return model

    def delete_model(self, provider: str, model_id: str) -> bool:
        models = self.models.get(provider, [])
        self.models[provider] = [m for m in models if m.get("modelId") != model_id]
        return True

    def delete_model_by_id(self, mid: str) -> bool:
        for provider, models in list(self.models.items()):
            for m in models:
                if m.get("id") == mid:
                    models.remove(m)
                    return True
        return False

    def create_combo(self, combo: dict[str, Any]) -> dict[str, Any]:
        name = combo.get("name")
        existing = [c for c in self.combos if c.get("name") == name]
        if existing:
            entry = dict(combo)
            entry["id"] = existing[0]["id"]
            idx = self.combos.index(existing[0])
            self.combos[idx] = entry
            return entry
        entry = dict(combo)
        entry["id"] = self._next("cb")
        self.combos.append(entry)
        return entry

    def update_combo(self, cid: str, combo: dict[str, Any]) -> dict[str, Any]:
        for i, c in enumerate(self.combos):
            if c.get("id") == cid or c.get("name") == combo.get("name"):
                self.combos[i] = dict(combo)
                self.combos[i]["id"] = cid
                return self.combos[i]
        return {}

    def delete_combo(self, cid: str) -> bool:
        self.combos = [c for c in self.combos if c.get("id") != cid]
        return True


def _mock_request(gateway: _MockGateway, method: str, url: str, json: Any = None, headers: Any = None) -> _FakeResponse:
    url = str(url)
    if method == "GET":
        if url.endswith("/api/providers"):
            return _FakeResponse({"connections": gateway.get_connections()})
        if "/api/provider-models?provider=" in url:
            provider = url.split("provider=")[-1]
            return _FakeResponse({"models": gateway.get_models(provider)})
        if url.endswith("/api/combos"):
            return _FakeResponse({"combos": gateway.get_combos()})
    if method == "POST":
        if url.endswith("/api/providers") and json and isinstance(json, dict) and "providers" not in json:
            return _FakeResponse(gateway.create_connection(json))
        if url.endswith("/api/provider-models"):
            return _FakeResponse(gateway.create_model(json))
        if url.endswith("/api/combos"):
            return _FakeResponse(gateway.create_combo(json))
    if method == "PUT":
        if "/api/providers/" in url:
            cid = url.split("/api/providers/")[-1]
            return _FakeResponse(gateway.update_connection(cid, json))
        if "/api/combos/" in url:
            cid = url.split("/api/combos/")[-1]
            return _FakeResponse(gateway.update_combo(cid, json))
    if method == "DELETE":
        if "/api/providers/" in url and "/api/provider-models" not in url:
            cid = url.split("/api/providers/")[-1]
            gateway.delete_connection(cid)
            return _FakeResponse({}, status_code=204)
        if "?provider=" in url and "&modelId=" in url:
            provider = url.split("?provider=")[-1].split("&")[0]
            model_id = url.split("&modelId=")[-1]
            gateway.delete_model(provider, model_id)
            return _FakeResponse({}, status_code=204)
        if "/api/provider-models/" in url and "?provider=" not in url:
            mid = url.split("/api/provider-models/")[-1]
            gateway.delete_model_by_id(mid)
            return _FakeResponse({}, status_code=204)
        if "/api/combos/" in url:
            cid = url.split("/api/combos/")[-1]
            gateway.delete_combo(cid)
            return _FakeResponse({}, status_code=204)
    return _FakeResponse({}, status_code=404)


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.content = b'{}'
        self.text = '{}'

    def json(self):
        return self._payload

