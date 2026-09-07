"""Issue #140: build_all catalog staleness gate (ADR 0007 rank 6).

Stale catalog (fetched_at >14d) triggers a warn-only per-catalog refresh
before pricing re-average; one catalog's refresh failure never blocks the
other and never fails the build; fresh catalogs skip refresh entirely.
"""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from llm_discovery.build_all import build_all
from llm_discovery.config import load_config


def _stale_ts(days: int = 20) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _fresh_ts() -> str:
    return datetime.now(UTC).isoformat()


def _write_catalogs(data_dir: Path, aa_fetched_at: str | None, md_fetched_at: str | None):
    (data_dir / "artificial_analysis_models.json").write_text(json.dumps(
        {"source": "artificial-analysis", "fetched_at": aa_fetched_at, "models": []}))
    (data_dir / "models_dev_catalog.json").write_text(json.dumps(
        {"models": {}, "providers": {}, "fetched_at": md_fetched_at}))


def _keep(model_id):
    return {
        "model_id": model_id,
        "provider_model_id": model_id,
        "decision": "keep",
        "evidence_level": "strong",
        "coding_score": 55,
        "aa_model_id": "aa-" + model_id,
        "aa_score": 50,
        "confidence": 0.9,
        "pricing": {"price_1m_blended_3_to_1": 0.5, "price_1m_input_tokens": 0.3, "price_1m_output_tokens": 0.9},
        "benchmarks": {"scores": {"aa_intelligence": {"score": 50, "source": "https://example.com/aa"}}, "raw_benchmarks": [], "benchmark_coverage": 0.25},
        "evidence": [f"https://example.com/evidence for {model_id}"],
    }


def _discover_one():
    def discover_fn(name, config=None, aa=None, models_dev=None, max_workers=4, store=None):
        return {"keep": [_keep(f"model-{name}")], "drop": [], "error": []}
    return discover_fn


def _run(tmp_path: Path, **kwargs):
    config_path = Path("config/providers.yaml")
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(config_path)
    names = [p.name for p in cfg.providers[:2]]
    return build_all(data_dir=data_dir, config_path=config_path, provider_names=names,
                     discover_fn=_discover_one(), **kwargs)


def _patch_refreshers():
    return (
        patch("llm_discovery.refresh.refresh_artificial_analysis"),
        patch("llm_discovery.refresh.refresh_models_dev"),
        patch("llm_discovery.refresh.refresh_benchmarks"),
    )


def test_stale_catalogs_trigger_per_catalog_refresh(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_catalogs(data_dir, _stale_ts(20), _stale_ts(30))
    p_aa, p_md, p_bench = _patch_refreshers()
    with p_aa as mock_aa, p_md as mock_md, p_bench as mock_bench:
        res = _run(tmp_path)
    mock_aa.assert_called_once()
    mock_md.assert_called_once()
    mock_bench.assert_called_once()
    assert (data_dir / "model_info_store.json").exists()
    assert res["catalogs"]["checked"] is True
    assert res["catalogs"]["refresh"] == {"aa": "ok", "models_dev": "ok", "benchmarks": "ok"}


def test_fresh_catalogs_skip_refresh(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_catalogs(data_dir, _fresh_ts(), _fresh_ts())
    p_aa, p_md, p_bench = _patch_refreshers()
    with p_aa as mock_aa, p_md as mock_md, p_bench as mock_bench:
        res = _run(tmp_path)
    mock_aa.assert_not_called()
    mock_md.assert_not_called()
    mock_bench.assert_not_called()
    assert (data_dir / "model_info_store.json").exists()


def test_refresh_failure_is_warn_only(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_catalogs(data_dir, _stale_ts(20), None)
    p_aa, p_md, p_bench = _patch_refreshers()
    with p_aa as mock_aa, p_md as mock_md:
        mock_aa.side_effect = RuntimeError("network down")
        res = _run(tmp_path)  # must NOT raise
    assert (data_dir / "model_info_store.json").exists()
    assert res["catalogs"]["refresh"]["aa"] == "failed"


def test_aa_failure_does_not_block_models_dev_refresh(tmp_path):
    # the major fix: AA 401 (no key) must not stop the stale models.dev refresh
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_catalogs(data_dir, _stale_ts(20), _stale_ts(30))
    p_aa, p_md, p_bench = _patch_refreshers()
    with p_aa as mock_aa, p_md as mock_md:
        mock_aa.side_effect = RuntimeError("HTTP 401 no AA key")
        res = _run(tmp_path)
    mock_md.assert_called_once()
    assert res["catalogs"]["refresh"]["aa"] == "failed"
    assert res["catalogs"]["refresh"]["models_dev"] == "ok"
    assert (data_dir / "model_info_store.json").exists()


def test_missing_catalogs_do_not_refresh(tmp_path):
    # cache-miss tolerated: no catalog files at all -> no network call
    p_aa, p_md, p_bench = _patch_refreshers()
    with p_aa as mock_aa, p_md as mock_md:
        res = _run(tmp_path)
    mock_aa.assert_not_called()
    mock_md.assert_not_called()
    assert res["catalogs"]["checked"] is False
    assert (tmp_path / "data" / "model_info_store.json").exists()


def test_max_age_zero_disables_gate(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_catalogs(data_dir, _stale_ts(20), _stale_ts(30))
    p_aa, p_md, p_bench = _patch_refreshers()
    with p_aa as mock_aa, p_md as mock_md:
        res = _run(tmp_path, catalog_max_age_days=0)
    mock_aa.assert_not_called()
    mock_md.assert_not_called()
    assert (data_dir / "model_info_store.json").exists()


def test_cli_build_all_parses_catalog_flags():
    from llm_discovery.cli import build_parser
    ns = build_parser().parse_args(["build-all", "--no-catalog-refresh", "--catalog-max-age-days", "0"])
    assert ns.catalog == "build-all"
    assert ns.no_catalog_refresh is True
    assert ns.catalog_max_age_days == 0
    ns2 = build_parser().parse_args(["build-all"])
    assert ns2.no_catalog_refresh is False
    assert ns2.catalog_max_age_days == 14
