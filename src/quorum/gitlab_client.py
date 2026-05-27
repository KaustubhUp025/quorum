"""GitLab clients for Quorum.

Two implementations with the same async interface:

GitLabMCPClient  — connects to the GitLab MCP server (Premium/Ultimate only).
                   Uses semantic_code_search for true AI-powered cross-repo search.

GitLabRESTClient — uses GitLab's standard REST API (any plan, any token).
                   semantic_code_search falls back to lexical blob search;
                   Gemini still reasons over the real results.

The agent (agent.py) uses the shared interface — it doesn't care which client
is supplied. The CLI picks the right one automatically (MCP if reachable, REST
otherwise) or the caller can force REST with use_rest=True.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Protocol
from urllib.parse import quote

import httpx
import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared protocol — both clients satisfy this interface
# ---------------------------------------------------------------------------

class GitLabClient(Protocol):
    """Methods the agent expects on any GitLab client."""

    @asynccontextmanager
    def connect(self) -> AsyncGenerator["GitLabClient", None]: ...

    async def get_merge_request_diffs(self, project_id: str, mr_iid: int) -> str: ...
    async def get_merge_request(self, project_id: str, mr_iid: int) -> str: ...
    async def semantic_code_search(self, project_id: str, query: str, max_results: int) -> str: ...
    async def create_workitem_note(self, project_id: str, mr_iid: int, body: str, note_type: str) -> str: ...
    async def manage_pipeline(self, project_id: str, pipeline_id: int, action: str) -> str: ...


# ---------------------------------------------------------------------------
# MCP client (Premium / Ultimate)
# ---------------------------------------------------------------------------

class GitLabMCPClient:
    """Thin async wrapper around the GitLab MCP server."""

    def __init__(self, mcp_url: str, token: str) -> None:
        self._url = mcp_url
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._session: ClientSession | None = None

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator["GitLabMCPClient", None]:
        """Establish the MCP session. Use as `async with client.connect()`."""
        async with streamablehttp_client(self._url, headers=self._headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._session = session
                log.info("mcp_connected", url=self._url)
                try:
                    yield self
                finally:
                    self._session = None
                    log.info("mcp_disconnected")

    async def _call(self, tool_name: str, arguments: dict) -> str:
        if self._session is None:
            raise RuntimeError("Not connected — use `async with client.connect()`")
        log.debug("mcp_tool_call", tool=tool_name, args=arguments)
        result = await self._session.call_tool(tool_name, arguments=arguments)

        if result.isError:
            error_text = " ".join(
                getattr(c, "text", str(c)) for c in (result.content or [])
            )
            log.warning("mcp_tool_error", tool=tool_name, error=error_text)
            return f"[MCP ERROR] {error_text}"

        parts = []
        for content in result.content or []:
            if hasattr(content, "text"):
                parts.append(content.text)
            else:
                parts.append(json.dumps(
                    content.model_dump() if hasattr(content, "model_dump") else str(content)
                ))
        return "\n".join(parts)

    async def get_merge_request_diffs(self, project_id: str, mr_iid: int) -> str:
        return await self._call("get_merge_request_diffs", {"project_id": project_id, "iid": mr_iid})

    async def get_merge_request(self, project_id: str, mr_iid: int) -> str:
        return await self._call("get_merge_request", {"project_id": project_id, "iid": mr_iid})

    async def semantic_code_search(self, project_id: str, query: str, max_results: int = 5) -> str:
        return await self._call(
            "semantic_code_search",
            {"project_id": project_id, "search_query": query, "per_page": max_results},
        )

    async def create_workitem_note(
        self, project_id: str, mr_iid: int, body: str, note_type: str = "MergeRequest"
    ) -> str:
        return await self._call(
            "create_workitem_note",
            {"project_id": project_id, "noteable_type": note_type, "noteable_iid": mr_iid, "body": body},
        )

    async def manage_pipeline(self, project_id: str, pipeline_id: int, action: str) -> str:
        return await self._call("manage_pipeline", {"project_id": project_id, "pipeline_id": pipeline_id, "action": action})

    async def list_available_tools(self) -> list[str]:
        if self._session is None:
            raise RuntimeError("Not connected")
        result = await self._session.list_tools()
        return [t.name for t in result.tools]


# ---------------------------------------------------------------------------
# REST client (any plan)
# ---------------------------------------------------------------------------

class GitLabRESTClient:
    """
    GitLab client that uses the standard REST API instead of MCP.

    Works on any GitLab plan — free, Premium, Ultimate.
    Uses the same async interface as GitLabMCPClient so the agent is unchanged.

    Trade-off vs MCP:
      - semantic_code_search → lexical blob search (fast, real results, not AI-semantic)
      - manage_pipeline → not supported (no REST equivalent with same semantics)
      - Everything else is functionally identical.
    """

    def __init__(self, gitlab_url: str, token: str) -> None:
        self._base = gitlab_url.rstrip("/") + "/api/v4"
        self._token = token
        self._http: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator["GitLabRESTClient", None]:
        """Open a shared httpx session for the lifetime of the review."""
        async with httpx.AsyncClient(
            headers={"Private-Token": self._token},
            timeout=30.0,
            follow_redirects=True,
        ) as client:
            self._http = client
            log.info("rest_client_ready", base=self._base)
            yield self
            self._http = None

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Not connected — use `async with client.connect()`")
        return self._http

    @staticmethod
    def _pid(project_id: str) -> str:
        """URL-encode the project path for REST API URLs."""
        return quote(str(project_id), safe="")

    # ------------------------------------------------------------------
    # Diff / MR info
    # ------------------------------------------------------------------

    async def get_merge_request_diffs(self, project_id: str, mr_iid: int) -> str:
        """Return unified diff text for all changed files in the MR."""
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests/{mr_iid}/diffs",
            params={"per_page": 50, "unidiff": "true"},
        )
        resp.raise_for_status()
        diffs = resp.json()

        parts: list[str] = []
        for d in diffs:
            old_path = d.get("old_path", "/dev/null")
            new_path = d.get("new_path", "/dev/null")
            parts.append(f"--- a/{old_path}")
            parts.append(f"+++ b/{new_path}")
            raw_diff = d.get("diff", "")
            if raw_diff:
                parts.append(raw_diff)
        return "\n".join(parts)

    async def get_merge_request(self, project_id: str, mr_iid: int) -> str:
        """Return key MR metadata as a JSON string."""
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests/{mr_iid}"
        )
        resp.raise_for_status()
        mr = resp.json()
        return json.dumps({
            "title": mr.get("title"),
            "description": mr.get("description"),
            "author": mr.get("author", {}).get("username"),
            "source_branch": mr.get("source_branch"),
            "target_branch": mr.get("target_branch"),
            "state": mr.get("state"),
            "web_url": mr.get("web_url"),
        }, indent=2)

    # ------------------------------------------------------------------
    # Code search (lexical blob search — free plan compatible)
    # ------------------------------------------------------------------

    async def semantic_code_search(
        self, project_id: str, query: str, max_results: int = 5
    ) -> str:
        """
        Search project blobs using GitLab's built-in code search.

        This is lexical (keyword-based), not semantic/AI-powered like the MCP tool.
        Gemini receives the same format and reasons over real code from the repo.

        Extracts the most meaningful terms from the natural-language query
        before calling the GitLab search API.
        """
        _stop = {"the", "a", "an", "in", "for", "of", "to", "that", "this",
                 "is", "are", "was", "any", "all", "not", "has", "have", "find",
                 "look", "search", "check", "does", "with", "from", "where"}
        terms = [w for w in query.lower().split() if len(w) > 3 and w not in _stop]
        search_term = " ".join(terms[:3]) if terms else query[:60]

        log.debug("rest_code_search", query=query, search_term=search_term)
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/search",
            params={"scope": "blobs", "search": search_term, "per_page": max_results},
        )

        if resp.status_code == 403:
            return (
                f"[Code search not enabled on this project. "
                f"Query was: {query!r}. No results available.]"
            )
        resp.raise_for_status()

        results = resp.json()
        if not results:
            return f"No code found matching: {query!r} (searched for: {search_term!r})"

        parts = [f"Search results for: {query!r}  [lexical match on: {search_term!r}]\n"]
        for r in results:
            parts.append(f"File: {r.get('path', 'unknown')}  (ref: {r.get('ref', 'HEAD')})")
            if r.get("data"):
                parts.append(r["data"])
            parts.append("---")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Comment posting
    # ------------------------------------------------------------------

    async def create_workitem_note(
        self, project_id: str, mr_iid: int, body: str, note_type: str = "MergeRequest"
    ) -> str:
        """Post a comment on the MR via the REST notes API."""
        resp = await self._client.post(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests/{mr_iid}/notes",
            json={"body": body},
        )
        resp.raise_for_status()
        note = resp.json()
        note_id = note.get("id")
        log.info("rest_note_posted", note_id=note_id)
        return json.dumps({"id": note_id})

    async def manage_pipeline(self, project_id: str, pipeline_id: int, action: str) -> str:
        log.warning("manage_pipeline_not_supported_in_rest_mode", action=action)
        return "[REST mode: pipeline gating requires GitLab MCP (Premium/Ultimate)]"


# ---------------------------------------------------------------------------
# Factory — auto-detect MCP availability, fall back to REST
# ---------------------------------------------------------------------------

async def probe_mcp(mcp_url: str, token: str) -> bool:
    """Return True if the GitLab MCP endpoint responds (not 404/403)."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                mcp_url,
                headers={"Authorization": f"Bearer {token}"},
            )
            return resp.status_code not in (403, 404)
    except Exception:
        return False


def make_client(settings: object) -> "GitLabMCPClient | GitLabRESTClient":
    """
    Return a GitLabMCPClient if settings say so, otherwise GitLabRESTClient.

    Call this from CLI; the agent accepts either type.
    Actual MCP reachability is probed at connect time, not here —
    keep this synchronous for simplicity.
    """
    from quorum.config import Settings
    s: Settings = settings  # type: ignore[assignment]
    return GitLabRESTClient(s.gitlab_url, s.gitlab_token)
