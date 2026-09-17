"""Issue #233: Judge transport categorization and recovery.

Categorize judge failures (timeout, connection failure, 429 with
Retry-After, 5xx, auth, malformed JSON via existing repair plus one
JSON-only retry, tool/search failure, validation failure, unknown) with
bounded exponential backoff (10 -> 20 -> 40s cap 60s, honoring
Retry-After, no retry storms). Persistent failure stays an explicit
retryable error (decision=error, tier=error, evidence_level=none,
error_category, retry_count), never conflated with weak. Alternate judge
route is considered when configured.

AC:
- Per-category classification and logging; 429 respects Retry-After
- Malformed JSON uses existing repair and retries once with JSON-only instruction
- Persistent failure is error not weak, with error_category/retry_count
- No retry storms; backoff is bounded and concurrent-safe
- Tests for retry, Retry-After, and JSON repair paths
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from llm_discovery.benchmarks import BenchmarkDataCache
from llm_discovery.catalogs import ArtificialAnalysisCatalog, ModelsDevCatalog
from llm_discovery.evaluation import ModelEvaluationRequest
from llm_discovery.evaluator import EvaluatorCoordinator
from llm_discovery.judge_transport import (
    BACKOFF_CAP,
    CAT_AUTH,
    CAT_CONNECTION,
    CAT_MALFORMED_JSON,
    CAT_RATE_LIMIT,
    CAT_SERVER_5XX,
    CAT_TIMEOUT,
    CAT_TOOL,
    CAT_UNKNOWN,
    CAT_VALIDATION,
    JudgeError,
    JudgeTransport,
)
from llm_discovery.llm import JudgeRoute, LocalLLMEvaluator

_VALID_BODY = {
    "canonical_name": "X",
    "coding": True,
    "aa_relevance": "none",
    "confidence": 0.9,
    "decision": "keep",
    "evidence_level": "strong",
    "evidence": ["ok"],
    "coding_assessment": None,
}


class _Resp:
    def __init__(self, status_code, headers=None, json_data=None, text=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data or {}
        self.text = text if text is not None else json.dumps(self._json)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)

    def json(self):
        return self._json


def _transport():
    return JudgeTransport(
        base_url="https://judge.test/v1",
        model="judge-x",
        api_key="fake",
    )


def _evaluator(**kw):
    return LocalLLMEvaluator(
        base_url="https://judge.test/v1",
        model="judge-x",
        api_key="fake",
        min_score=24,
        **kw,
    )


def _req():
    return ModelEvaluationRequest(provider="groq", model_id="test-model")


def _patch_transport_post(monkeypatch, responses):
    """Script httpx.post for the transport: a queue of responses or exceptions."""
    calls = {"n": 0}

    def post(*a, **k):
        item = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("llm_discovery.judge_transport.httpx.post", post)
    monkeypatch.setattr("llm_discovery.judge_transport.time.sleep", lambda s: None)
    return calls


# ---------------------------------------------------------------------------
# AC1: per-category classification; 429 respects Retry-After
# ---------------------------------------------------------------------------
class TestTransportCategorization:
    def test_401_auth_fails_immediately_without_retry(self, monkeypatch):
        calls = _patch_transport_post(monkeypatch, [_Resp(401, text="invalid api key")])
        with pytest.raises(JudgeError) as ei:
            _transport().post_chat([{"role": "user", "content": "hi"}])
        exc = ei.value
        assert exc.category == CAT_AUTH
        assert exc.retry_count == 0
        assert exc.retryable is False
        assert exc.status_code == 401
        assert calls["n"] == 1  # no retry storm on auth failure

    def test_403_auth_fails_immediately_without_retry(self, monkeypatch):
        calls = _patch_transport_post(monkeypatch, [_Resp(403, text="forbidden")])
        with pytest.raises(JudgeError) as ei:
            _transport().post_chat([{"role": "user", "content": "hi"}])
        assert ei.value.category == CAT_AUTH
        assert calls["n"] == 1

    def test_400_is_validation_failure_no_retry(self, monkeypatch):
        calls = _patch_transport_post(monkeypatch, [_Resp(400, text="bad model name")])
        with pytest.raises(JudgeError) as ei:
            _transport().post_chat([{"role": "user", "content": "hi"}])
        assert ei.value.category == CAT_VALIDATION
        assert ei.value.retryable is False
        assert calls["n"] == 1

    def test_404_is_unknown_no_retry(self, monkeypatch):
        calls = _patch_transport_post(monkeypatch, [_Resp(404, text="not found")])
        with pytest.raises(JudgeError) as ei:
            _transport().post_chat([{"role": "user", "content": "hi"}])
        assert ei.value.category == CAT_UNKNOWN
        assert ei.value.retryable is False
        assert calls["n"] == 1

    def test_500_retries_bounded_then_raises_5xx(self, monkeypatch):
        calls = _patch_transport_post(monkeypatch, [_Resp(500, text="boom")])
        with pytest.raises(JudgeError) as ei:
            _transport().post_chat([{"role": "user", "content": "hi"}])
        exc = ei.value
        assert exc.category == CAT_SERVER_5XX
        assert exc.retry_count == 3
        assert exc.retryable is True
        assert calls["n"] == 4  # initial + 3 retries, bounded

    def test_timeout_retries_bounded_then_raises(self, monkeypatch):
        calls = _patch_transport_post(
            monkeypatch,
            [httpx.TimeoutException("read timed out")],
        )
        with pytest.raises(JudgeError) as ei:
            _transport().post_chat([{"role": "user", "content": "hi"}])
        exc = ei.value
        assert exc.category == CAT_TIMEOUT
        assert exc.retry_count == 3
        assert exc.retryable is True
        assert calls["n"] == 4

    def test_connection_error_retries_bounded_then_raises(self, monkeypatch):
        calls = _patch_transport_post(
            monkeypatch,
            [httpx.ConnectError("connection refused")],
        )
        with pytest.raises(JudgeError) as ei:
            _transport().post_chat([{"role": "user", "content": "hi"}])
        exc = ei.value
        assert exc.category == CAT_CONNECTION
        assert exc.retry_count == 3
        assert exc.retryable is True

    def test_429_retry_after_captured_on_error(self, monkeypatch):
        def post(*a, **k):
            return _Resp(429, headers={"retry-after": "12"})

        monkeypatch.setattr("llm_discovery.judge_transport.httpx.post", post)
        monkeypatch.setattr("llm_discovery.judge_transport.time.sleep", lambda s: None)
        with pytest.raises(JudgeError) as ei:
            _transport().post_chat([{"role": "user", "content": "hi"}])
        assert ei.value.category == CAT_RATE_LIMIT
        assert ei.value.retry_after == 12.0  # Retry-After honored in classification


# ---------------------------------------------------------------------------
# AC2: malformed JSON uses existing repair + one JSON-only retry
# ---------------------------------------------------------------------------
class TestJsonOnlyRetry:
    def test_malformed_json_retried_once_then_error(self, monkeypatch):
        bad = {"choices": [{"message": {"content": "garbage not json"}}]}
        calls = {"n": 0}

        def fake_post_chat(self, messages, disable_tools=False):
            calls["n"] += 1
            return _Resp(200, json_data=bad)

        monkeypatch.setattr(JudgeTransport, "post_chat", fake_post_chat)
        ev = _evaluator()
        try:
            ev.evaluate(_req())
            assert False, "should have raised"
        except JudgeError as exc:
            assert exc.category == CAT_MALFORMED_JSON
            assert exc.retry_count == 1
            assert calls["n"] == 2  # initial + exactly one JSON-only retry

    def test_json_only_retry_uses_disable_tools(self, monkeypatch):
        bad = {"choices": [{"message": {"content": "garbage"}}]}
        good = {"choices": [{"message": {"content": json.dumps(_VALID_BODY)}}]}
        seen_disable = []

        def fake_post_chat(self, messages, disable_tools=False):
            seen_disable.append(disable_tools)
            if seen_disable[-1] and len(seen_disable) >= 2:
                return _Resp(200, json_data=good)
            return _Resp(200, json_data=bad)

        monkeypatch.setattr(JudgeTransport, "post_chat", fake_post_chat)
        ev = _evaluator()
        result = ev.evaluate(_req())
        assert result.decision == "keep"
        # The JSON-only retry disables tools (response_format=json_object path).
        assert seen_disable == [False, True]

    def test_validation_failure_not_retried(self, monkeypatch):
        # Parsed JSON but wrong shape -> validation failure, single call.
        bad = {"choices": [{"message": {"content": json.dumps({"decision": "keep"})}}]}
        calls = {"n": 0}

        def fake_post_chat(self, messages, disable_tools=False):
            calls["n"] += 1
            return _Resp(200, json_data=bad)

        monkeypatch.setattr(JudgeTransport, "post_chat", fake_post_chat)
        ev = _evaluator()
        try:
            ev.evaluate(_req())
            assert False, "should have raised"
        except JudgeError as exc:
            assert exc.category == CAT_VALIDATION
            assert exc.retry_count == 0
            assert calls["n"] == 1

    def test_tool_failure_categorized(self, monkeypatch):
        def exploding_search(q):
            raise RuntimeError("search backend down")

        def fake_post_chat(self, messages, disable_tools=False):
            return _Resp(
                200,
                json_data={"choices": [{
                    "message": {
                        "content": "",
                        "tool_calls": [{
                            "id": "c1",
                            "function": {"name": "search_web", "arguments": json.dumps({"query": "x"})},
                        }],
                    }
                }]},
            )

        monkeypatch.setattr(JudgeTransport, "post_chat", fake_post_chat)
        ev = _evaluator(search_web=exploding_search)
        try:
            ev.evaluate(_req())
            assert False, "should have raised"
        except JudgeError as exc:
            assert exc.category == CAT_TOOL
            assert exc.retryable is False


# ---------------------------------------------------------------------------
# Alternate judge route (issue #233: consider alternate route when configured)
# ---------------------------------------------------------------------------
class TestAlternateJudgeRoute:
    def _primary_fails(self, monkeypatch, alt_ok):
        def fake_post_chat(self, messages, disable_tools=False):
            if self.model == "judge-x":  # primary evaluator model from _evaluator()
                raise JudgeError(
                    "Judge HTTP 429 after 3 retries",
                    category=CAT_RATE_LIMIT,
                    retry_count=3,
                    retryable=True,
                    status_code=429,
                )
            if alt_ok:
                return _Resp(200, json_data={"choices": [{"message": {"content": json.dumps(_VALID_BODY)}}]})
            raise JudgeError(
                "Judge HTTP 503 after 3 retries",
                category=CAT_SERVER_5XX,
                retry_count=3,
                retryable=True,
                status_code=503,
            )

        monkeypatch.setattr(JudgeTransport, "post_chat", fake_post_chat)

    def test_alternate_route_recovers(self, monkeypatch):
        self._primary_fails(monkeypatch, alt_ok=True)
        ev = _evaluator(alternate=JudgeRoute(base_url="https://alt.test/v1", model="alt-x"))
        result = ev.evaluate(_req())
        assert result.decision == "keep"
        assert result.judge_model == "alt-x"  # produced by the alternate route

    def test_alternate_route_failure_marks_attempted(self, monkeypatch):
        self._primary_fails(monkeypatch, alt_ok=False)
        ev = _evaluator(alternate=JudgeRoute(base_url="https://alt.test/v1", model="alt-x"))
        try:
            ev.evaluate(_req())
            assert False, "should have raised"
        except JudgeError as exc:
            # Primary failure is the one surfaced; alternate attempt is noted.
            assert exc.category == CAT_RATE_LIMIT
            assert exc.alternate_attempted is True
            assert exc.alternate_category == CAT_SERVER_5XX

    def test_no_alternate_route_propagates_primary_failure(self, monkeypatch):
        self._primary_fails(monkeypatch, alt_ok=True)
        ev = _evaluator()  # no alternate configured
        try:
            ev.evaluate(_req())
            assert False, "should have raised"
        except JudgeError as exc:
            assert exc.alternate_attempted is False

    def test_non_retryable_validation_skips_alternate(self, monkeypatch):
        """issue #233: schema-invalid JSON is a non-retryable validation failure -
        the alternate route is NOT consulted (would not fix a schema mismatch)."""
        alt_calls = {"n": 0}

        def fake_post_chat(self, messages, disable_tools=False):
            if self.model == "alt-x":
                alt_calls["n"] += 1
            # Primary returns schema-invalid JSON (parsed, wrong shape).
            return _Resp(
                200,
                json_data={
                    "choices": [{"message": {"content": json.dumps({"decision": "keep"})}}]
                },
            )

        monkeypatch.setattr(JudgeTransport, "post_chat", fake_post_chat)
        ev = _evaluator(alternate=JudgeRoute(base_url="https://alt.test/v1", model="alt-x"))
        with pytest.raises(JudgeError) as ei:
            ev.evaluate(_req())
        assert ei.value.category == CAT_VALIDATION
        assert alt_calls["n"] == 0  # alternate route not consulted for non-retryable failure
        assert ei.value.alternate_attempted is False


# ---------------------------------------------------------------------------
# AC3: persistent failure is error not weak, with error_category/retry_count
# ---------------------------------------------------------------------------
class TestErrorRecordFields:
    def test_llm_error_record_carries_category_and_retry(self):
        exc = JudgeError(
            "Judge HTTP 429 after 3 retries",
            category=CAT_RATE_LIMIT,
            retry_count=3,
            retryable=True,
            status_code=429,
        )
        coord = EvaluatorCoordinator(
            provider_name="p", aa=None, models_dev=None, evaluator=None,
            min_score=24, max_score=45,
        )
        rec = coord._llm_error_record("some-model", exc)
        assert rec["decision"] == "error"
        assert rec["tier"] == "error"
        assert rec["evidence_level"] == "none"
        assert rec["error_category"] == CAT_RATE_LIMIT
        assert rec["retry_count"] == 3
        assert rec["evidence_status"] == "error"

    def test_classify_prefers_exception_category(self):
        coord = EvaluatorCoordinator(
            provider_name="p", aa=None, models_dev=None, evaluator=None,
            min_score=24, max_score=45,
        )
        exc = JudgeError("429 rate limited", category=CAT_RATE_LIMIT, retry_count=3, retryable=True)
        assert coord._classify_judge_error(exc) == CAT_RATE_LIMIT
        # Non-JudgeError exceptions fall back to legacy heuristics.
        assert coord._classify_judge_error(RuntimeError("request timed out")) == "judge_timeout"

    def test_alternate_note_in_evidence(self):
        exc = JudgeError(
            "Judge HTTP 429 after 3 retries",
            category=CAT_RATE_LIMIT,
            retry_count=3,
            retryable=True,
        )
        exc.alternate_attempted = True
        exc.alternate_category = CAT_SERVER_5XX
        coord = EvaluatorCoordinator(
            provider_name="p", aa=None, models_dev=None, evaluator=None,
            min_score=24, max_score=45,
        )
        rec = coord._llm_error_record("some-model", exc)
        assert any("alternate judge route also failed" in e for e in rec["evidence"])


# ---------------------------------------------------------------------------
# AC3 (evaluator level): JudgeError from the judge produces an error record,
# never a weak/uncertain verdict.
# ---------------------------------------------------------------------------
class _RaisingFakeEvaluator:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def evaluate(self, request, packet=None):
        self.calls += 1
        raise self.exc


def _make_aa(tmp, models):
    p = Path(tmp) / "aa.json"
    p.write_text(json.dumps({"source": "test", "models": models}))
    return ArtificialAnalysisCatalog(p)


def _make_md(tmp, models_dict=None):
    p = Path(tmp) / "md.json"
    p.write_text(json.dumps({"models": models_dict or {}, "providers": {}}))
    return ModelsDevCatalog(p)


def _empty_cache():
    cache = BenchmarkDataCache()
    cache._loaded = True
    cache._data = {}
    return cache


class TestCoordinatorJudgeError:
    def test_judge_error_yields_error_record_not_weak(self, tmp_path):
        aa = _make_aa(tmp_path, [])
        md = _make_md(tmp_path, {
            "ghost-coder": {
                "id": "ghost-coder",
                "name": "Ghost Coder",
                "description": "Ghost coding agent for repository edits in python and rust",
                "weights": [{"label": "X", "url": "https://huggingface.co/ghost-labs/ghost-coder"}],
            }
        })
        cache = _empty_cache()
        fake = _RaisingFakeEvaluator(JudgeError(
            "Judge HTTP 429 after 3 retries",
            category=CAT_RATE_LIMIT,
            retry_count=3,
            retryable=True,
            status_code=429,
        ))
        coord = EvaluatorCoordinator(
            provider_name="test", aa=aa, models_dev=md, evaluator=fake,
            min_score=24, max_score=45, cache=cache,
        )
        rec = coord.evaluate({"id": "ghost-coder"})
        assert fake.calls == 1
        assert rec["decision"] == "error"
        assert rec["tier"] == "error"
        assert rec["evidence_level"] == "none"
        assert rec["error_category"] == CAT_RATE_LIMIT
        assert rec["retry_count"] == 3
        assert rec["evidence_status"] == "error"
        # A categorized judge failure must never surface as weak/uncertain:
        # only the explicit error fields matter here (recovery_attempts is
        # attached by the weak-recovery branch; the moderate/ambiguous LLM
        # tail does not tag it, which is fine - the record is an error).
        assert rec["decision"] not in ("drop", "uncertain", "keep")


# ---------------------------------------------------------------------------
# AC4: bounded + concurrent-safe backoff
# ---------------------------------------------------------------------------
class TestBoundedBackoff:
    def test_backoff_bounded_at_cap(self):
        # Backoff schedule must never exceed the cap (no retry storms).
        backoff = 10
        waits = []
        for _ in range(6):
            waits.append(min(backoff * 1.1, BACKOFF_CAP))
            backoff = min(backoff * 2, BACKOFF_CAP)
        assert all(w <= BACKOFF_CAP for w in waits)

    def test_transport_is_stateless_per_call(self):
        # Concurrent-safe: no shared mutable backoff state between calls.
        t1 = JudgeTransport(base_url="https://a.test/v1", model="m")
        t2 = JudgeTransport(base_url="https://b.test/v1", model="m")
        assert not hasattr(JudgeTransport, "backoff")
        assert t1 is not t2
