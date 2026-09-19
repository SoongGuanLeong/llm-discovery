"""Issue #242: Force fresh LLM judge per provider (--force-judge)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from llm_discovery.benchmarks import BenchmarkDataCache
from llm_discovery.candidate_store import CandidateStore
from llm_discovery.evaluation import ModelEvaluation
from llm_discovery.evaluator import EvaluatorCoordinator, resolve_cache_identity
from llm_discovery.model_info_store import (
    BenchmarkSnapshot,
    JudgeSnapshot,
    ModelInfoRecord,
    ModelInfoStore,
    PricingSnapshot,
    StoreMeta,
)


def _fresh_ts() -> str:
    return datetime.now(UTC).isoformat()


def _empty_cache():
    c = BenchmarkDataCache()
    c._loaded = True
    c._data = {}
    return c


class _FakeAA:
    models = []


class _FakeMD:
    models = {}
    providers = {}

    def get_model(self, model_id):
        return None


class _CountingFake:
    def __init__(self, result):
        self.calls = 0
        self.result = result

    def evaluate(self, request, packet=None):
        self.calls += 1
        return self.result


def _moderate_llm_result():
    return ModelEvaluation(
        coding=True,
        decision="keep",
        confidence=0.8,
        evidence_level="moderate",
        evidence=["AA 30 via https://artificialanalysis.ai/models/x"],
        coding_assessment=None,
    )


def _weak_llm_result():
    return ModelEvaluation(
        coding=False,
        decision="drop",
        confidence=0.4,
        evidence_level="weak",
        evidence=["unverified claim only"],
        coding_assessment=None,
    )


def _keeper_store(tmp_path, key="force-keeper-1"):
    store = ModelInfoStore(tmp_path / "store.json")
    rec = ModelInfoRecord(
        benchmarks=BenchmarkSnapshot(scores={}, raw_benchmarks=[], benchmark_coverage=0.0),
        pricing=PricingSnapshot(blended=0.5, input=0.3, output=0.9),
        _meta=StoreMeta(first_seen=_fresh_ts(), last_updated=_fresh_ts(), version=2),
        judge=JudgeSnapshot(
            evidence_level="moderate",
            evidence=["https://example.com/m"],
            confidence=0.8,
            coding=True,
            canonical_name="Force Keeper",
            tier="flash",
            decision="keep",
            judge_model="test-judge",
        ),
    )
    store.put(key, rec)
    return store


class TestCoordinatorBypass:
    def test_keeper_hit_normal_no_judge(self, tmp_path):
        store = _keeper_store(tmp_path)
        assert EvaluatorCoordinator.classify_hit(store.get("force-keeper-1")) == "strong_hit"
        fake = _CountingFake(_moderate_llm_result())
        fake_res = Mock(
            aa_model={
                "id": "force-keeper-1",
                "name": "Force Keeper",
                "slug": "force-keeper-1",
                "evaluations": {"artificial_analysis_intelligence_index": 30},
                "pricing": {"price_1m_blended_3_to_1": 0.5},
            }
        )
        with patch("llm_discovery.pipeline.resolve_model", return_value=fake_res):
            coord = EvaluatorCoordinator(
                provider_name="agnes",
                aa=_FakeAA(),
                models_dev=_FakeMD(),
                evaluator=fake,
                min_score=24,
                max_score=45,
                cache=_empty_cache(),
                store=store,
            )
            res = coord.evaluate({"id": "force-keeper-1"})
        assert res.get("cached") is True
        assert fake.calls == 0

    def test_keeper_bypass_force_runs_judge(self, tmp_path):
        store = _keeper_store(tmp_path)
        fake = _CountingFake(_moderate_llm_result())
        fake_res = Mock(
            aa_model={
                "id": "force-keeper-1",
                "name": "Force Keeper",
                "slug": "force-keeper-1",
                "evaluations": {"artificial_analysis_intelligence_index": 30},
                "pricing": {"price_1m_blended_3_to_1": 0.5},
            }
        )
        with patch("llm_discovery.pipeline.resolve_model", return_value=fake_res):
            coord = EvaluatorCoordinator(
                provider_name="agnes",
                aa=_FakeAA(),
                models_dev=_FakeMD(),
                evaluator=fake,
                min_score=24,
                max_score=45,
                cache=_empty_cache(),
                store=store,
                force_judge=True,
            )
            res = coord.evaluate({"id": "force-keeper-1"})
        assert res.get("cached") is not True
        assert fake.calls == 1

    def test_candidate_hit_normal_force_bypass(self, tmp_path):
        from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog

        aa_path = tmp_path / "aa.json"
        aa_path.write_text(json.dumps({"source": "test", "models": []}))
        md_path = tmp_path / "md.json"
        md_path.write_text(json.dumps({"models": {}, "providers": {}}))
        aa = ArtificialAnalysisCatalog(aa_path)
        md = ModelsDevCatalog(md_path)
        cache = _empty_cache()
        fake = _CountingFake(_weak_llm_result())
        cstore = CandidateStore(tmp_path / "cand.json")
        coord = EvaluatorCoordinator(
            provider_name="agnes", aa=aa, models_dev=md,
            evaluator=fake, min_score=24, max_score=45,
            cache=cache, candidate_store=cstore,
        )
        r1 = coord.evaluate({"id": "force-weak-1"})
        assert r1["decision"] == "uncertain"
        r2 = coord.evaluate({"id": "force-weak-1"})
        assert r2["source"] == "candidate_cache"
        coord_force = EvaluatorCoordinator(
            provider_name="agnes", aa=aa, models_dev=md,
            evaluator=fake, min_score=24, max_score=45,
            cache=cache, candidate_store=cstore, force_judge=True,
        )
        r3 = coord_force.evaluate({"id": "force-weak-1"})
        assert r3["source"] == "deterministic"
        assert r3.get("cached") is not True

    def test_deterministic_screening_stays_without_judge(self, tmp_path):
        fake = _CountingFake(_moderate_llm_result())
        coord = EvaluatorCoordinator(
            provider_name="agnes", aa=_FakeAA(), models_dev=_FakeMD(),
            evaluator=fake, min_score=24, max_score=45,
            cache=_empty_cache(), force_judge=True,
        )
        res = coord.evaluate({"id": "my-tts-model"})
        assert res["decision"] == "drop"
        assert res["evidence_level"] == "strong"
        assert fake.calls == 0

    def test_force_defaults_off(self):
        c = EvaluatorCoordinator(
            provider_name="p", aa=None, models_dev=None,
            evaluator=None, min_score=24, max_score=45,
        )
        assert c.force_judge is False


class TestPlumbing:
    def test_discover_provider_signature(self):
        import inspect

        from llm_discovery.pipeline import discover_provider

        sig = inspect.signature(discover_provider)
        assert "force_judge" in sig.parameters
        assert sig.parameters["force_judge"].default is False

    def test_build_all_signature(self):
        import inspect

        from llm_discovery.build_all import build_all

        sig = inspect.signature(build_all)
        assert "force_judge" in sig.parameters
        assert sig.parameters["force_judge"].default is False

    def test_cli_parses_flag(self):
        from llm_discovery.cli import _build_parser

        p = _build_parser()
        a = p.parse_args(["build"])
        assert a.force_judge is False
        b = p.parse_args(["build", "--force-judge", "--providers", "agnes"])
        assert b.force_judge is True
        assert b.providers == ["agnes"]
        # cost guard: help warns about judge spend
        from pathlib import Path as _P

        src = (_P(__file__).parent.parent / "src" / "llm_discovery" / "cli.py").read_text()
        assert "--force-judge" in src
        low = src.lower()
        assert "judge budget" in low or "costs judge" in low

    def test_discover_cli_parses_flag(self):
        from llm_discovery.cli import _build_parser

        p = _build_parser()
        a = p.parse_args(["discover", "groq"])
        assert a.force_judge is False
        b = p.parse_args(["discover", "groq", "--force-judge"])
        assert b.force_judge is True
        assert b.provider == "groq"

    def test_scoped_forced_run_writes_only_subset(self, tmp_path):
        from llm_discovery.build_all import build_all

        cfg = tmp_path / "providers.yaml"
        cfg.write_text(
            "judge_llm:\n  base_url: http://x\n  model: m\n  secret: S\n  timeout: 10\n"
            "artificial_analysis:\n  min_score: 24\n  max_score: 45\n"
            "infisical: {}\nproviders:\n- name: agnes\n  base_url: http://x\n  secret: S\n- name: groq\n  base_url: http://y\n  secret: S2\n"
        )
        seen = {}

        def discover_fn(name, *a, **kw):
            seen[name] = kw.get("force_judge", "missing")
            return {"keep": [], "drop": [], "uncertain": [], "error": []}

        # pre-existing other provider report must survive
        res_dir = tmp_path / "data" / "results"
        res_dir.mkdir(parents=True)
        (res_dir / "groq.yaml").write_text(
            yaml.safe_dump({"provider": "groq", "keep": [{"model_id": "g1"}], "drop_llm": [], "uncertain": [], "error": []})
        )
        res = build_all(
            data_dir=tmp_path / "data",
            config_path=cfg,
            provider_names=["agnes"],
            discover_fn=discover_fn,
            force_judge=True,
            no_catalog_refresh=True,
        )
        assert seen.get("agnes") is True
        assert (res_dir / "agnes.yaml").exists()
        assert (res_dir / "groq.yaml").exists()
        # no cross-provider eviction: groq file untouched
        assert "g1" in (res_dir / "groq.yaml").read_text()
