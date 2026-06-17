"""Tests for the quolab semantic-search adapter (GitLabSemanticClient + make_client).

quolab is the OSS replacement for GitLab Ultimate's AI code search. This client routes
semantic_code_search to a quolab service and delegates everything else to REST, with a
fallback to REST lexical search when quolab is unreachable.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from quorum.gitlab_client import (
    GitLabRESTClient,
    GitLabSemanticClient,
    make_client,
)


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


def test_make_client_semantic_returns_adapter():
    c = make_client(_settings())
    assert isinstance(c, GitLabSemanticClient)


def test_make_client_semantic_without_url_falls_back_to_rest():
    c = make_client(_settings(search_service_url=""))
    assert isinstance(c, GitLabRESTClient)


def test_adapter_delegates_non_search_methods_to_rest():
    c = GitLabSemanticClient("https://gitlab.com", "tok", "http://localhost:8080")
    # a REST-only method is reachable via __getattr__ delegation
    assert callable(c.create_workitem_note)
    assert callable(c.get_merge_request_diffs)


async def test_adapter_uses_quolab_when_reachable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/search")
        return httpx.Response(200, json={"formatted": "QUOLAB HIT a.py:1-5"})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    c = GitLabSemanticClient("https://gitlab.com", "tok", "http://localhost:8080")
    out = await c.semantic_code_search("group/repo", "where is the lock acquired")
    assert "QUOLAB HIT" in out


async def test_adapter_falls_back_to_rest_on_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=transport, **kw))

    c = GitLabSemanticClient("https://gitlab.com", "tok", "http://localhost:8080")

    async def fake_rest(project_id, query, max_results=5):
        return "REST LEXICAL FALLBACK"

    monkeypatch.setattr(c._rest, "semantic_code_search", fake_rest)
    out = await c.semantic_code_search("group/repo", "q")
    assert out == "REST LEXICAL FALLBACK"
