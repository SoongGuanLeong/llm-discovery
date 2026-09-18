"""Pricing-endpoint free filter (pay-per-token gateways, e.g. bestvirtualgoods).

Console pricing endpoint (public /api/pricing) tags truly-free models
(tags contains 'free' or model_ratio == 0). The live chat-completions probe
cannot distinguish free from funded-paid on pay-per-token gateways — both
return 200 when the account has credit — so the pricing endpoint is consulted
before the probe. No model names are hardcoded: any host serving the JSON
shape {data: [{model_name, tags, model_ratio, ...}]} benefits.
"""
from __future__ import annotations

import httpx
import pytest

from llm_discovery import pipeline


class FakePricingResponse:
    def __init__(self, status_code: int, payload: object):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> object:
        return self._payload


def _pricing_row(name: str, *, tags: object = None, ratio: object = None) -> dict[str, object]:
    return {"model_name": name, "tags": tags, "model_ratio": ratio}


def _pricing_payload(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"auto_groups": ["default"], "data": rows}


def _get_factory(responses: dict[str, object]):
    def fake_get(url: str, **kwargs: object) -> FakePricingResponse:
        # url like https://<host>/api/pricing
        host = url.split("//", 1)[1].split("/", 1)[0]
        if host not in responses:
            raise httpx.ConnectError("no such host")
        item = responses[host]
        if isinstance(item, Exception):
            raise item
        assert isinstance(item, tuple)
        return FakePricingResponse(item[0], item[1])

    return fake_get


def _model(mid: str) -> dict[str, str]:
    return {"id": mid}


class TestSplitFreeByPricingEndpoint:
    def test_mixed_split_filters_tagged_and_ratio_zero(self, monkeypatch):
        rows = [
            _pricing_row("prov/model-a", tags="free", ratio=0),
            _pricing_row("prov/model-b", tags=None, ratio=0.15),
            _pricing_row("prov/model-c", tags=None, ratio=0),
        ]
        monkeypatch.setattr(httpx, "get", _get_factory({"api.example.com": (200, _pricing_payload(rows))}))
        models = [_model("prov/model-a"), _model("prov/model-b"), _model("prov/model-c")]
        split = pipeline._split_free_by_pricing_endpoint("https://api.example.com/v1", models, "bvg")
        assert split is not None
        free, paid = split
        assert [m["id"] for m in free] == ["prov/model-a", "prov/model-c"]
        assert [m["id"] for m in paid] == ["prov/model-b"]

    def test_falls_back_to_api_host_strip(self, monkeypatch):
        rows = [_pricing_row("prov/free-a", tags="free", ratio=0), _pricing_row("prov/paid", ratio=0.3)]
        # only the web host (api. stripped) serves the endpoint
        monkeypatch.setattr(httpx, "get", _get_factory({"example.com": (200, _pricing_payload(rows))}))
        models = [_model("prov/free-a"), _model("prov/paid")]
        split = pipeline._split_free_by_pricing_endpoint("https://api.example.com/v1", models, "bvg")
        assert split is not None
        free, paid = split
        assert [m["id"] for m in free] == ["prov/free-a"]
        assert [m["id"] for m in paid] == ["prov/paid"]

    def test_none_when_endpoint_missing(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", _get_factory({}))
        models = [_model("prov/a")]
        assert pipeline._split_free_by_pricing_endpoint("https://api.example.com/v1", models, "bvg") is None

    def test_none_when_non_json_404(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", _get_factory({"api.example.com": (404, {})}))
        models = [_model("prov/a")]
        assert pipeline._split_free_by_pricing_endpoint("https://api.example.com/v1", models, "bvg") is None

    def test_list_tags_row_detected_free(self, monkeypatch):
        rows = [
            {"model_name": "prov/list-free", "tags": ["free", "new"], "model_ratio": 0.2},
            _pricing_row("prov/paid", ratio=0.3),
        ]
        monkeypatch.setattr(httpx, "get", _get_factory({"api.example.com": (200, _pricing_payload(rows))}))
        models = [_model("prov/list-free"), _model("prov/paid")]
        split = pipeline._split_free_by_pricing_endpoint("https://api.example.com/v1", models, "bvg")
        assert split is not None
        assert [m["id"] for m in split[0]] == ["prov/list-free"]

    def test_all_free_non_mixed_returns_none(self, monkeypatch):
        rows = [_pricing_row("prov/a", tags="free", ratio=0), _pricing_row("prov/b", tags="free", ratio=0)]
        monkeypatch.setattr(httpx, "get", _get_factory({"api.example.com": (200, _pricing_payload(rows))}))
        models = [_model("prov/a"), _model("prov/b")]
        assert pipeline._split_free_by_pricing_endpoint("https://api.example.com/v1", models, "bvg") is None

    def test_all_paid_non_mixed_returns_none(self, monkeypatch):
        rows = [_pricing_row("prov/a", ratio=0.1), _pricing_row("prov/b", ratio=0.2)]
        monkeypatch.setattr(httpx, "get", _get_factory({"api.example.com": (200, _pricing_payload(rows))}))
        models = [_model("prov/a"), _model("prov/b")]
        assert pipeline._split_free_by_pricing_endpoint("https://api.example.com/v1", models, "bvg") is None


class TestFetchPricingFreeIds:
    def test_ids_extracted_from_model_name(self, monkeypatch):
        rows = [_pricing_row("prov/x", tags="free", ratio=0), _pricing_row("prov/y", ratio=0.5)]
        monkeypatch.setattr(httpx, "get", _get_factory({"api.example.com": (200, _pricing_payload(rows))}))
        ids = pipeline._fetch_pricing_free_ids("https://api.example.com/v1")
        assert ids == {"prov/x"}

    def test_returns_none_on_bad_payload(self, monkeypatch):
        monkeypatch.setattr(httpx, "get", _get_factory({"api.example.com": (200, {"unexpected": True})}))
        assert pipeline._fetch_pricing_free_ids("https://api.example.com/v1") is None
