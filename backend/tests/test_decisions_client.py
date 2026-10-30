"""Decisions wire contract, offline transport and usage-first failure handling."""
from copy import deepcopy
import asyncio
import json

import httpx
import pytest

from app.core import decisions_client as client


def request():
    return {"model": client.MODEL, "input": "合成公开资料", "questions": [{
        "name": "fact_0", "type": "choice", "instructions": "合成关系核验",
        "choices": [{"value": v, "description": v} for v in client.CHOICES]}]}


def response():
    return {"model": client.MODEL, "answers": [{"name": "fact_0", "type": "choice",
        "choice": "insufficient", "confidence": 0.8,
        "probabilities": [{"value": v, "probability": 0.7 if v == "insufficient" else 0.1} for v in client.CHOICES]}],
        "usage": {"input_tokens": 50, "output_tokens": 0, "total_tokens": 50,
                  "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                  "output_tokens_details": {"reasoning_tokens": 0}}}


@pytest.fixture
def wire(monkeypatch):
    real = httpx.AsyncClient
    calls, configs = [], []
    data = {"status": 200, "body": response()}

    def handle(req):
        calls.append(req)
        return httpx.Response(data["status"], json=data["body"], headers={"location": "https://foreign.invalid"})

    def build(**kwargs):
        configs.append(dict(kwargs))
        kwargs.pop("proxy")
        return real(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(client.httpx, "AsyncClient", build)
    return data, calls, configs


def test_official_tls_no_redirect_no_ambient_proxy_and_real_usage(wire):
    data, calls, configs = wire
    metered = []
    value = client.create_decisions(request(), api_key="synthetic-secret", usage_callback=metered.append)
    assert len(calls) == 1 and str(calls[0].url) == client.ENDPOINT
    assert configs == [{"timeout": 30, "proxy": None, "trust_env": False, "follow_redirects": False, "verify": True}]
    assert json.loads(calls[0].content) == request()
    assert metered == [data["body"]["usage"]] and value["usage"] == metered[0]
    assert len(value["response_sha256"]) == 64 and "synthetic-secret" not in json.dumps(value)


@pytest.mark.parametrize("mutation,code", [
    (lambda v: v["answers"].clear(), "answer_coverage_mismatch"),
    (lambda v: v["answers"][0].update(name="foreign"), "answer_identity_mismatch"),
    (lambda v: v.update(model="another-model"), "model_mismatch"),
    (lambda v: v["answers"][0].update(choice=True), "invalid_answer"),
    (lambda v: v["answers"][0]["probabilities"].pop(), "invalid_probabilities"),
    (lambda v: v["answers"][0]["probabilities"][0].update(probability=0.4), "invalid_probabilities"),
])
def test_answer_failure_preserves_usage_before_rejecting(wire, mutation, code):
    data, calls, _ = wire
    mutation(data["body"])
    known = []
    with pytest.raises(client.DecisionsError, match=code):
        client.create_decisions(request(), api_key="synthetic", usage_callback=known.append)
    assert len(calls) == 1 and known == [response()["usage"]]


@pytest.mark.parametrize("status", [301, 401, 403, 429, 503])
def test_status_never_retries_redirects_or_leaks_body(wire, status):
    data, calls, _ = wire
    data.update(status=status, body={"message": "secret fixture response"})
    with pytest.raises(client.DecisionsError) as caught:
        client.create_decisions(request(), api_key="synthetic-secret")
    assert str(caught.value) == f"Decisions research: http_{status}"
    assert len(calls) == 1


@pytest.mark.parametrize("change", [
    lambda u: u.pop("input_tokens"),
    lambda u: u.update(input_tokens=True),
    lambda u: u.update(total_tokens=0),
    lambda u: u["input_tokens_details"].update(cached_tokens=-1),
])
def test_missing_or_invalid_usage_stays_unknown(wire, change):
    data, calls, _ = wire
    change(data["body"]["usage"])
    known = []
    with pytest.raises(client.DecisionsError, match="usage_unknown"):
        client.create_decisions(request(), api_key="synthetic", usage_callback=known.append)
    assert known == [None] and len(calls) == 1


def test_refusal_is_preserved_as_refusal_not_negative(wire):
    data, _, _ = wire
    data["body"]["answers"] = [{"type": "refusal", "name": "fact_0"}]
    value = client.create_decisions(request(), api_key="synthetic")
    assert value["answers"] == [{"type": "refusal", "name": "fact_0"}]


@pytest.mark.parametrize("proxy", ["http://localhost:7890", "https://proxy.invalid:8443"])
def test_proxy_is_only_an_explicit_argument(wire, proxy):
    client.create_decisions(request(), api_key="synthetic", proxy=proxy)
    assert wire[2][0]["proxy"] == proxy


@pytest.mark.parametrize("kwargs", [{"proxy": "socks5://localhost:9999"}, {"proxy": "http://proxy.invalid/?token=secret"},
    {"timeout": 31}, {"timeout": True}, {"api_key": ""}, {"api_key": "line\nbreak"}])
def test_invalid_connection_rejected_before_network(wire, kwargs):
    with pytest.raises(client.DecisionsError):
        client.create_decisions(request(), **{"api_key": "synthetic", **kwargs})
    assert not wire[1] and not wire[2]


def test_duplicate_names_rejected_before_network(wire):
    value = request()
    value["questions"] += [deepcopy(value["questions"][0])]
    with pytest.raises(client.DecisionsError, match="invalid_request"):
        client.create_decisions(value, api_key="synthetic")
    assert not wire[1]


def test_chunked_body_has_one_wall_deadline_and_is_closed(monkeypatch):
    real, closed = httpx.AsyncClient, []

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b" "
                await asyncio.sleep(0.005)

        async def aclose(self):
            closed.append(True)

    async def handle(req):
        return httpx.Response(200, stream=SlowBody())

    def build(**kwargs):
        kwargs.pop("proxy")
        return real(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(client.httpx, "AsyncClient", build)
    with pytest.raises(client.DecisionsError, match="timeout"):
        client.create_decisions(request(), api_key="synthetic", timeout=0.02)
    assert closed == [True]
