"""Issue #225: provider concurrency cap 2 + model workers cap 4, measured via instrumentation."""
from __future__ import annotations

import inspect
import threading
import time
from pathlib import Path

from llm_discovery.build_all import PROVIDER_CONCURRENCY, build_all
from llm_discovery.config import load_config
from llm_discovery.pipeline import discover_all_providers, discover_provider


def _first_n_provider_names(n: int = 6) -> list[str]:
    cfg = load_config(Path("config/providers.yaml"))
    names = [p.name for p in cfg.providers[:n]]
    assert len(names) == n, f"config must list at least {n} providers for the concurrency test"
    return names


class TestConcurrencyConstants:
    def test_provider_concurrency_is_2(self):
        assert PROVIDER_CONCURRENCY == 2

    def test_max_workers_defaults_are_4(self):
        assert inspect.signature(build_all).parameters["max_workers"].default == 4
        assert inspect.signature(discover_provider).parameters["max_workers"].default == 4
        assert inspect.signature(discover_all_providers).parameters["max_workers"].default == 4

    def test_cli_default_workers_is_4(self, monkeypatch):
        # main() argparse --workers/--max-workers default must be 4 (was 8)
        import llm_discovery.build_all as build_all_mod

        captured: dict = {}

        def fake_build_all(**kwargs):
            captured.update(kwargs)
            return {"store_path": "", "store_size": 0, "compact_bytes": 0, "pretty_bytes": 0}

        monkeypatch.setattr(build_all_mod, "build_all", fake_build_all)
        monkeypatch.setattr("sys.argv", ["llm-discovery", "build-all"])
        build_all_mod.main()
        assert captured["max_workers"] == 4


class TestMeasuredProviderConcurrency:
    def test_build_all_caps_inflight_at_2_and_passes_4_workers(self, tmp_path):
        """AC1: 6 mocked providers, 50ms each, in-flight count recorded under a lock.

        Max observed in-flight must be <= 2 (the cap is actually binding: == 2),
        and every discover_fn call must receive max_workers=4.
        """
        names = _first_n_provider_names(6)
        data_dir = tmp_path / "data"
        lock = threading.Lock()
        state = {"in_flight": 0, "max_in_flight": 0, "workers_seen": []}

        def discover_fn(name, config, aa, models_dev, max_workers, store=None):
            with lock:
                state["in_flight"] += 1
                state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
            state["workers_seen"].append(max_workers)
            time.sleep(0.05)
            with lock:
                state["in_flight"] -= 1
            return {"keep": [], "drop": [], "error": []}

        res = build_all(
            data_dir=data_dir,
            config_path=Path("config/providers.yaml"),
            provider_names=names,
            discover_fn=discover_fn,
        )
        assert res["providers_discovered"] == 6
        assert state["max_in_flight"] <= 2, "provider concurrency cap violated: {0} in flight".format(state["max_in_flight"])
        assert state["max_in_flight"] >= 2, "cap not binding - expected 2 simultaneous providers"
        assert state["workers_seen"] == [4] * 6
        assert res["telemetry"]["provider_concurrency"] == 2
