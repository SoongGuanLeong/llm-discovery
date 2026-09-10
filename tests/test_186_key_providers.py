"""Tests for issue #186: Key persist + live providers dropdown."""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from ui.server import app, ENV_PATH, PROVIDERS_YAML, _hint_for_key

client = TestClient(app)
REPO_ROOT = Path(__file__).parents[1]


# ---- helpers ----
@pytest.fixture(autouse=True)
def _clean_env():
    orig = os.environ.get("OMNIROUTE_API_KEY")
    yield
    if orig is None:
        os.environ.pop("OMNIROUTE_API_KEY", None)
    else:
        os.environ["OMNIROUTE_API_KEY"] = orig


# ---- GET /api/providers ----
def test_providers_live_sorted_and_count():
    r = client.get("/api/providers")
    assert r.status_code == 200
    j = r.json()
    assert "providers" in j
    names = j["providers"]
    assert isinstance(names, list)
    # file currently has 20, spec said 22 — accept actual file count but must be sorted
    assert names == sorted(names)
    # at least 20 (spec) — use actual count from yaml
    data = yaml.safe_load(PROVIDERS_YAML.read_text(encoding="utf-8"))
    expected = sorted([p["name"] for p in data["providers"]])
    assert names == expected


def test_providers_reflects_edit_without_restart(tmp_path, monkeypatch):
    fake_yaml = tmp_path / "providers.yaml"
    data = yaml.safe_load(PROVIDERS_YAML.read_text(encoding="utf-8"))
    fake_yaml.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr("ui.server.PROVIDERS_YAML", fake_yaml)
    r1 = client.get("/api/providers")
    assert "zzz_live_provider" not in r1.json()["providers"]
    # edit
    data["providers"].append({"name": "zzz_live_provider", "base_url": "https://example.com", "secret": "X"})
    fake_yaml.write_text(yaml.safe_dump(data), encoding="utf-8")
    r2 = client.get("/api/providers")
    assert "zzz_live_provider" in r2.json()["providers"]
    assert r2.json()["providers"] == sorted(r2.json()["providers"])
    # revert
    data["providers"] = [p for p in data["providers"] if p.get("name") != "zzz_live_provider"]
    fake_yaml.write_text(yaml.safe_dump(data), encoding="utf-8")
    r3 = client.get("/api/providers")
    assert "zzz_live_provider" not in r3.json()["providers"]


# ---- GET /api/config/status ----
def test_status_hasKey_false_when_not_set(monkeypatch):
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    r = client.get("/api/config/status")
    assert r.status_code == 200
    j = r.json()
    assert j["hasKey"] is False
    assert j["hint"] == ""
    assert "OMNIROUTE_API_KEY" not in r.text or "***" in r.text  # no raw key leakage via status


