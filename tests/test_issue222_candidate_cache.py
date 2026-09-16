"""Issue #222: Weak/none candidate caching (long TTL).

Weak/none evidence_hash is cached as Candidate/uncertain with a 60-90d TTL so the
same no-evidence model is not retried every build. A Candidate never becomes a
Keeper: is_accurate_enough still blocks. Distinct from the slim v2 Source of Truth
Keeper store — a separate Candidate store (data/model_candidate_store.json).

Acceptance criteria:
- AC1: Weak/none returns uncertain without LLM and is cached 60-90d
- AC2: Second build with identical evidence_hash skips LLM (evaluator call count 0)
- AC3: Weak never inserted as Keeper into slim v2 Source of Truth
- AC4: Weak models not retried each build; measured via build_all telemetry

Offline + deterministic: tmp catalogs, empty benchmark cache, ISO timestamps
relative to now (pattern: test_issue221_per_evidence_ttl.py).
"""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from llm_discovery.benchmarks import BenchmarkDataCache
from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from llm_discovery.candidate_store import (
    CANDIDATE_TTL_DAYS,
    CandidateCacheStats,
    CandidateRecord,
    CandidateStore,
)
from llm_discovery.evaluation import ModelEvaluation
from llm_discovery.evaluator import EvaluatorCoordinator, resolve_cache_identity
from llm_discovery.gate import is_accurate_enough
from llm_discovery.model_info_store import ModelInfoStore, compute_evidence_hash, is_stale


def _make_aa(tmp, models):
    data = {"source": "test", "models": models}
    p = Path(tmp) / "aa.json"
    p.write_text(json.dumps(data))
    return ArtificialAnalysisCatalog(p)


def _make_md(tmp, models_dict=None):
    data = {"models": models_dict or {}, "providers": {}}
    p = Path(tmp) / "md.json"
    p.write_text(json.dumps(data))
    return ModelsDevCatalog(p)


def _empty_cache(tmp):
    """Benchmark cache pinned at a non-existent path: never reads disk, stays empty."""
    cache = BenchmarkDataCache(cache_path=Path(tmp) / "no-benchmarks.json")
    cache._loaded = True
    cache._data = {}
    return cache


class _CountingFake:
    def __init__(self, result):
        self.calls = 0
        self.result = result
        self.last_request = None

    def evaluate(self, request, packet=None):
        self.calls += 1
        self.last_request = request
        return self.result


def _weak_llm_result():
    return ModelEvaluation(
        coding=False,
        decision="drop",
        confidence=0.4,
        evidence_level="weak",
        evidence=["unverified claim only"],
        coding_assessment=None,
    )


def _iso_days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _no_evidence_hash() -> str:
    """Evidence hash for a model with no AA, no benchmarks, no pricing, no URLs."""
    return compute_evidence_hash(None, {}, None, [])


def _make_coord(tmp, fake, aa, md, cache, store=None, cstore=None, provider="xkiro"):
    return EvaluatorCoordinator(
        provider_name=provider,
        aa=aa,
        models_dev=md,
        evaluator=fake,
        min_score=24,
        max_score=45,
        cache=cache,
        store=store,
        candidate_store=cstore,
    )


class TestCandidateTTLConstant:
    def test_ttl_within_60_90_days(self):
        assert 60 <= CANDIDATE_TTL_DAYS <= 90


