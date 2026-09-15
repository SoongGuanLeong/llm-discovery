"""Judge 429/503 retry: transient rate limits must be waited out, not masked as
a drop verdict. The judge (LocalLLMEvaluator) is the integration seam; here we
mock JudgeTransport + time.sleep to assert retry/backoff behavior offline.
"""
import httpx
import json

from llm_discovery.judge_transport import JudgeTransport
from llm_discovery.json_repair import extract_json, repair_json
from llm_discovery.llm import LocalLLMEvaluator
from llm_discovery.search import NoopSearcher


class _Resp:
    def __init__(self, status_code, headers=None, json_data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)

    def json(self):
        return self._json


def _evaluator():
    return LocalLLMEvaluator(
        base_url="https://apihub.agnes-ai.com/v1",
        model="mimo-v2.5-free",
        api_key="fake",
        min_score=24,
        search_web=NoopSearcher().search,
    )


def _transport():
    return JudgeTransport(
        base_url="https://apihub.agnes-ai.com/v1",
        model="mimo-v2.5-free",
        api_key="fake",
    )


def test_post_retries_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            return _Resp(429, headers={})
        return _Resp(200, json_data={"choices": [{"message": {"content": "x"}}]})

    monkeypatch.setattr("llm_discovery.judge_transport.httpx.post", post)
    monkeypatch.setattr("llm_discovery.judge_transport.time.sleep", lambda s: None)

    resp = _transport().post_chat([{"role": "user", "content": "hi"}])
    assert resp.status_code == 200
    assert calls["n"] == 3


def test_post_exhausts_retries_and_returns_final(monkeypatch):
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        return _Resp(429, headers={})

    monkeypatch.setattr("llm_discovery.judge_transport.httpx.post", post)
    monkeypatch.setattr("llm_discovery.judge_transport.time.sleep", lambda s: None)

    resp = _transport().post_chat([{"role": "user", "content": "hi"}])
    assert resp.status_code == 429
    assert calls["n"] == 4  # initial + 3 retries


def test_post_honors_retry_after_header(monkeypatch):
    slept = []

    def post(*a, **k):
        return _Resp(429, headers={"retry-after": "3"})

    monkeypatch.setattr("llm_discovery.judge_transport.httpx.post", post)
    monkeypatch.setattr("llm_discovery.judge_transport.time.sleep", lambda s: slept.append(s))

    resp = _transport().post_chat([{"role": "user", "content": "hi"}])
    assert resp.status_code == 429
    assert slept[0] == 3  # Retry-After honored, not the exponential default


def test_post_backs_off_exponentially_without_header(monkeypatch):
    slept = []

    def post(*a, **k):
        return _Resp(429, headers={})

    monkeypatch.setattr("llm_discovery.judge_transport.httpx.post", post)
    monkeypatch.setattr("llm_discovery.judge_transport.time.sleep", lambda s: slept.append(s))

    _transport().post_chat([{"role": "user", "content": "hi"}])
    assert slept == [10, 20, 40]  # exponential backoff, capped


def test_post_delegates_to_transport(monkeypatch):
    """LocalLLMEvaluator._post delegates to JudgeTransport.post_chat."""
    def fake_post_chat(self, messages, disable_tools=False):
        return _Resp(200, json_data={"choices": [{"message": {"content": "delegated"}}]})

    monkeypatch.setattr(JudgeTransport, "post_chat", fake_post_chat)

    resp = _evaluator()._post([{"role": "user", "content": "hi"}])
    assert resp.status_code == 200


# _extract_json must pull JSON out when the judge leads with prose + a fence.
_Q = chr(96) * 3


def test_extract_json_bare_object():
    assert json.loads(extract_json('{"a": 1}')) == {"a": 1}


def test_extract_json_strips_fenced_block_with_leading_prose():
    payload = '{"canonical_name": "X", "decision": "keep"}'
    content = (
        "The evidence is clear:" + chr(10) + chr(10)
        + "- AA candidate matches" + chr(10) + chr(10)
        + _Q + "json" + chr(10) + payload + chr(10) + _Q
    )
    assert json.loads(extract_json(content)) == {"canonical_name": "X", "decision": "keep"}


def test_extract_json_strips_fence_without_lang_tag():
    payload = '{"a": 2}'
    content = "intro" + chr(10) + _Q + chr(10) + payload + chr(10) + _Q
    assert json.loads(extract_json(content)) == {"a": 2}


def test_extract_json_prefixed_fence_with_lang():
    payload = '{"a": 3}'
    content = _Q + "json" + chr(10) + payload + chr(10) + _Q
    assert json.loads(extract_json(content)) == {"a": 3}


def test_extract_json_brace_on_fence_line():
    """JSON with { on the same line as the fence tag — raw_decode rescue."""
    content = _Q + "json" + "{" + "\n  \"canonical_name\": \"X\"," + "\n  \"decision\": \"keep\"" + "\n}\n" + _Q
    result = extract_json(content)
    assert json.loads(result) == {"canonical_name": "X", "decision": "keep"}


