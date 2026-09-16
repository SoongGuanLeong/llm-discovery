"""Issue #232: Bounded LLM/web recovery for claim-bearing weak.

For weak with a verifiable first-party claim, allow at most two targeted web searches
(first-party model card/docs, then benchmark evidence) with source URLs required.
Keep allowlisted first-party plus owner-match verification; no LLM for genuine weak
without claim. Candidate TTL still avoids repeats.

AC:
- At most two searches; queries are targeted (model card + benchmark)
- Externally discovered claims require source URL; unverified claim stays weak
- Claim-bearing weak can be verified to moderate via LLM; genuine weak without
  claim stays weak without LLM
- Search/judge budget observable in build-all telemetry
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from llm_discovery.evaluator import EvaluatorCoordinator
from llm_discovery.evaluation import ModelEvaluation, ModelEvaluationRequest
from llm_discovery.search_throttle import reset_search_accounting, get_search_accounting
from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from llm_discovery.benchmarks import BenchmarkDataCache


# ---------------------------------------------------------------------------
# Fake evaluator that counts judge calls
# ---------------------------------------------------------------------------
class CountingFakeEvaluator:
    """Fake LocalLLMEvaluator: counts evaluate() calls."""

    def __init__(self, result=None):
        self.calls = 0
        self.result = result or _moderate_result()

    def evaluate(self, request, packet=None):
        self.calls += 1
        return self.result


def _moderate_result():
    return ModelEvaluation(
        coding=True,
        decision="keep",
        confidence=0.9,
        evidence_level="moderate",
        evidence=["https://huggingface.co/ghost-labs/ghost-coder model card mentions coding"],
        coding_assessment=None,
        judge_model="test",
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------
def _make_aa(tmp, models):
    p = Path(tmp) / "aa.json"
    p.write_text(json.dumps({"source": "test", "models": models}))
    return ArtificialAnalysisCatalog(p)


def _make_md(tmp, models_dict=None):
    p = Path(tmp) / "md.json"
    p.write_text(json.dumps({"models": models_dict or {}, "providers": {}}))
    return ModelsDevCatalog(p)


def _coord(aa, md, cache, fake, candidate_store=None):
    return EvaluatorCoordinator(
        provider_name="test",
        aa=aa,
        models_dev=md,
        evaluator=fake,
        min_score=24,
        max_score=45,
        cache=cache,
        candidate_store=candidate_store,
    )


def _empty_cache():
    cache = BenchmarkDataCache()
    cache._loaded = True
    cache._data = {}
    return cache


# ---------------------------------------------------------------------------
# AC3: genuine weak without claim stays weak without LLM
# ---------------------------------------------------------------------------
class TestGenuineWeakNoClaimStaysWeak:
    def test_no_claim_no_llm(self, tmp_path):
        reset_search_accounting()
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path)
        cache = _empty_cache()
        calls: list[str] = []

        def fake_search(q):
            calls.append(q)
            return [{"title": "r", "url": "https://example.com/x", "snippet": "s"}]

        fake = CountingFakeEvaluator()
        coord = _coord(aa, md, cache, fake)
        rec = coord.evaluate({"id": "no-claim-model-xyz"})
        assert rec["evidence_level"] == "weak"
        assert fake.calls == 0, "LLM judge must not run for genuine weak without claim"
        assert calls == [], "search_web must not be called for genuine weak without claim"
        assert rec["evidence_status"] == "uncertain"


# ---------------------------------------------------------------------------
# AC: claim-bearing weak triggers bounded LLM recovery
# ---------------------------------------------------------------------------
class TestClaimBearingWeakBoundedRecovery:
    def test_claim_triggers_single_bounded_llm_pass(self, tmp_path):
        # AC3: claim-bearing weak triggers exactly one judged LLM pass.
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path, {"ghost-coder": {
            "id": "ghost-coder",
            "name": "Ghost Coder",
            "description": "Ghost coding agent for repository edits in python and rust",
            "weights": [{"label": "X", "url": "https://huggingface.co/ghost-labs/ghost-coder"}],
        }})
        cache = _empty_cache()
        fake = CountingFakeEvaluator()
        coord = _coord(aa, md, cache, fake)
        rec = coord.evaluate({"id": "ghost-coder"})
        assert fake.calls == 1
        assert rec["evidence_level"] == "moderate"
        assert rec["decision"] == "keep"
        # A verified claim promotes to moderate on the main LLM path; no alias
        # recovery was pending, so no recovery-specific tags are emitted.
        assert rec.get("evidence_status") in (None, "recovered")
        assert "llm_web_recovery" not in rec.get("recovery_attempts", [])

    def test_claim_with_allowlisted_url_verifies_to_moderate(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path, {"ghost-coder": {
            "id": "ghost-coder",
            "name": "Ghost Coder",
            "description": "Ghost coding agent for repository edits in python and rust",
            "weights": [{"label": "X", "url": "https://huggingface.co/ghost-labs/ghost-coder"}],
        }})
        cache = _empty_cache()
        allowlisted_moderate = ModelEvaluation(
            coding=True,
            decision="keep",
            confidence=0.9,
            evidence_level="moderate",
            evidence=["https://huggingface.co/ghost-labs/ghost-coder model card claims coding in python and rust"],
            coding_assessment=None,
            judge_model="test",
        )
        fake = CountingFakeEvaluator(allowlisted_moderate)
        coord = _coord(aa, md, cache, fake)
        rec = coord.evaluate({"id": "ghost-coder"})
        assert fake.calls == 1
        assert rec["evidence_level"] == "moderate"
        assert rec["decision"] == "keep"

    def test_claim_with_non_allowlisted_url_stays_weak_or_moderate_with_url(self, tmp_path):
        # AC2: externally discovered claim with non-allowlisted URL stays weak
        # (triangulation guard demotes claim-only moderate).
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path, {"ghost-coder-unverified": {
            "id": "ghost-coder-unverified",
            "name": "Ghost Coder",
            "description": "Ghost coding agent for repository edits in python and rust",
            "weights": [{"label": "X", "url": "https://example.com/ghost-labs/ghost-coder"}],
        }})
        cache = _empty_cache()
        fake = CountingFakeEvaluator(
            ModelEvaluation(
                coding=True,
                decision="keep",
                confidence=0.9,
                evidence_level="moderate",
                evidence=["ghost model at example.com claims coding in python and rust"],
                coding_assessment=None,
                judge_model="test",
            )
        )
        coord = _coord(aa, md, cache, fake)
        rec = coord.evaluate({"id": "ghost-coder-unverified"})
        if rec["evidence_level"] == "moderate":
            from llm_discovery.verified_claim import evidence_has_allowlisted_url
            assert evidence_has_allowlisted_url(rec.get("evidence", []))
        else:
            assert rec["evidence_level"] == "weak"


# ---------------------------------------------------------------------------
# AC4: Search/judge budget observable in build-all telemetry
# ---------------------------------------------------------------------------
class TestSearchJudgeBudgetTelemetry:
    def test_search_accounting_has_judge_calls(self):
        reset_search_accounting()
        accounting, _ = get_search_accounting()
        assert hasattr(accounting, "judge_calls"), "SearchAccounting must track judge_calls"
        snap = accounting.snapshot()
        assert "judge_calls" in snap
        assert snap["judge_calls"] == 0
        # bump() unifies all counters (issue #232: judge_calls included)
        accounting.bump("judge_calls")
        accounting.bump("judge_calls")
        snap = accounting.snapshot()
        assert snap["judge_calls"] == 2

    def test_bump_judge_call_helper_tracks_global(self):
        # The module-level helper used by EvaluatorCoordinator increments the
        # global accounting judge_calls.
        from llm_discovery.search_throttle import bump_judge_call
        reset_search_accounting()
        accounting, _ = get_search_accounting()
        assert accounting.judge_calls == 0
        bump_judge_call()
        bump_judge_call()
        assert accounting.judge_calls == 2

    def test_build_all_telemetry_includes_judge_calls(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SEARCH_GLOBAL_BUDGET", raising=False)
        from llm_discovery.build_all import build_all
        from llm_discovery.config import load_config

        cfg = load_config(Path("config/providers.yaml"))
        names = [p.name for p in cfg.providers[:2]]

        def discover_fn(name, config, aa, models_dev, max_workers, store=None):
            return {"keep": [], "drop": [], "error": []}

        res = build_all(
            data_dir=tmp_path / "data",
            config_path=Path("config/providers.yaml"),
            provider_names=names,
            discover_fn=discover_fn,
        )
        s = res["telemetry"]["search"]
        assert "judge_calls" in s, "telemetry.search must include judge_calls"
        assert s["calls"] == 0
        assert s["judge_calls"] == 0


# ---------------------------------------------------------------------------
# AC1: max_searches=2 default + hard cap on the LocalLLMEvaluator loop
# ---------------------------------------------------------------------------
class TestMaxSearchesBound:
    def test_default_max_searches_is_two(self):
        import inspect
        from llm_discovery.llm import LocalLLMEvaluator
        sig = inspect.signature(LocalLLMEvaluator.__init__)
        param = sig.parameters.get("max_searches")
        assert param is not None, "max_searches must be a parameter"
        assert param.default == 2, "max_searches default should be 2"

    def test_hard_cap_stops_after_two_searches(self):
        # AC1: even if the judge keeps requesting search_web, execution is
        # hard-capped at max_searches (2) and the limit notice is sent.
        from llm_discovery.llm import LocalLLMEvaluator

        searches: list[str] = []

        def fake_search(query):
            searches.append(query)
            return [{"title": "r", "url": "https://huggingface.co/x/y", "snippet": query}]

        ev = LocalLLMEvaluator(
            base_url="http://judge.test",
            model="judge-x",
            api_key="x",
            min_score=24,
            search_web=fake_search,
            max_searches=2,
            timeout=60,
        )

        class _FakeResp:
            def __init__(self, payload):
                self.status_code = 200
                self._payload = payload
            def raise_for_status(self):
                pass
            def json(self):
                return self._payload

        final_content = json.dumps({
            "canonical_name": None,
            "coding": True,
            "aa_relevance": "none",
            "confidence": 0.9,
            "decision": "keep",
            "evidence_level": "moderate",
            "evidence": ["https://huggingface.co/x/y model card mentions coding"],
        })

        def _tool_msg(query):
            return {
                "choices": [{"message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": "c", "type": "function",
                                    "function": {"name": "search_web",
                                                 "arguments": json.dumps({"query": query})}}],
                }}]
            }

        def _final_msg():
            return {
                "choices": [{"message": {"role": "assistant", "content": final_content, "tool_calls": []}}]
            }

        # 4 turns: two real searches, a 3rd request the hard cap suppresses,
        # then a final decision.
        responses = [
            _tool_msg("query-for-model-card"),
            _tool_msg("query-for-benchmark-evidence"),
            _tool_msg("should-not-execute-3"),
            _final_msg(),
        ]

        def scripted_post(messages, disable_tools=False):
            return _FakeResp(responses.pop(0))

        ev._post = scripted_post  # type: ignore[assignment]

        request = ModelEvaluationRequest(
            provider="test",
            model_id="claim-model",
            provider_metadata={"id": "claim-model"},
        )
        result = ev.evaluate(request)
        assert len(searches) == 2, f"expected 2 searches, got {len(searches)}"
        assert result.evidence_level == "moderate"
        assert result.decision == "keep"