class TestCandidateStoreUnit:
    def test_put_get_delete_persistence_roundtrip(self, tmp_path):
        path = tmp_path / "model_candidate_store.json"
        store = CandidateStore(path)
        assert store.size() == 0
        rec = CandidateRecord(
            model_id="weak-model",
            evidence_hash=_no_evidence_hash(),
            evidence_level="weak",
            decision="uncertain",
            tier="uncertain",
            last_updated=_iso_days_ago(0),
        )
        store.put("weak-model", rec)
        assert store.size() == 1
        assert path.exists()
        # Fresh instance reloads from disk (build boundary)
        reloaded = CandidateStore(path)
        got = reloaded.get("weak-model")
        assert got is not None
        assert got.evidence_hash == rec.evidence_hash
        assert got.evidence_level == "weak"
        assert got.model_id == "weak-model"
        # delete persists
        assert reloaded.delete("weak-model") is True
        assert CandidateStore(path).get("weak-model") is None
        assert reloaded.delete("missing-key") is False

    def test_stats_thread_safe(self):
        stats = CandidateCacheStats()
        def bump():
            for _ in range(100):
                stats.record_hit()
                stats.record_miss()
        threads = [threading.Thread(target=bump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert stats.hits == 400
        assert stats.misses == 400


class TestAC1WeakUncertainCached:
    def test_weak_returns_uncertain_without_llm(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache = _empty_cache(tmp_path)
        fake = _CountingFake(_weak_llm_result())
        cstore = CandidateStore(tmp_path / "model_candidate_store.json")
        coord = _make_coord(tmp_path, fake, aa, md, cache, cstore=cstore)
        rec = coord.evaluate({"id": "xkiro-weak-1"})
        assert rec["decision"] == "uncertain"
        assert rec["tier"] == "uncertain"
        assert rec["evidence_level"] == "weak"
        assert fake.calls == 0

    def test_weak_candidate_stored_with_evidence_hash(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache = _empty_cache(tmp_path)
        fake = _CountingFake(_weak_llm_result())
        cstore = CandidateStore(tmp_path / "model_candidate_store.json")
        coord = _make_coord(tmp_path, fake, aa, md, cache, cstore=cstore)
        coord.evaluate({"id": "xkiro-weak-1"})
        key = resolve_cache_identity("xkiro-weak-1", None)
        entry = cstore.get(key)
        assert entry is not None
        assert entry.evidence_hash == _no_evidence_hash()
        assert entry.evidence_level == "weak"
        assert entry.model_id == "xkiro-weak-1"
        assert entry.last_updated is not None
        datetime.fromisoformat(entry.last_updated.replace("Z", "+00:00"))  # parseable ISO
        assert cstore.size() == 1


class TestAC2SecondBuildSkipsLLM:
    def test_identical_evidence_hash_hits_candidate_no_llm(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache = _empty_cache(tmp_path)
        fake = _CountingFake(_weak_llm_result())
        cstore = CandidateStore(tmp_path / "model_candidate_store.json")
        coord = _make_coord(tmp_path, fake, aa, md, cache, cstore=cstore)
        rec1 = coord.evaluate({"id": "xkiro-weak-1"})
        assert rec1["source"] == "deterministic"  # first build: fresh evaluation
        rec2 = coord.evaluate({"id": "xkiro-weak-1"})
        assert fake.calls == 0
        assert rec2["decision"] == "uncertain"
        assert rec2["tier"] == "uncertain"
        assert rec2["evidence_level"] == "weak"
        assert rec2["source"] == "candidate_cache"
        assert rec2.get("cached") is True
        assert cstore.stats.hits >= 1

    def test_candidate_hit_across_build_boundary(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache = _empty_cache(tmp_path)
        fake = _CountingFake(_weak_llm_result())
        store_path = tmp_path / "model_candidate_store.json"
        cstore1 = CandidateStore(store_path)
        coord1 = _make_coord(tmp_path, fake, aa, md, cache, cstore=cstore1)
        coord1.evaluate({"id": "xkiro-weak-1"})
        # Second build: fresh store instance (reloaded from disk) + fresh coordinator
        cstore2 = CandidateStore(store_path)
        coord2 = _make_coord(tmp_path, fake, aa, md, cache, cstore=cstore2)
        rec2 = coord2.evaluate({"id": "xkiro-weak-1"})
        assert fake.calls == 0
        assert rec2["source"] == "candidate_cache"
        assert rec2["decision"] == "uncertain"
        assert cstore2.stats.hits == 1


class TestHashChangeAndPromotion:
    def test_aa_appears_misses_reevaluates_and_deletes_candidate(self, tmp_path):
        aa0 = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache = _empty_cache(tmp_path)
        fake = _CountingFake(_weak_llm_result())
        cstore = CandidateStore(tmp_path / "model_candidate_store.json")
        key = resolve_cache_identity("promote-model", None)
        # Build 1: no evidence -> weak -> candidate stored
        coord0 = _make_coord(tmp_path, fake, aa0, md, cache, cstore=cstore)
        rec0 = coord0.evaluate({"id": "promote-model"})
        assert rec0["decision"] == "uncertain"
        assert cstore.get(key) is not None
        # Build 2: evidence recovered (AA score 58 -> deterministic strong)
        aa1 = _make_aa(tmp_path, [{"id": "aa-promote", "name": "Promote", "slug": "promote-model", "evaluations": {"artificial_analysis_intelligence_index": 58}}])
        coord1 = _make_coord(tmp_path, fake, aa1, md, cache, cstore=cstore)
        rec1 = coord1.evaluate({"id": "promote-model"})
        # hash changed -> miss (re-evaluated, not served from candidate cache)
        assert rec1["source"] == "deterministic"
        assert rec1["evidence_level"] == "strong"
        assert rec1["decision"] == "keep"
        assert fake.calls == 0
        assert cstore.stats.misses >= 1
        # Model became a Keeper -> stale candidate entry deleted
        assert cstore.get(key) is None
        assert cstore.size() == 0

    def test_hash_change_still_weak_reevaluates_and_updates_entry(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache0 = _empty_cache(tmp_path)
        fake = _CountingFake(_weak_llm_result())
        cstore = CandidateStore(tmp_path / "model_candidate_store.json")
        key = resolve_cache_identity("low-bench-model", None)
        coord0 = _make_coord(tmp_path, fake, aa, md, cache0, cstore=cstore)
        coord0.evaluate({"id": "low-bench-model"})
        h1 = cstore.get(key).evidence_hash
        assert h1 == _no_evidence_hash()
        # Build 2: a low benchmark appears (score 15 < 20/30 floors -> still weak)
        cache1 = _empty_cache(tmp_path)
        cache1._data["low-bench-model"] = {"benchmarks": {"aider_polyglot": {"score": 15, "source": "https://example.com"}}}
        coord1 = _make_coord(tmp_path, fake, aa, md, cache1, cstore=cstore)
        rec1 = coord1.evaluate({"id": "low-bench-model"})
        assert rec1["source"] == "deterministic"  # miss, re-evaluated
        assert rec1["decision"] == "uncertain"
        assert rec1["evidence_level"] == "weak"
        assert cstore.stats.misses >= 1
        entry = cstore.get(key)
        assert entry.evidence_hash != h1  # evidence state changed -> new hash
        assert entry.evidence_level == "weak"
        # Third build with unchanged evidence hits again
        rec3 = coord1.evaluate({"id": "low-bench-model"})
        assert rec3["source"] == "candidate_cache"
        assert fake.calls == 0


class TestCandidateTTL:
    def test_91_days_old_candidate_misses(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache = _empty_cache(tmp_path)
        fake = _CountingFake(_weak_llm_result())
        cstore = CandidateStore(tmp_path / "model_candidate_store.json")
        key = resolve_cache_identity("ttl-weak-old", None)
        cstore.put(key, CandidateRecord(
            model_id="ttl-weak-old",
            evidence_hash=_no_evidence_hash(),
            evidence_level="weak",
            last_updated=_iso_days_ago(91),
        ))
        coord = _make_coord(tmp_path, fake, aa, md, cache, cstore=cstore)
        rec = coord.evaluate({"id": "ttl-weak-old"})
        assert rec["source"] == "deterministic"  # stale -> re-evaluated
        assert rec["decision"] == "uncertain"
        assert cstore.stats.misses >= 1
        # weak branch refreshed the entry with a fresh timestamp
        entry = cstore.get(key)
        assert not is_stale(entry.last_updated, CANDIDATE_TTL_DAYS)

    def test_89_days_old_candidate_hits(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache = _empty_cache(tmp_path)
        fake = _CountingFake(_weak_llm_result())
        cstore = CandidateStore(tmp_path / "model_candidate_store.json")
        key = resolve_cache_identity("ttl-weak-fresh", None)
        cstore.put(key, CandidateRecord(
            model_id="ttl-weak-fresh",
            evidence_hash=_no_evidence_hash(),
            evidence_level="weak",
            last_updated=_iso_days_ago(89),
        ))
        coord = _make_coord(tmp_path, fake, aa, md, cache, cstore=cstore)
        rec = coord.evaluate({"id": "ttl-weak-fresh"})
        assert rec["source"] == "candidate_cache"
        assert rec.get("cached") is True
        assert rec["decision"] == "uncertain"
        assert rec["evidence_level"] == "weak"
        assert fake.calls == 0
        assert cstore.stats.hits == 1


class TestAC3NeverKeeper:
    def test_weak_not_in_keeper_store(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache = _empty_cache(tmp_path)
        fake = _CountingFake(_weak_llm_result())
        store = ModelInfoStore(tmp_path / "model_info_store.json")
        cstore = CandidateStore(tmp_path / "model_candidate_store.json")
        coord = _make_coord(tmp_path, fake, aa, md, cache, store=store, cstore=cstore)
        rec = coord.evaluate({"id": "keeper-block-model"})
        assert rec["decision"] == "uncertain"
        # AC3: absent from the slim v2 Source of Truth (not just judge-less)
        assert store.get("keeper-block-model") is None
        assert not (tmp_path / "model_info_store.json").exists()
        # ... but present in the separate Candidate store
        assert cstore.get(resolve_cache_identity("keeper-block-model", None)) is not None
        # Gate still blocks it from ever being a Keeper
        ok, reason = is_accurate_enough(rec)
        assert ok is False
        assert reason != ""

    def test_no_candidate_store_default_no_side_effects(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache = _empty_cache(tmp_path)
        fake = _CountingFake(_weak_llm_result())
        store = ModelInfoStore(tmp_path / "model_info_store.json")
        coord = _make_coord(tmp_path, fake, aa, md, cache, store=store)  # candidate_store=None
        rec = coord.evaluate({"id": "plain-weak-model"})
        assert rec["decision"] == "uncertain"
        assert fake.calls == 0
        # weak no longer pollutes the Keeper store, nothing else written
        assert store.get("plain-weak-model") is None
        assert not (tmp_path / "model_candidate_store.json").exists()


class TestAC4BuildAllTelemetry:
    """Two build_all runs against the same tmp data dir: weak models not retried."""
    WEAK_IDS = ("xkiro-weak-1", "xkiro-weak-2", "xkiro-weak-3")

    def test_two_builds_candidate_cache_hits(self, tmp_path):
        from llm_discovery.build_all import build_all
        from llm_discovery.config import load_config

        config_path = Path("config/providers.yaml")
        cfg = load_config(config_path)
        names = [p.name for p in cfg.providers[:1]]
        data_dir = tmp_path / "data222"

        aa = _make_aa(tmp_path, [])  # no AA entries: deterministic weak for all
        md = _make_md(tmp_path)
        fake = _CountingFake(_weak_llm_result())

        def discover_fn(name, config, aa_arg, models_dev_arg, max_workers, store=None, candidate_store=None):
            cache = _empty_cache(tmp_path)
            coord = EvaluatorCoordinator(
                provider_name=name,
                aa=aa,
                models_dev=md,
                evaluator=fake,
                min_score=24,
                max_score=45,
                cache=cache,
                store=store,
                candidate_store=candidate_store,
            )
            out = {"keep": [], "drop": [], "uncertain": [], "error": []}
            for mid in self.WEAK_IDS:
                rec = coord.evaluate({"id": mid})
                bucket = {"keep": "keep", "drop": "drop", "uncertain": "uncertain"}.get(rec["decision"], "error")
                out[bucket].append(rec)
            return out

        res1 = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        assert fake.calls == 0  # deterministic weak: no LLM even on the first build
        tel1 = res1["telemetry"]["candidate_cache"]
        assert tel1["hits"] == 0
        assert tel1["misses"] >= len(self.WEAK_IDS)
        assert tel1["size"] >= len(self.WEAK_IDS)
        # weak models land in the uncertain bucket of the Ephemeral Report
        yaml1 = yaml.safe_load((data_dir / "results" / f"{names[0]}.yaml").read_text())
        assert len(yaml1["uncertain"]) == len(self.WEAK_IDS)
        assert yaml1["keep"] == []
        # no Keepers written for weak models
        assert res1["store_size"] == 0

        res2 = build_all(data_dir=data_dir, config_path=config_path, provider_names=names, discover_fn=discover_fn)
        assert fake.calls == 0  # second build: evaluator not called for the weak models
        tel2 = res2["telemetry"]["candidate_cache"]
        assert tel2["hits"] >= 1
        assert tel2["size"] >= len(self.WEAK_IDS)
        assert res2["store_size"] == 0
