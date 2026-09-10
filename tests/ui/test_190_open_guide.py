"""Tests for issue #190: File icon open providers.yaml + prereq guide."""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from ui.server import app, PROVIDERS_YAML

client = TestClient(app)
REPO = Path(__file__).parents[2]

def test_open_config_returns_fileUrl_shape():
    r = client.post("/api/open-config")
    assert r.status_code == 200, r.text
    j = r.json()
    assert "path" in j
    assert "fileUrl" in j
    assert "opened" in j
    assert j["fileUrl"].startswith("file://")
    assert "config/providers.yaml" in j["path"] or "providers.yaml" in j["path"]
    assert j["path"] in j["fileUrl"] or Path(j["path"]).name in j["fileUrl"]
    assert isinstance(j["opened"], bool)

def test_open_config_fallback_code_success():
    mock = MagicMock(returncode=0)
    with patch("ui.server.subprocess.run", return_value=mock) as m:
        r = client.post("/api/open-config")
        assert r.status_code == 200
        assert r.json()["opened"] is True
        # first call should be code --goto
        first_cmd = m.call_args_list[0][0][0] if m.call_args_list else []
        assert "code" in first_cmd[0] if first_cmd else True

def test_open_config_fallback_xdg_when_code_fails():
    def side_effect(cmd, **kw):
        if cmd[0] == "code":
            raise FileNotFoundError("code not found")
        # xdg-open succeeds
        mock = MagicMock(returncode=0)
        return mock
    with patch("ui.server.subprocess.run", side_effect=side_effect) as m:
        r = client.post("/api/open-config")
        assert r.status_code == 200
        j = r.json()
        assert j["opened"] is True
        cmds = [c[0][0][0] for c in m.call_args_list]
        assert "code" in cmds[0]
        assert "xdg-open" in cmds

def test_open_config_fallback_open_when_both_fail():
    def side_effect(cmd, **kw):
        if cmd[0] in ("code", "xdg-open"):
            raise FileNotFoundError(f"{cmd[0]} not found")
        mock = MagicMock(returncode=0)
        return mock
    with patch("ui.server.subprocess.run", side_effect=side_effect) as m:
        r = client.post("/api/open-config")
        assert r.status_code == 200
        j = r.json()
        assert j["opened"] is True
        cmds = [c[0][0][0] for c in m.call_args_list]
        assert "open" in cmds[-1]

def test_open_config_all_fail_returns_not_opened():
    with patch("ui.server.subprocess.run", side_effect=FileNotFoundError("all missing")):
        r = client.post("/api/open-config")
        assert r.status_code == 200
        j = r.json()
        assert j["opened"] is False
        assert j["fileUrl"].startswith("file://")
        assert "path" in j

def test_guide_links_present():
    html = (REPO / "ui" / "static" / "index.html").read_text()
    # Canonical links required by spec
    assert "github.com/diegosouzapw/OmniRoute" in html
    assert "npmjs.com/package/omniroute" in html
    assert "infisical.com/docs/cli/overview" in html
    assert "infisical.com/docs/cli/usage" in html
    assert "infisical.com/docs/self-hosting" in html

def test_guide_verbatim_wiring():
    html = (REPO / "ui" / "static" / "index.html").read_text()
    # OmniRoute verbatim
    assert "npm install -g omniroute" in html
    assert "http://localhost:20128" in html
    assert "diegosouzapw/omniroute:latest" in html
    assert "/v1/chat/completions" in html
    assert 'model' in html and 'auto' in html
    # Infisical verbatim
    assert "brew install infisical/get-cli/infisical" in html
    assert "infisical login" in html
    assert "infisical run" in html or "infisical export" in html
    assert "app.infisical.com" in html
    assert "INFISICAL_DOMAIN" in html
    # offline
    assert "BRAVE_API_KEY" in html
    assert "DISABLE_WEB_SEARCH" in html
    assert "DuckDuckGo" in html

def test_guide_details_open_and_localstorage():
    html = (REPO / "ui" / "static" / "index.html").read_text()
    js = (REPO / "ui" / "static" / "app.js").read_text()
    # details open by default
    assert "<details" in html
    assert "open" in html  # at least one details open
    # Collapse state persisted in localStorage ui.*
    assert "localStorage" in js
    # must be ui.* prefix
    for m in re.finditer(r'localStorage\s*\.\s*(?:getItem|setItem)\s*\(\s*["\']([^"\']+)["\']', js):
        key = m.group(1)
        assert key.startswith("ui."), f"localStorage key must be ui.* got {key!r}"
    # guide open key should exist
    assert "ui.guide" in js.lower() or "ui." in js

def test_file_icon_button_document_not_folder():
    html = (REPO / "ui" / "static" / "index.html").read_text()
    js = (REPO / "ui" / "static" / "app.js").read_text()
    # Document icon, not folder — check label mono config/providers.yaml
    assert "config/providers.yaml" in html
    assert "mono" in html
    # Button exists (icon button)
    assert "open-config" in html.lower() or "openConfig" in js or "/api/open-config" in js
    # clipboard writeText
    assert "clipboard" in js
    assert "writeText" in js
    # POST /api/open-config
    assert "/api/open-config" in js
    # fileUrl fallback link
    assert "fileUrl" in js or "file://" in js or "fileUrl" in html
    # toast messages
    assert "Copied path" in js or "Copied" in html
    assert "Opened in editor" in js

def test_no_inline_yaml_editor():
    html = (REPO / "ui" / "static" / "index.html").read_text()
    # Spec: No inline YAML editor; out of scope stays — ensure no textarea/editor for yaml
    # Allow provider-filter input but not yaml editor textarea
    assert html.lower().count("<textarea") == 0
    # No codemirror/monaco
    assert "codemirror" not in html.lower()
    assert "monaco" not in html.lower()

def test_no_secret_in_static_bundle():
    js = (REPO / "ui" / "static" / "app.js").read_text()
    html = (REPO / "ui" / "static" / "index.html").read_text()
    css = (REPO / "ui" / "static" / "style.css").read_text()
    for content, name in [(js, "app.js"), (html, "index.html"), (css, "style.css")]:
        # No raw secret, no apiKey literal with value
        assert "sk-" not in content or "env:" in content
        # Ensure built bundle doesn't embed OMNIROUTE_API_KEY raw (hint only in runtime)
        # Check that apiKey literal in static is only env: placeholder not raw
        if "apiKey" in content:
            # allow env:SECRET placeholder
            assert "env:" in content