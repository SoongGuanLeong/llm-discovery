"""Judge 429/503 retry: transient rate limits must be waited out, not masked as
a drop verdict. The judge (LocalLLMEvaluator) is the integration seam; here we
mock only httpx.post + time.sleep to assert retry/backoff behavior offline.
"""
import httpx
import json

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


def test_post_retries_429_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            return _Resp(429, headers={})
        return _Resp(200, json_data={"choices": [{"message": {"content": "x"}}]})

    monkeypatch.setattr("llm_discovery.llm.httpx.post", post)
    monkeypatch.setattr("llm_discovery.llm.time.sleep", lambda s: None)

    resp = _evaluator()._post([{"role": "user", "content": "hi"}])
    assert resp.status_code == 200
    assert calls["n"] == 3


def test_post_exhausts_retries_and_returns_final(monkeypatch):
    calls = {"n": 0}

    def post(*a, **k):
        calls["n"] += 1
        return _Resp(429, headers={})

    monkeypatch.setattr("llm_discovery.llm.httpx.post", post)
    monkeypatch.setattr("llm_discovery.llm.time.sleep", lambda s: None)

    resp = _evaluator()._post([{"role": "user", "content": "hi"}])
    assert resp.status_code == 429
    assert calls["n"] == 4  # initial + 3 retries


def test_post_honors_retry_after_header(monkeypatch):
    slept = []

    def post(*a, **k):
        return _Resp(429, headers={"retry-after": "3"})

    monkeypatch.setattr("llm_discovery.llm.httpx.post", post)
    monkeypatch.setattr("llm_discovery.llm.time.sleep", lambda s: slept.append(s))

    resp = _evaluator()._post([{"role": "user", "content": "hi"}])
    assert resp.status_code == 429
    assert slept[0] == 3  # Retry-After honored, not the exponential default


def test_post_backs_off_exponentially_without_header(monkeypatch):
    slept = []

    def post(*a, **k):
        return _Resp(429, headers={})

    monkeypatch.setattr("llm_discovery.llm.httpx.post", post)
    monkeypatch.setattr("llm_discovery.llm.time.sleep", lambda s: slept.append(s))

    _evaluator()._post([{"role": "user", "content": "hi"}])
    assert slept == [10, 20, 40]  # exponential backoff, capped


# _extract_json must pull JSON out when the judge leads with prose + a fence.
_Q = chr(96) * 3


def test_extract_json_bare_object():
    ev = _evaluator()
    assert json.loads(ev._extract_json('{"a": 1}')) == {"a": 1}


def test_extract_json_strips_fenced_block_with_leading_prose():
    ev = _evaluator()
    payload = '{"canonical_name": "X", "decision": "keep"}'
    content = (
        "The evidence is clear:" + chr(10) + chr(10)
        + "- AA candidate matches" + chr(10) + chr(10)
        + _Q + "json" + chr(10) + payload + chr(10) + _Q
    )
    assert json.loads(ev._extract_json(content)) == {"canonical_name": "X", "decision": "keep"}


def test_extract_json_strips_fence_without_lang_tag():
    ev = _evaluator()
    payload = '{"a": 2}'
    content = "intro" + chr(10) + _Q + chr(10) + payload + chr(10) + _Q
    assert json.loads(ev._extract_json(content)) == {"a": 2}


def test_extract_json_prefixed_fence_with_lang():
    ev = _evaluator()
    payload = '{"a": 3}'
    content = _Q + "json" + chr(10) + payload + chr(10) + _Q
    assert json.loads(ev._extract_json(content)) == {"a": 3}


def test_extract_json_brace_on_fence_line():
    """JSON with { on the same line as the fence tag — raw_decode rescue."""
    ev = _evaluator()
    content = _Q + "json" + "{" + "\n  \"canonical_name\": \"X\"," + "\n  \"decision\": \"keep\"" + "\n}\n" + _Q
    result = ev._extract_json(content)
    assert json.loads(result) == {"canonical_name": "X", "decision": "keep"}


def test_extract_json_trailing_prose_after_object():
    """JSON followed by extra prose — raw_decode strips trailing text."""
    ev = _evaluator()
    content = '{"a": 1}\n\nSome extra prose here.'
    result = ev._extract_json(content)
    assert json.loads(result) == {"a": 1}


def test_extract_json_prose_before_object():
    """Prose before the JSON object — raw_decode finds the first {."""
    ev = _evaluator()
    content = 'Here is the answer:\n{"a": 1}\nDone.'
    result = ev._extract_json(content)
    assert json.loads(result) == {"a": 1}


def test_repair_json_escapes_newlines_in_strings():
    ev = _evaluator()
    bad = '{"evidence": ["line1\nline2"]}'
    assert json.loads(ev._repair_json(bad)) == {"evidence": ["line1\nline2"]}


def test_repair_json_strips_trailing_commas():
    ev = _evaluator()
    bad = '{"a": 1, "b": 2,}'
    assert json.loads(ev._repair_json(bad)) == {"a": 1, "b": 2}


def test_repair_json_handles_nested_with_newlines():
    ev = _evaluator()
    bad = '{"decision": "keep",\n"evidence": ["Qwen3\nbenchmark score"]}'
    assert json.loads(ev._repair_json(bad)) == {
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

    def fake_post(*a, **k):
        return next(responses)

    monkeypatch.setattr("llm_discovery.llm.httpx.post", fake_post)
    monkeypatch.setattr("llm_discovery.llm.time.sleep", lambda s: None)

    ev = _evaluator()
    from llm_discovery.evaluation import ModelEvaluationRequest
    req = ModelEvaluationRequest(provider="groq", model_id="test-model")
    result = ev.evaluate(req)
    assert result.decision == "keep"
    assert result.canonical_name == "X"


def test_evaluate_raises_after_one_invalid_json_retry(monkeypatch):
    """Two consecutive invalid JSON responses → RuntimeError (→ error record)."""
    bad = {"choices": [{"message": {"content": "still garbage"}}]}

    def fake_post(*a, **k):
        return _Resp(200, json_data=bad)

    monkeypatch.setattr("llm_discovery.llm.httpx.post", fake_post)
    monkeypatch.setattr("llm_discovery.llm.time.sleep", lambda s: None)

    ev = _evaluator()
    from llm_discovery.evaluation import ModelEvaluationRequest
    req = ModelEvaluationRequest(provider="groq", model_id="test-model")

    try:
        ev.evaluate(req)
        assert False, "should have raised"
    except RuntimeError as exc:
        assert "invalid JSON" in str(exc)

