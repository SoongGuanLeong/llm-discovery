"""Scaffold tests for ui (issue #185) — health, static, port precedence, no secrets."""
from fastapi.testclient import TestClient

from ui.__main__ import parse_args, DEFAULT_PORT
from ui.server import app

client = TestClient(app)


def test_health_returns_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    j = r.json()
    assert j == {"status": "ok"} or j.get("ok") is True or j.get("status") == "ok"


def test_root_serves_html():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "llm-discovery" in r.text.lower()


def test_static_app_js_served():
    r = client.get("/static/app.js")
    assert r.status_code == 200
    assert "fetch" in r.text or "health" in r.text.lower()


def test_root_contains_no_secrets():
    import os
    r = client.get("/")
    body = r.text
    raw = os.environ.get("OMNIROUTE_API_KEY")
    if raw:
        assert raw not in body
    assert "apiKey" not in body.lower() or "env:SECRET" not in body


def test_default_port():
    import os

    os.environ.pop("PORT", None)
    port, _ = parse_args([])
    assert port == DEFAULT_PORT
    assert port == 8765


def test_port_env_override(monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    port, _ = parse_args([])
    assert port == 9000


def test_port_flag_wins_over_env(monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    port, _ = parse_args(["--port", "7777"])
    assert port == 7777