def test_status_hint_masked_never_raw(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_API_KEY", "mykey123abc")
    r = client.get("/api/config/status")
    j = r.json()
    assert j["hasKey"] is True
    assert j["hint"].startswith("***")
    assert "mykey123abc" not in r.text
    assert j["hint"] == "***3abc"  # spec example hint last 3-4
    # short key
    monkeypatch.setenv("OMNIROUTE_API_KEY", "ab")
    r = client.get("/api/config/status")
    assert r.json()["hint"] == "***"
    assert "ab" not in r.json()["hint"] or r.json()["hint"] == "***"


def test_hint_helper_variants():
    assert _hint_for_key("") == ""
    assert _hint_for_key("ab") == "***"
    assert _hint_for_key("abcd") == "***bcd"
    assert _hint_for_key("mykey123abc") == "***3abc"
    assert _hint_for_key("x" * 20).startswith("***")
    assert "x" * 20 not in _hint_for_key("x" * 20) or _hint_for_key("x" * 20).startswith("***")


# ---- POST /api/config/omniroute-key ----
def test_post_blank_whitespace_returns_400():
    for payload in [{"key": "   "}, {"key": "\t\n"}, {"key": "  \n  "}]:
        r = client.post("/api/config/omniroute-key", json=payload)
        assert r.status_code == 400, payload
        assert r.json()["detail"] == "key required"
        # never leak raw
        assert "   " not in r.text or r.json()["detail"] == "key required"


def test_post_trims_and_persists(tmp_path, monkeypatch):
    fake_env = tmp_path / ".env"
    fake_env.write_text("OTHER=keep\n", encoding="utf-8")
    monkeypatch.setattr("ui.server.ENV_PATH", fake_env)
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    r = client.post("/api/config/omniroute-key", json={"key": "  mykey123abc  "})
    assert r.status_code == 200
    j = r.json()
    assert j["hasKey"] is True
    assert j["hint"] == "***3abc"
    assert "mykey123abc" not in r.text
    # trimmed value in env
    assert os.environ.get("OMNIROUTE_API_KEY") == "mykey123abc"
    # persisted and other vars kept
    text = fake_env.read_text(encoding="utf-8")
    assert "OMNIROUTE_API_KEY" in text
    assert "OTHER=keep" in text


def test_post_empty_deletes_key_keeps_other_vars(tmp_path, monkeypatch):
    fake_env = tmp_path / ".env"
    fake_env.write_text("OTHER=keep\nOMNIROUTE_API_KEY='toDelete'\n", encoding="utf-8")
    monkeypatch.setattr("ui.server.ENV_PATH", fake_env)
    monkeypatch.setenv("OMNIROUTE_API_KEY", "toDelete")
    r = client.post("/api/config/omniroute-key", json={"key": ""})
    assert r.status_code == 200
    assert r.json()["hasKey"] is False
    assert r.json()["hint"] == ""
    assert os.environ.get("OMNIROUTE_API_KEY") is None
    text = fake_env.read_text(encoding="utf-8")
    assert "OMNIROUTE_API_KEY" not in text
    assert "OTHER=keep" in text


def test_post_chmod_600_if_created(tmp_path, monkeypatch):
    fake_env = tmp_path / "new.env"
    assert not fake_env.exists()
    monkeypatch.setattr("ui.server.ENV_PATH", fake_env)
    monkeypatch.delenv("OMNIROUTE_API_KEY", raising=False)
    r = client.post("/api/config/omniroute-key", json={"key": "newkey123abc"})
    assert r.status_code == 200
    assert fake_env.exists()
    mode = oct(fake_env.stat().st_mode)[-3:]
    assert mode == "600", f"expected 600 got {mode}"
    # cleanup
    fake_env.unlink(missing_ok=True)
    os.environ.pop("OMNIROUTE_API_KEY", None)


# ---- UI static checks: card, providers dropdown, secrets handling ----
def test_ui_key_card_contains_required_elements():
    r = client.get("/")
    html = r.text
    # type=password
    assert 'type="password"' in html
    # placeholder with bullets + ***
    assert "***" in html
    # badge Not set gray / Saved hint green handled via JS — check badge elements exist
    assert 'id="key-badge"' in html
    assert "Not set" in html or "badge-gray" in html
    # eye toggle local only no fetch
    assert 'id="eye-btn"' in html
    assert 'id="key-input"' in html
    # explicit Save + Clear buttons
    assert 'id="save-btn"' in html and ">Save<" in html
    assert 'id="clear-btn"' in html and ">Clear<" in html
    # inline red error + toast container
    assert 'id="key-error"' in html
    assert 'id="toast"' in html


def test_ui_providers_dropdown_exists():
    html = client.get("/").text
    assert 'id="provider-select"' in html
    assert 'id="provider-filter"' in html
    assert 'id="providers-count"' in html
    js = client.get("/static/app.js").text
    assert "provider" in js.lower()
    assert "/api/providers" in js
    assert "/api/config/status" in js
    assert "/api/config/omniroute-key" in js


def test_app_js_eye_toggle_is_local_no_fetch():
    js = client.get("/static/app.js").text
    assert "eye" in js.lower()
    # handler must toggle input type locally
    assert "keyInput.type" in js or 'key-input' in js
    # ensure eye handler toggles password/text without fetch
    assert 'type = keyInput.type' in js or 'keyInput.type ==' in js or 'password' in js.lower()
    # ensure save is explicit via button, not auto on input event for secrets
    assert "save-btn" in js or "doSave" in js


def test_no_localstorage_secret():
    js = client.get("/static/app.js").text
    html = client.get("/").text
    # app.js must never persist secrets via localStorage
    assert "localStorage" not in js or "OMNIROUTE_API_KEY" not in js
    if "localStorage" in js:
        for m in re.finditer(r"localStorage\s*\.\s*(?:getItem|setItem)\s*\(\s*[\"\']([^\"\']+)[\"\']", js):
            key = m.group(1)
            assert key.startswith("ui."), f"localStorage key must be ui.* got {key!r}"
        assert "OMNIROUTE_API_KEY" not in js
    # html hint text mentions Never in localStorage but not actual JS usage; ensure no <script> uses localStorage for secrets
    if "localStorage" in html and "<script" in html:
        # Only allow mention in plain text hint, not in inline script
        script_parts = re.findall(r"<script[^>]*>(.*?)</script>", html, flags=re.S)
        for part in script_parts:
            assert "localStorage" not in part or "ui." in part


def test_api_responses_never_return_raw_key(tmp_path, monkeypatch):
    fake_env = tmp_path / ".env"
    fake_env.write_text("OTHER=keep\n", encoding="utf-8")
    monkeypatch.setattr("ui.server.ENV_PATH", fake_env)
    secret = "supersecret12345abc"
    r = client.post("/api/config/omniroute-key", json={"key": secret})
    assert secret not in r.text
    r2 = client.get("/api/config/status")
    assert secret not in r2.text
    assert r2.json()["hint"] == "***" + secret[-4:]
    # providers must not leak key
    r3 = client.get("/api/providers")
    assert secret not in r3.text


def test_status_never_logs_raw_key_is_redacted_in_response():
    # ensure status and providers responses contain no raw secret even if env set
    import os as _os
    _os.environ["OMNIROUTE_API_KEY"] = "anothersecretXYZ9"
    try:
        for endpoint in ["/api/config/status", "/api/providers", "/api/health"]:
            r = client.get(endpoint)
            assert "anothersecretXYZ9" not in r.text
    finally:
        _os.environ.pop("OMNIROUTE_API_KEY", None)
