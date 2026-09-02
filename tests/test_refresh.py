"""Tests for catalog refresh (issue #2 T6): atomic write + backup + all 3 JSONs."""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import httpx
import pytest

from llm_discovery.refresh import (
    _atomic_write_json,
    _normalize_aa_payload,
    fetch_artificial_analysis,
    fetch_models_dev,
    refresh_all,
    refresh_benchmarks,
    refresh_artificial_analysis,
    refresh_models_dev,
)


def test_atomic_write_creates_file(tmp_path: Path):
    target = tmp_path / "out.json"
    data = {"a": 1}
    bp = _atomic_write_json(target, data, backup=True)
    assert bp is None  # no prior file, no backup
    assert target.exists()
    assert json.loads(target.read_text()) == data


def test_atomic_write_backup(tmp_path: Path):
    target = tmp_path / "out.json"
    target.write_text(json.dumps({"old": 1}))
    bp = _atomic_write_json(target, {"new": 2}, backup=True)
    assert bp is not None
    assert bp.exists()
    assert json.loads(bp.read_text()) == {"old": 1}
    assert json.loads(target.read_text()) == {"new": 2}
    # no leftover tmp
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_no_backup(tmp_path: Path):
    target = tmp_path / "out.json"
    target.write_text(json.dumps({"old": 1}))
    bp = _atomic_write_json(target, {"new": 2}, backup=False)
    assert bp is None
    assert json.loads(target.read_text()) == {"new": 2}
    assert not (tmp_path / "out.json.bak").exists()


def test_normalize_aa_payload_list():
    raw = [{"id": "1", "slug": "m1"}]
    out = _normalize_aa_payload(raw)
    assert out["source"] == "artificial-analysis"
    assert out["models"] == raw
    assert "fetched_at" in out


def test_normalize_aa_payload_snapshot():
    raw = {"source": "artificial-analysis", "models": [{"id": "1"}], "tier": "free"}
    out = _normalize_aa_payload(raw)
    assert out["models"] == [{"id": "1"}]
    assert "fetched_at" in out


def test_fetch_models_dev_catalog_shape(tmp_path: Path):
    fake = {"models": {"a/b": {"id": "a/b"}}, "providers": {"p": {"id": "p"}}}
    mock_resp = MagicMock()
    mock_resp.json.return_value = fake
    mock_resp.raise_for_status = MagicMock()
    with patch("llm_discovery.refresh.httpx.get", return_value=mock_resp) as mock_get:
        data = fetch_models_dev(url="https://example.com/catalog.json")
        assert data == fake
        mock_get.assert_called_once()