def test_extract_json_trailing_prose_after_object():
    """JSON followed by extra prose — raw_decode strips trailing text."""
    content = '{"a": 1}\n\nSome extra prose here.'
    result = extract_json(content)
    assert json.loads(result) == {"a": 1}


def test_extract_json_prose_before_object():
    """Prose before the JSON object — raw_decode finds the first {."""
    content = 'Here is the answer:\n{"a": 1}\nDone.'
    result = extract_json(content)
    assert json.loads(result) == {"a": 1}


def test_repair_json_escapes_newlines_in_strings():
    bad = '{"evidence": ["line1\nline2"]}'
    assert json.loads(repair_json(bad)) == {"evidence": ["line1\nline2"]}


def test_repair_json_strips_trailing_commas():
    bad = '{"a": 1, "b": 2,}'
    assert json.loads(repair_json(bad)) == {"a": 1, "b": 2}


def test_repair_json_handles_nested_with_newlines():
    bad = '{"decision": "keep",\n"evidence": ["Qwen3\nbenchmark score"]}'
    assert json.loads(repair_json(bad)) == {
        "decision": "keep",
        "evidence": ["Qwen3\nbenchmark score"],
    }


# --- Judge retry-on-invalid-JSON -------------------------------------------

_valid_body = {
    "canonical_name": "X",
    "coding": True,
    "aa_relevance": "none",
    "confidence": 0.9,
    "decision": "keep",
    "evidence_level": "strong",
    "evidence": ["ok"],
    "coding_assessment": None,
}


def test_evaluate_retries_on_invalid_json_then_succeeds(monkeypatch):
    """First response has garbage content; second response returns valid JSON."""
    responses = iter([
        _Resp(200, json_data={"choices": [{"message": {"content": "garbage not json"}}]}),
        _Resp(200, json_data={"choices": [{"message": {"content": json.dumps(_valid_body)}}]}),
    ])

    def fake_post_chat(self, messages, disable_tools=False):
        return next(responses)

    monkeypatch.setattr(JudgeTransport, "post_chat", fake_post_chat)

    ev = _evaluator()
    from llm_discovery.evaluation import ModelEvaluationRequest
    req = ModelEvaluationRequest(provider="groq", model_id="test-model")
    result = ev.evaluate(req)
    assert result.decision == "keep"
    assert result.canonical_name == "X"


def test_evaluate_raises_after_one_invalid_json_retry(monkeypatch):
    """Two consecutive invalid JSON responses -> RuntimeError (-> error record)."""
    bad = {"choices": [{"message": {"content": "still garbage"}}]}

    def fake_post_chat(self, messages, disable_tools=False):
        return _Resp(200, json_data=bad)

    monkeypatch.setattr(JudgeTransport, "post_chat", fake_post_chat)

    ev = _evaluator()
    from llm_discovery.evaluation import ModelEvaluationRequest
    req = ModelEvaluationRequest(provider="groq", model_id="test-model")

    try:
        ev.evaluate(req)
        assert False, "should have raised"
    except RuntimeError as exc:
        assert "invalid JSON" in str(exc)


def test_evaluate_truncation_respects_search_results(monkeypatch):
    """Issue #215: llm.py:260 previously hardcoded result[:3], truncating
    even when SEARCH_MAX_RESULTS=5. The fix removes the redundant slice so
    all results from the searcher are passed to the judge.
    """
    import os
    from unittest.mock import patch

    # Create 5 search results
    search_results = [
        {"title": f"result{i}", "url": f"https://example.com/{i}", "snippet": f"snippet{i}"}
        for i in range(5)
    ]

    tool_content = None
    call_count = [0]

    def mock_post_chat(self, messages, disable_tools=False):
        nonlocal tool_content
        # Check if this is the tool result message
        for msg in messages:
            if msg.get("role") == "tool" and msg.get("content"):
                tool_content = msg["content"]
                break
        # Return tool call response then final JSON
        if call_count[0] == 0:
            call_count[0] += 1
            return _Resp(200, json_data={"choices": [{
                "message": {
                    "tool_calls": [{
                        "id": "call_1",
                        "function": {"name": "search_web", "arguments": json.dumps({"query": "test"})}
                    }],
                    "content": ""
                }
            }]})
        return _Resp(200, json_data={"choices": [{"message": {"content": json.dumps(_valid_body)}}]})

    monkeypatch.setattr(JudgeTransport, "post_chat", mock_post_chat)
    monkeypatch.setattr("llm_discovery.llm.time.sleep", lambda s: None)

    ev = LocalLLMEvaluator(
        base_url="https://apihub.agnes-ai.com/v1",
        model="mimo-v2-5-free",
        api_key="fake",
        min_score=24,
        search_web=lambda q: search_results,  # Return 5 results
    )

    from llm_discovery.evaluation import ModelEvaluationRequest
    req = ModelEvaluationRequest(provider="groq", model_id="test-model")
    result = ev.evaluate(req)
    assert result.decision == "keep"

    # Verify tool_content contains all 5 results (not truncated to 3)
    if tool_content:
        parsed = json.loads(tool_content)
        assert len(parsed) == 5, f"Expected 5 results, got {len(parsed)} - dual-site truncation bug"
