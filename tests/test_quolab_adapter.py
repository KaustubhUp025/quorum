"""Tests for the quolab semantic-search adapter (GitLabSemanticClient + make_client).

quolab is the OSS replacement for GitLab Ultimate's AI code search. This client wraps a
base GitLab client (glab MCP when available, else REST), routes semantic_code_search to a
quolab service, delegates everything else to the base client, and falls back to REST
lexical search when quolab is unreachable.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from quorum.formatter import post_gate_decision
from quorum.gitlab_client import (
    GitLabGlabMCPClient,
    GitLabRESTClient,
    GitLabSemanticClient,
    make_client,
    quolab_auth_headers,
)
from quorum.models import Finding, ReviewResult, Severity


@pytest.fixture(autouse=True)
def _no_real_id_token(monkeypatch):
    # Never hit the GCP metadata server / ADC during unit tests; default to "no token".
    monkeypatch.setattr("quorum.gitlab_client._fetch_id_token", lambda audience: "")


def _settings(**over):
    base = dict(
        gitlab_url="https://gitlab.com",
        gitlab_token="tok",
        mcp_mode="semantic",
        search_service_url="http://localhost:8080",
        mcp_server_cmd="",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _adapter(inner=None, search_url="http://localhost:8080", api_key=""):
    """Build an adapter over a REST inner+fallback (default) for direct-unit tests."""
    rest = GitLabRESTClient("https://gitlab.com", "tok")
    return GitLabSemanticClient(inner or rest, search_url, rest, api_key=api_key)


def test_make_client_semantic_returns_adapter(monkeypatch):
    # Without glab on PATH the inner client is REST.
    monkeypatch.setattr("quorum.gitlab_client._glab_available", lambda: False)
    c = make_client(_settings())
    assert isinstance(c, GitLabSemanticClient)
    assert isinstance(c._inner, GitLabRESTClient)


def test_make_client_semantic_wraps_glab_when_available(monkeypatch):
    # With glab on PATH the inner client is the glab MCP client — so semantic mode
    # keeps full MCP capability (e.g. pipeline gating) and only search goes to quolab.
    monkeypatch.setattr("quorum.gitlab_client._glab_available", lambda: True)
    c = make_client(_settings(), project_id="group/repo")
    assert isinstance(c, GitLabSemanticClient)
    assert isinstance(c._inner, GitLabGlabMCPClient)
    # the lexical fallback path is always a REST client
    assert isinstance(c._rest, GitLabRESTClient)


def test_make_client_semantic_without_url_falls_back_to_rest():
    c = make_client(_settings(search_service_url=""))
    assert isinstance(c, GitLabRESTClient)


def test_adapter_delegates_non_search_methods_to_inner():
    c = _adapter()
    # a REST-only method is reachable via __getattr__ delegation to the inner client
    assert callable(c.create_workitem_note)
    assert callable(c.get_merge_request_diffs)


async def test_adapter_uses_quolab_when_reachable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/search")
        return httpx.Response(200, json={"formatted": "QUOLAB HIT a.py:1-5"})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    c = _adapter()
    out = await c.semantic_code_search("group/repo", "where is the lock acquired")
    assert "QUOLAB HIT" in out


async def test_adapter_falls_back_to_rest_on_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    c = _adapter()

    async def fake_rest(project_id, query, max_results=5):
        return "REST LEXICAL FALLBACK"

    monkeypatch.setattr(c._rest, "semantic_code_search", fake_rest)
    out = await c.semantic_code_search("group/repo", "q")
    assert out == "REST LEXICAL FALLBACK"


def _review_with_finding():
    f = Finding(
        rule_id="RULE_06",
        rule_name="Fencing Token Missing",
        severity=Severity.CRITICAL,
        confidence=100,
        title="No fencing token",
        explanation="Static lock value used.",
        file_path="src/app.py",
        line_number=10,
    )
    return ReviewResult(mr_iid=1, project_id="owner/repo", findings=[f])


async def test_gate_posts_sarif_and_returns_decision(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/gate")
        seen["body"] = json.loads(request.content)
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"state": "failed", "blocking": 1, "total": 1})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    out = await post_gate_decision(
        "http://localhost:8080", "owner/repo", "abc123", _review_with_finding(),
        api_key="gatekey",
    )
    assert out == {"state": "failed", "blocking": 1, "total": 1}
    # the posted body carries the SARIF Quorum emits, plus project + sha
    assert seen["body"]["project_id"] == "owner/repo"
    assert seen["body"]["sha"] == "abc123"
    assert seen["body"]["sarif"]["version"] == "2.1.0"
    assert seen["key"] == "gatekey"  # api key forwarded to the gate


async def test_adapter_sends_api_key_header(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"formatted": "HIT"})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    c = _adapter(api_key="s3cret")
    out = await c.semantic_code_search("g/r", "q")
    assert out == "HIT"
    assert seen["key"] == "s3cret"


def test_quolab_auth_headers_combines_key_and_token(monkeypatch):
    monkeypatch.setattr("quorum.gitlab_client._fetch_id_token", lambda audience: "idtok")
    h = quolab_auth_headers("https://quolab.example", "k")
    assert h["X-API-Key"] == "k"
    assert h["Authorization"] == "Bearer idtok"


def test_quolab_auth_headers_empty_when_no_key_no_token():
    # autouse fixture makes _fetch_id_token return "" → no headers at all
    assert quolab_auth_headers("https://quolab.example", "") == {}


async def test_gate_never_raises_on_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    out = await post_gate_decision(
        "http://localhost:8080", "owner/repo", "abc", _review_with_finding()
    )
    assert out is None


async def test_fallback_connects_separate_unconnected_rest(monkeypatch):
    # Regression: in glab-wrapping mode the REST fallback is a *separate*, never-
    # connected client. When quolab is down the adapter must open a session on it
    # rather than raise "Not connected — use `async with client.connect()`".
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)  # quolab /search down

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    rest = GitLabRESTClient("https://gitlab.com", "tok")  # never connected (glab is inner)
    assert getattr(rest, "_http", None) is None
    c = GitLabSemanticClient(inner=object(), search_url="http://localhost:8080", rest_fallback=rest)

    opened = {"n": 0}
    real_connect = rest.connect

    def spy_connect():
        opened["n"] += 1
        return real_connect()

    async def fake_search(project_id, query, max_results=5):
        return "REST LEXICAL"

    monkeypatch.setattr(rest, "connect", spy_connect)
    monkeypatch.setattr(rest, "semantic_code_search", fake_search)

    out = await c.semantic_code_search("g/r", "q")
    assert out == "REST LEXICAL"
    assert opened["n"] == 1  # opened a session on demand instead of raising


async def test_adapter_falls_back_on_empty_quolab_result(monkeypatch):
    # quolab reachable but returns no formatted body → still fall back to lexical.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"formatted": ""})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    c = _adapter()

    async def fake_rest(project_id, query, max_results=5):
        return "REST LEXICAL FALLBACK"

    monkeypatch.setattr(c._rest, "semantic_code_search", fake_rest)
    out = await c.semantic_code_search("group/repo", "q")
    assert out == "REST LEXICAL FALLBACK"