def test_fetch_models_dev_flat_api(tmp_path: Path):
    flat = {
        "groq": {"id": "groq", "name": "Groq", "api": "https://api.groq.com", "models": {"llama": {"id": "llama", "name": "Llama"}}},
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = flat
    mock_resp.raise_for_status = MagicMock()
    with patch("llm_discovery.refresh.httpx.get", return_value=mock_resp):
        data = fetch_models_dev()
        assert "models" in data and "providers" in data
        assert "llama" in data["models"]
        assert "groq" in data["providers"]


def test_refresh_models_dev_writes_atomically(tmp_path: Path):
    fake = {"models": {"a": {"id": "a"}}, "providers": {}}
    mock_resp = MagicMock()
    mock_resp.json.return_value = fake
    mock_resp.raise_for_status = MagicMock()
    out = tmp_path / "models_dev_catalog.json"
    out.write_text(json.dumps({"models": {}, "providers": {}}))
    with patch("llm_discovery.refresh.httpx.get", return_value=mock_resp):
        refresh_models_dev(output=out, backup=True, dry_run=False)
    assert json.loads(out.read_text()) == fake
    assert (tmp_path / "models_dev_catalog.json.bak").exists()


def test_refresh_benchmarks_rebuilt_from_local(tmp_path: Path):
    # create minimal catalogs
    aa_data = {
        "source": "artificial-analysis",
        "models": [
            {"id": "1", "slug": "my-model", "name": "My Model", "evaluations": {"artificial_analysis_intelligence_index": 50}, "model_creator": {"name": "X"}}
        ],
    }
    md_data = {
        "models": {
            "my-model": {"id": "my-model", "name": "My Model", "benchmarks": [{"name": "SWE-Bench Verified", "score": 60}]}
        },
        "providers": {},
    }
    aa_path = tmp_path / "artificial_analysis_models.json"
    md_path = tmp_path / "models_dev_catalog.json"
    out_path = tmp_path / "benchmarks.json"
    aa_path.write_text(json.dumps(aa_data))
    md_path.write_text(json.dumps(md_data))
    refresh_benchmarks(aa_path=aa_path, models_dev_path=md_path, output=out_path, backup=False)
    assert out_path.exists()
    data = json.loads(out_path.read_text())
    assert "my-model" in data
    assert "aa_intelligence" in data["my-model"]["benchmarks"]


def test_refresh_all_calls_all_three(tmp_path: Path):
    aa_data = {"source": "artificial-analysis", "tier": "free", "fetched_at": "now", "models": [{"id": "1", "slug": "m1", "name": "M1", "evaluations": {"artificial_analysis_intelligence_index": 30}, "model_creator": {"name": "X"}}]}
    md_data = {"models": {"m1": {"id": "m1", "name": "M1"}}, "providers": {"p": {"id": "p", "name": "P", "models": {}}}}
    # mock both fetches
    aa_resp = MagicMock()
    aa_resp.json.return_value = aa_data
    aa_resp.raise_for_status = MagicMock()
    md_resp = MagicMock()
    md_resp.json.return_value = md_data
    md_resp.raise_for_status = MagicMock()
    def fake_get(url, **kwargs):
        if "artificial" in url:
            return aa_resp
        return md_resp
    with patch("llm_discovery.refresh.httpx.get", side_effect=fake_get):
        results = refresh_all(data_dir=tmp_path, backup=False)
    assert (tmp_path / "artificial_analysis_models.json").exists()
    assert (tmp_path / "models_dev_catalog.json").exists()
    assert (tmp_path / "benchmarks.json").exists()
    assert set(results.keys()) == {"aa", "models_dev", "benchmarks"}


def test_refresh_all_only_flag(tmp_path: Path):
    aa_data = {"source": "artificial-analysis", "models": []}
    md_data = {"models": {}, "providers": {}}
    aa_path = tmp_path / "artificial_analysis_models.json"
    md_path = tmp_path / "models_dev_catalog.json"
    aa_path.write_text(json.dumps(aa_data))
    md_path.write_text(json.dumps(md_data))
    # only benchmarks - should not call network
    with patch("llm_discovery.refresh.httpx.get") as mock_get:
        results = refresh_all(data_dir=tmp_path, backup=False, only=["benchmarks"])
        mock_get.assert_not_called()
    assert list(results.keys()) == ["benchmarks"]
    assert (tmp_path / "benchmarks.json").exists()


def test_refresh_dry_run_no_write(tmp_path: Path):
    aa_data = {"source": "artificial-analysis", "models": [{"id": "1"}]}
    mock_resp = MagicMock()
    mock_resp.json.return_value = aa_data
    mock_resp.raise_for_status = MagicMock()
    out = tmp_path / "artificial_analysis_models.json"
    with patch("llm_discovery.refresh.httpx.get", return_value=mock_resp):
        result = refresh_artificial_analysis(output=out, dry_run=True, backup=False)
    assert result is None
    assert not out.exists()


def test_cli_refresh_help():
    from llm_discovery.cli import build_parser
    parser = build_parser()
    # should not raise
    ns = parser.parse_args(["refresh", "--dry-run", "--only", "benchmarks"])
    assert ns.catalog == "refresh"
    assert ns.dry_run is True

