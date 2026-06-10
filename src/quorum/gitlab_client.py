"""GitLab clients for Quorum.

Four implementations, same async interface — pick based on your setup:

GitLabGlabMCPClient  — RECOMMENDED for demos / hackathon submission.
                       Uses `glab mcp serve` (official GitLab CLI) via stdio.
                       191 tools including glab_search_semantic (requires
                       GitLab Duo / Ultimate for AI-semantic results).
                       PAT auth via GITLAB_TOKEN. Requires glab v1.80+.

GitLabZereightClient — Community fallback (@zereight/mcp-gitlab).
                       107 tools, PAT auth, works on any plan/tier.
                       No semantic code search (REST lexical fallback).
                       Requires Node.js + npx.

GitLabMCPClient      — Official GitLab Duo HTTP MCP (/api/v4/mcp).
                       Requires Premium/Ultimate + OAuth token.
                       Has server-side semantic search_code.
                       Kept for reference; OAuth integration pending.

GitLabRESTClient     — Plain GitLab REST API. Any plan, any token.
                       No external binary needed (pure Python/httpx).
                       semantic_code_search falls back to lexical blob search.

The agent accepts any client — it calls the shared interface methods.
The factory (make_client) picks the tier based on flags + availability.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Protocol
from urllib.parse import quote, urlparse

import httpx
import structlog
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client, get_default_environment
from mcp.client.streamable_http import streamablehttp_client

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared protocol — all clients satisfy this interface
# ---------------------------------------------------------------------------

class GitLabClient(Protocol):
    """Methods the agent expects on any GitLab client."""

    @asynccontextmanager
    def connect(self) -> AsyncGenerator["GitLabClient", None]: ...

    async def get_merge_request_diffs(self, project_id: str, mr_iid: int) -> str: ...
    async def get_merge_request(self, project_id: str, mr_iid: int) -> str: ...
    async def get_mr_metadata(self, project_id: str, mr_iid: int) -> dict: ...
    async def semantic_code_search(self, project_id: str, query: str, max_results: int) -> str: ...
    async def create_workitem_note(self, project_id: str, mr_iid: int, body: str, note_type: str) -> str: ...
    async def manage_pipeline(self, project_id: str, pipeline_id: int, action: str) -> str: ...
    async def get_file_contents(self, project_id: str, file_path: str, ref: str) -> str: ...
    async def get_pipeline_job_output(self, project_id: str, job_id: int) -> str: ...
    async def create_merge_request(
        self, project_id: str, source_branch: str, target_branch: str,
        title: str, description: str,
    ) -> str: ...
    async def create_branch(self, project_id: str, branch: str, ref: str) -> str: ...
    async def commit_file(
        self, project_id: str, branch: str, file_path: str, content: str, message: str
    ) -> str: ...
    async def get_mr_pipelines(self, project_id: str, mr_iid: int) -> list[dict]: ...
    async def get_pipeline_jobs(self, project_id: str, pipeline_id: int) -> list[dict]: ...
    async def create_mr_discussion(
        self,
        project_id: str,
        mr_iid: int,
        body: str,
        file_path: str | None = None,
        line_number: int | None = None,
        diff_refs: dict | None = None,
    ) -> str: ...
    async def list_mr_notes(self, project_id: str, mr_iid: int) -> list[dict]: ...
    async def get_project_languages(self, project_id: str) -> dict[str, float]: ...
    async def get_project_permissions(self, project_id: str) -> dict: ...
    async def apply_mr_labels(self, project_id: str, mr_iid: int, labels: list[str]) -> None: ...
    async def get_file_contributors(self, project_id: str, file_path: str) -> list[str]: ...


# ---------------------------------------------------------------------------
# Official glab MCP client — glab mcp serve (RECOMMENDED)
# ---------------------------------------------------------------------------

class GitLabGlabMCPClient:
    """
    MCP client using the official GitLab CLI (`glab mcp serve`) via stdio.

    Provides 191 tools including `glab_search_semantic` which performs
    AI-powered semantic code search when the project has GitLab Duo enabled
    (requires Ultimate tier or trial).

    Authentication: GITLAB_TOKEN environment variable (PAT with `api` scope).
    Requires: glab v1.80+ installed on the system PATH.

    How it works:
    - A temporary git directory is created with the target project as `origin`.
    - glab reads this remote to know which GitLab instance and project to use.
    - The temp dir is cleaned up when the context manager exits.
    """

    def __init__(self, gitlab_url: str, token: str, project_id: str) -> None:
        self._url = gitlab_url.rstrip("/")
        self._token = token
        self._project_id = project_id        # e.g. "quorum-hackathon/quorum-demo"
        self._session: ClientSession | None = None
        self._available_tools: set[str] = set()
        self._tmpdir: str | None = None
        self._rest: GitLabRESTClient | None = None

    def _make_git_context(self) -> str:
        """Create a temp dir with a git remote pointing at the target project."""
        host = urlparse(self._url).hostname or "gitlab.com"
        remote_url = f"https://{host}/{self._project_id}.git"
        tmpdir = tempfile.mkdtemp(prefix="quorum-glab-")
        subprocess.run(["git", "init", tmpdir], capture_output=True, check=False)
        subprocess.run(
            ["git", "-C", tmpdir, "remote", "add", "origin", remote_url],
            capture_output=True, check=False,
        )
        return tmpdir

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator["GitLabGlabMCPClient", None]:
        """Start `glab mcp serve` as a subprocess and initialise the MCP session."""
        import asyncio
        # Run the blocking git subprocess calls on a thread pool to avoid stalling
        # the asyncio event loop (critical on Cloud Run with concurrent requests).
        self._tmpdir = await asyncio.to_thread(self._make_git_context)
        env = {
            **get_default_environment(),
            "GITLAB_TOKEN": self._token,
        }
        params = StdioServerParameters(
            command="glab",
            args=["mcp", "serve"],
            env=env,
            cwd=self._tmpdir,
        )
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    self._session = session
                    tools_result = await session.list_tools()
                    self._available_tools = {t.name for t in tools_result.tools}
                    log.info(
                        "glab_mcp_connected",
                        tool_count=len(self._available_tools),
                        project_id=self._project_id,
                    )
                    self._rest = GitLabRESTClient(self._url, self._token)
                    async with self._rest.connect():
                        try:
                            yield self
                        finally:
                            self._session = None
                            self._rest = None
                            self._available_tools = set()
        finally:
            if self._tmpdir:
                shutil.rmtree(self._tmpdir, ignore_errors=True)
                self._tmpdir = None

    async def _call(self, tool_name: str, args: list | None = None,
                    flags: dict | None = None, limit: int = 2000) -> str:
        if self._session is None:
            raise RuntimeError("Not connected — use `async with client.connect()`")
        log.debug("glab_tool_call", tool=tool_name, flags=flags)
        arguments: dict[str, Any] = {"limit": limit}
        if args is not None:
            arguments["args"] = args
        if flags:
            arguments["flags"] = flags
        result = await self._session.call_tool(tool_name, arguments=arguments)
        if result.isError:
            error_text = " ".join(
                getattr(c, "text", str(c)) for c in (result.content or [])
            )
            log.warning("glab_tool_error", tool=tool_name, error=error_text[:200])
            return f"[GLAB ERROR] {error_text}"
        parts: list[str] = []
        for content in result.content or []:
            if hasattr(content, "text"):
                parts.append(content.text)
            else:
                parts.append(str(content))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Interface implementation
    # ------------------------------------------------------------------

    async def get_merge_request_diffs(self, project_id: str, mr_iid: int) -> str:
        if "glab_mr_diff" in self._available_tools:
            # Use a high limit — full diffs can easily exceed 10k chars
            return await self._call("glab_mr_diff",
                args=[str(mr_iid)], flags={"color": "never"}, limit=50000)
        assert self._rest is not None
        return await self._rest.get_merge_request_diffs(project_id, mr_iid)

    async def get_merge_request(self, project_id: str, mr_iid: int) -> str:
        if "glab_mr_view" in self._available_tools:
            # limit is the glab MCP output CHAR cap (default 2000) — MR title +
            # description + notes routinely exceed that, so raise it.
            return await self._call("glab_mr_view", args=[str(mr_iid)], limit=8000)
        assert self._rest is not None
        return await self._rest.get_merge_request(project_id, mr_iid)

    # Phrases returned by GitLab's AI layer when semantic search isn't ready.
    # These come back as successful MCP responses (no [GLAB ERROR] prefix) but are
    # useless as code evidence — fall through to lexical REST search instead.
    _SEMANTIC_ERROR_PHRASES = (
        "no embeddings",
        "indexing started",
        "indexing in progress",
        "not available",
        "feature not available",
        "semantic search is disabled",
    )

    async def semantic_code_search(
        self, project_id: str, query: str, max_results: int = 5
    ) -> str:
        if "glab_search_semantic" in self._available_tools:
            # flags["limit"] = result COUNT; the _call limit= is the output CHAR
            # cap (default 2000). Multi-file snippets need far more room, else the
            # agent only ever sees the first ~2 KB of evidence.
            result = await self._call(
                "glab_search_semantic",
                flags={"query": query, "limit": max_results},
                limit=12000,
            )
            result_lower = result.lower()
            is_error = "[GLAB ERROR]" in result or any(
                phrase in result_lower for phrase in self._SEMANTIC_ERROR_PHRASES
            )
            if not is_error:
                return result
            log.warning("glab_semantic_search_failed_falling_back", error=result[:200])
        assert self._rest is not None
        return await self._rest.semantic_code_search(project_id, query, max_results)

    async def create_workitem_note(
        self, project_id: str, mr_iid: int, body: str, note_type: str = "MergeRequest"
    ) -> str:
        if "glab_mr_note_create" in self._available_tools:
            result = await self._call("glab_mr_note_create",
                args=[str(mr_iid)], flags={"message": body})
            if "[GLAB ERROR]" not in result:
                log.info("glab_note_posted", url=result.strip())
                return result
        assert self._rest is not None
        return await self._rest.create_workitem_note(project_id, mr_iid, body, note_type)

    async def manage_pipeline(
        self, project_id: str, pipeline_id: int, action: str
    ) -> str:
        assert self._rest is not None
        return await self._rest.manage_pipeline(project_id, pipeline_id, action)

    async def get_file_contents(
        self, project_id: str, file_path: str, ref: str = "HEAD"
    ) -> str:
        assert self._rest is not None
        return await self._rest.get_file_contents(project_id, file_path, ref)

    async def get_pipeline_job_output(self, project_id: str, job_id: int) -> str:
        if "glab_ci_trace" in self._available_tools:
            result = await self._call("glab_ci_trace", args=[str(job_id)])
            if "[GLAB ERROR]" not in result:
                return result
        assert self._rest is not None
        return await self._rest.get_pipeline_job_output(project_id, job_id)

    async def create_merge_request(
        self, project_id: str, source_branch: str, target_branch: str,
        title: str, description: str = "",
    ) -> str:
        if "glab_mr_create" in self._available_tools:
            result = await self._call(
                "glab_mr_create",
                flags={
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                    "title": title,
                    "description": description,
                    "yes": True,
                },
            )
            if "[GLAB ERROR]" not in result:
                return result
        assert self._rest is not None
        return await self._rest.create_merge_request(
            project_id, source_branch, target_branch, title, description
        )

    async def get_mr_metadata(self, project_id: str, mr_iid: int) -> dict:
        assert self._rest is not None
        return await self._rest.get_mr_metadata(project_id, mr_iid)

    async def create_mr_discussion(
        self,
        project_id: str,
        mr_iid: int,
        body: str,
        file_path: str | None = None,
        line_number: int | None = None,
        diff_refs: dict | None = None,
    ) -> str:
        assert self._rest is not None
        return await self._rest.create_mr_discussion(
            project_id, mr_iid, body, file_path, line_number, diff_refs
        )

    async def create_branch(self, project_id: str, branch: str, ref: str) -> str:
        assert self._rest is not None
        return await self._rest.create_branch(project_id, branch, ref)

    async def commit_file(
        self, project_id: str, branch: str, file_path: str, content: str, message: str
    ) -> str:
        assert self._rest is not None
        return await self._rest.commit_file(project_id, branch, file_path, content, message)

    async def get_mr_pipelines(self, project_id: str, mr_iid: int) -> list[dict]:
        assert self._rest is not None
        return await self._rest.get_mr_pipelines(project_id, mr_iid)

    async def get_pipeline_jobs(self, project_id: str, pipeline_id: int) -> list[dict]:
        assert self._rest is not None
        return await self._rest.get_pipeline_jobs(project_id, pipeline_id)

    async def list_mr_notes(self, project_id: str, mr_iid: int) -> list[dict]:
        assert self._rest is not None
        return await self._rest.list_mr_notes(project_id, mr_iid)

    async def get_project_languages(self, project_id: str) -> dict[str, float]:
        assert self._rest is not None
        return await self._rest.get_project_languages(project_id)

    async def get_project_permissions(self, project_id: str) -> dict:
        assert self._rest is not None
        return await self._rest.get_project_permissions(project_id)

    async def apply_mr_labels(self, project_id: str, mr_iid: int, labels: list[str]) -> None:
        assert self._rest is not None
        await self._rest.apply_mr_labels(project_id, mr_iid, labels)

    async def get_file_contributors(self, project_id: str, file_path: str) -> list[str]:
        assert self._rest is not None
        return await self._rest.get_file_contributors(project_id, file_path)

    async def list_available_tools(self) -> list[str]:
        return sorted(self._available_tools)


# ---------------------------------------------------------------------------
# Community MCP client — @zereight/mcp-gitlab (fallback, 107 tools)
# ---------------------------------------------------------------------------

class GitLabYodaMCPClient:
    """
    Community MCP client using @zereight/mcp-gitlab (107 tools, PAT auth).

    Fallback for contributors/CI environments where glab is not installed.
    Launched as a child process via stdio transport — requires Node.js + npx.
    Authentication uses GITLAB_PERSONAL_ACCESS_TOKEN env var.

    Does NOT have semantic code search — falls back to REST lexical search.
    For full semantic search use GitLabGlabMCPClient (requires glab + Ultimate).

    Tool names are discovered at connect-time and resolved via alias list.
    Any unresolved operation falls back to the REST client.
    """

    # Priority-ordered candidate names for each logical operation.
    # Ordered candidate names for each logical operation.
    # Verified against @zereight/mcp-gitlab v2.1.16 (107 tools).
    _ALIASES: dict[str, list[str]] = {
        "get_merge_request_diffs": [
            "get_merge_request_diffs",       # @zereight ✅
            "list_merge_request_diffs",      # @zereight (alternate)
            "get_merge_request_diff",
        ],
        "get_merge_request": [
            "get_merge_request",             # @zereight ✅
            "show_merge_request",
        ],
        "semantic_code_search": [
            # @zereight has no code-search tool; falls back to REST lexical search
            "search_code",
            "search_blobs",
            "semantic_code_search",
            "search_project_code",
        ],
        "create_workitem_note": [
            "create_merge_request_note",     # @zereight ✅
            "create_note",                   # @zereight ✅ (generic)
            "add_note_to_merge_request",
            "create_workitem_note",
        ],
        "manage_pipeline": [
            "manage_pipeline",
            "cancel_pipeline",
            "retry_pipeline",
        ],
        "get_file_contents": [
            "get_file_contents",             # @zereight ✅
            "get_file",
            "get_repository_file",
            "show_file",
            "read_file",
        ],
        "get_pipeline_job_output": [
            # @zereight has no job-log tool; falls back to REST
            "get_job_log",
            "get_job_trace",
            "get_pipeline_job_output",
        ],
        "create_merge_request": [
            "create_merge_request",          # @zereight ✅
            "open_merge_request",
        ],
    }

    def __init__(
        self,
        gitlab_url: str,
        token: str,
        server_cmd: list[str] | None = None,
    ) -> None:
        self._url = gitlab_url.rstrip("/")
        self._token = token
        self._server_cmd = server_cmd or ["npx", "--yes", "@zereight/mcp-gitlab"]
        self._session: ClientSession | None = None
        self._available_tools: set[str] = set()
        # Resolved tool name cache: operation → actual tool name (or None)
        self._tool_cache: dict[str, str | None] = {}
        # REST fallback for operations with no matching MCP tool
        self._rest: GitLabRESTClient | None = None

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator["GitLabYodaMCPClient", None]:
        """Launch the MCP server subprocess and initialise the session."""
        env = {
            **get_default_environment(),
            "GITLAB_PERSONAL_ACCESS_TOKEN": self._token,
            "GITLAB_URL": self._url,
        }
        params = StdioServerParameters(
            command=self._server_cmd[0],
            args=self._server_cmd[1:],
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._session = session

                # Discover available tools
                tools_result = await session.list_tools()
                self._available_tools = {t.name for t in tools_result.tools}
                self._tool_cache.clear()

                log.info(
                    "mcp_yoda_connected",
                    tool_count=len(self._available_tools),
                )

                # Prepare REST fallback for any gaps
                self._rest = GitLabRESTClient(self._url, self._token)

                async with self._rest.connect():
                    try:
                        yield self
                    finally:
                        self._session = None
                        self._available_tools = set()
                        self._tool_cache.clear()
                        self._rest = None

    def _resolve(self, operation: str) -> str | None:
        """Return the first matching tool name for the operation, or None."""
        if operation in self._tool_cache:
            return self._tool_cache[operation]
        for candidate in self._ALIASES.get(operation, []):
            if candidate in self._available_tools:
                self._tool_cache[operation] = candidate
                return candidate
        self._tool_cache[operation] = None
        log.warning(
            "mcp_tool_unresolved",
            operation=operation,
            tried=self._ALIASES.get(operation, []),
        )
        return None

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> str:
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

        parts: list[str] = []
        for content in result.content or []:
            if hasattr(content, "text"):
                parts.append(content.text)
            else:
                parts.append(
                    json.dumps(
                        content.model_dump()
                        if hasattr(content, "model_dump")
                        else str(content)
                    )
                )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Interface implementation
    # ------------------------------------------------------------------

    async def get_merge_request_diffs(self, project_id: str, mr_iid: int) -> str:
        tool = self._resolve("get_merge_request_diffs")
        if tool:
            return await self._call(
                tool, {"project_id": project_id, "merge_request_iid": str(mr_iid)}
            )
        assert self._rest is not None
        return await self._rest.get_merge_request_diffs(project_id, mr_iid)

    async def get_merge_request(self, project_id: str, mr_iid: int) -> str:
        tool = self._resolve("get_merge_request")
        if tool:
            return await self._call(
                tool, {"project_id": project_id, "merge_request_iid": str(mr_iid)}
            )
        assert self._rest is not None
        return await self._rest.get_merge_request(project_id, mr_iid)

    async def semantic_code_search(
        self, project_id: str, query: str, max_results: int = 5
    ) -> str:
        tool = self._resolve("semantic_code_search")
        if tool:
            return await self._call(
                tool,
                {"project_id": project_id, "search": query, "per_page": max_results},
            )
        # No code-search tool in @zereight/mcp-gitlab — fall back to REST lexical search
        assert self._rest is not None
        return await self._rest.semantic_code_search(project_id, query, max_results)

    async def create_workitem_note(
        self, project_id: str, mr_iid: int, body: str, note_type: str = "MergeRequest"
    ) -> str:
        tool = self._resolve("create_workitem_note")
        if tool:
            return await self._call(
                tool,
                {
                    "project_id": project_id,
                    "merge_request_iid": str(mr_iid),
                    "body": body,
                },
            )
        assert self._rest is not None
        return await self._rest.create_workitem_note(project_id, mr_iid, body, note_type)

    async def manage_pipeline(
        self, project_id: str, pipeline_id: int, action: str
    ) -> str:
        tool = self._resolve("manage_pipeline")
        if tool:
            return await self._call(
                tool,
                {"project_id": project_id, "pipeline_id": pipeline_id, "action": action},
            )
        assert self._rest is not None
        return await self._rest.manage_pipeline(project_id, pipeline_id, action)

    async def get_file_contents(
        self, project_id: str, file_path: str, ref: str = "HEAD"
    ) -> str:
        tool = self._resolve("get_file_contents")
        if tool:
            return await self._call(
                tool,
                {"project_id": project_id, "file_path": file_path, "ref": ref},
            )
        assert self._rest is not None
        return await self._rest.get_file_contents(project_id, file_path, ref)

    async def get_pipeline_job_output(self, project_id: str, job_id: int) -> str:
        tool = self._resolve("get_pipeline_job_output")
        if tool:
            return await self._call(
                tool, {"project_id": project_id, "job_id": job_id}
            )
        assert self._rest is not None
        return await self._rest.get_pipeline_job_output(project_id, job_id)

    async def create_merge_request(
        self,
        project_id: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> str:
        tool = self._resolve("create_merge_request")
        if tool:
            return await self._call(
                tool,
                {
                    "project_id": project_id,
                    "source_branch": source_branch,
                    "target_branch": target_branch,
                    "title": title,
                    "description": description,
                },
            )
        assert self._rest is not None
        return await self._rest.create_merge_request(
            project_id, source_branch, target_branch, title, description
        )

    async def get_mr_metadata(self, project_id: str, mr_iid: int) -> dict:
        assert self._rest is not None
        return await self._rest.get_mr_metadata(project_id, mr_iid)

    async def create_mr_discussion(
        self,
        project_id: str,
        mr_iid: int,
        body: str,
        file_path: str | None = None,
        line_number: int | None = None,
        diff_refs: dict | None = None,
    ) -> str:
        assert self._rest is not None
        return await self._rest.create_mr_discussion(
            project_id, mr_iid, body, file_path, line_number, diff_refs
        )

    async def create_branch(self, project_id: str, branch: str, ref: str) -> str:
        assert self._rest is not None
        return await self._rest.create_branch(project_id, branch, ref)

    async def commit_file(
        self, project_id: str, branch: str, file_path: str, content: str, message: str
    ) -> str:
        assert self._rest is not None
        return await self._rest.commit_file(project_id, branch, file_path, content, message)

    async def get_mr_pipelines(self, project_id: str, mr_iid: int) -> list[dict]:
        assert self._rest is not None
        return await self._rest.get_mr_pipelines(project_id, mr_iid)

    async def get_pipeline_jobs(self, project_id: str, pipeline_id: int) -> list[dict]:
        assert self._rest is not None
        return await self._rest.get_pipeline_jobs(project_id, pipeline_id)

    async def list_mr_notes(self, project_id: str, mr_iid: int) -> list[dict]:
        assert self._rest is not None
        return await self._rest.list_mr_notes(project_id, mr_iid)

    async def get_project_languages(self, project_id: str) -> dict[str, float]:
        assert self._rest is not None
        return await self._rest.get_project_languages(project_id)

    async def get_project_permissions(self, project_id: str) -> dict:
        assert self._rest is not None
        return await self._rest.get_project_permissions(project_id)

    async def apply_mr_labels(self, project_id: str, mr_iid: int, labels: list[str]) -> None:
        assert self._rest is not None
        await self._rest.apply_mr_labels(project_id, mr_iid, labels)

    async def get_file_contributors(self, project_id: str, file_path: str) -> list[str]:
        assert self._rest is not None
        return await self._rest.get_file_contributors(project_id, file_path)

    async def list_available_tools(self) -> list[str]:
        return sorted(self._available_tools)


# ---------------------------------------------------------------------------
# Official MCP client (Premium / Ultimate — kept for reference)
# ---------------------------------------------------------------------------

class GitLabMCPClient:
    """Thin async wrapper around the official GitLab MCP server.

    Requires GitLab Premium/Ultimate and OAuth authentication.
    Use GitLabYodaMCPClient instead for PAT-based access.
    """

    def __init__(self, mcp_url: str, token: str) -> None:
        self._url = mcp_url
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._session: ClientSession | None = None

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator["GitLabMCPClient", None]:
        async with streamablehttp_client(self._url, headers=self._headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                self._session = session
                log.info("mcp_official_connected", url=self._url)
                try:
                    yield self
                finally:
                    self._session = None

    async def _call(self, tool_name: str, arguments: dict) -> str:
        if self._session is None:
            raise RuntimeError("Not connected — use `async with client.connect()`")
        result = await self._session.call_tool(tool_name, arguments=arguments)
        if result.isError:
            error_text = " ".join(getattr(c, "text", str(c)) for c in (result.content or []))
            return f"[MCP ERROR] {error_text}"
        parts = []
        for content in result.content or []:
            if hasattr(content, "text"):
                parts.append(content.text)
            else:
                parts.append(json.dumps(content.model_dump() if hasattr(content, "model_dump") else str(content)))
        return "\n".join(parts)

    async def get_merge_request_diffs(self, project_id: str, mr_iid: int) -> str:
        return await self._call("get_merge_request_diffs", {"project_id": project_id, "iid": mr_iid})

    async def get_merge_request(self, project_id: str, mr_iid: int) -> str:
        return await self._call("get_merge_request", {"project_id": project_id, "iid": mr_iid})

    async def semantic_code_search(self, project_id: str, query: str, max_results: int = 5) -> str:
        return await self._call("search_code", {"project_id": project_id, "search": query, "per_page": max_results})

    async def create_workitem_note(self, project_id: str, mr_iid: int, body: str, note_type: str = "MergeRequest") -> str:
        return await self._call("create_merge_request_note", {"project_id": project_id, "merge_request_iid": mr_iid, "body": body})

    async def manage_pipeline(self, project_id: str, pipeline_id: int, action: str) -> str:
        return await self._call("manage_pipeline", {"project_id": project_id, "pipeline_id": pipeline_id, "action": action})

    async def get_file_contents(self, project_id: str, file_path: str, ref: str = "HEAD") -> str:
        return await self._call("get_file", {"project_id": project_id, "file_path": file_path, "ref": ref})

    async def get_pipeline_job_output(self, project_id: str, job_id: int) -> str:
        return await self._call("get_job_log", {"project_id": project_id, "job_id": job_id})

    async def create_merge_request(self, project_id: str, source_branch: str, target_branch: str, title: str, description: str = "") -> str:
        return await self._call("create_merge_request", {"project_id": project_id, "source_branch": source_branch, "target_branch": target_branch, "title": title, "description": description})


# ---------------------------------------------------------------------------
# REST client (any plan)
# ---------------------------------------------------------------------------

class GitLabRESTClient:
    """
    GitLab client that uses the standard REST API instead of MCP.

    Works on any GitLab plan — free, Premium, Ultimate.
    Uses the same async interface as the MCP clients so the agent is unchanged.

    Trade-offs vs MCP:
      - semantic_code_search → lexical blob search (not AI-semantic)
      - manage_pipeline → not supported via REST (no identical semantics)
      - Everything else is functionally equivalent.
    """

    def __init__(self, gitlab_url: str, token: str) -> None:
        self._base = gitlab_url.rstrip("/") + "/api/v4"
        self._token = token
        self._http: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator["GitLabRESTClient", None]:
        async with httpx.AsyncClient(
            headers={"Private-Token": self._token},
            timeout=30.0,
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
        return quote(str(project_id), safe="")

    # ------------------------------------------------------------------
    # Diff / MR info
    # ------------------------------------------------------------------

    async def get_merge_request_diffs(self, project_id: str, mr_iid: int) -> str:
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
    # Code search (lexical — free plan compatible)
    # ------------------------------------------------------------------

    async def semantic_code_search(
        self, project_id: str, query: str, max_results: int = 5
    ) -> str:
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
            return f"[Code search not enabled. Query: {query!r}. No results.]"
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
        resp = await self._client.post(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests/{mr_iid}/notes",
            json={"body": body},
        )
        resp.raise_for_status()
        note = resp.json()
        note_id = note.get("id")
        log.info("rest_note_posted", note_id=note_id)
        return json.dumps({"id": note_id})

    async def create_mr_discussion(
        self,
        project_id: str,
        mr_iid: int,
        body: str,
        file_path: str | None = None,
        line_number: int | None = None,
        diff_refs: dict | None = None,
    ) -> str:
        """Create a threaded MR discussion. Posts an inline comment when file_path + line_number
        + diff_refs are provided; falls back to a top-level note on 422 (line not in diff)."""
        payload: dict = {"body": body}
        if file_path and line_number and diff_refs:
            payload["position"] = {
                "base_sha": diff_refs.get("base_sha", ""),
                "head_sha": diff_refs.get("head_sha", ""),
                "start_sha": diff_refs.get("start_sha", ""),
                "position_type": "text",
                "new_path": file_path,
                "new_line": line_number,
            }
        resp = await self._client.post(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests/{mr_iid}/discussions",
            json=payload,
        )
        if resp.status_code == 422 and "position" in payload:
            log.warning("inline_comment_422_fallback", file=file_path, line=line_number)
            return await self.create_workitem_note(project_id, mr_iid, body)
        resp.raise_for_status()
        data = resp.json()
        disc_id = data.get("id", "")
        log.info("rest_discussion_posted", discussion_id=disc_id, inline=bool("position" in payload))
        return json.dumps({"id": disc_id})

    async def manage_pipeline(self, project_id: str, pipeline_id: int, action: str) -> str:
        log.warning("manage_pipeline_not_supported_in_rest_mode", action=action)
        return "[REST mode: pipeline gating requires GitLab MCP]"

    # ------------------------------------------------------------------
    # File contents
    # ------------------------------------------------------------------

    async def get_file_contents(
        self, project_id: str, file_path: str, ref: str = "HEAD"
    ) -> str:
        encoded_path = quote(file_path, safe="")
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/repository/files/{encoded_path}",
            params={"ref": ref},
        )
        if resp.status_code == 404:
            return f"[File not found: {file_path} at ref={ref}]"
        resp.raise_for_status()
        data = resp.json()
        import base64
        content_b64 = data.get("content", "")
        try:
            return base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception:
            return content_b64

    # ------------------------------------------------------------------
    # CI job log
    # ------------------------------------------------------------------

    async def get_pipeline_job_output(self, project_id: str, job_id: int) -> str:
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/jobs/{job_id}/trace"
        )
        if resp.status_code == 404:
            return f"[Job log not found for job_id={job_id}]"
        resp.raise_for_status()
        log_text = resp.text
        # Truncate to last 8k chars to avoid flooding the context
        if len(log_text) > 8000:
            log_text = f"[...truncated — last 8000 chars shown...]\n{log_text[-8000:]}"
        return log_text

    # ------------------------------------------------------------------
    # Create merge request
    # ------------------------------------------------------------------

    async def create_merge_request(
        self,
        project_id: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> str:
        resp = await self._client.post(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests",
            json={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
                "remove_source_branch": False,
            },
        )
        resp.raise_for_status()
        mr = resp.json()
        log.info("rest_mr_created", mr_iid=mr.get("iid"), url=mr.get("web_url"))
        return json.dumps({
            "iid": mr.get("iid"),
            "title": mr.get("title"),
            "web_url": mr.get("web_url"),
            "state": mr.get("state"),
        }, indent=2)

    # ------------------------------------------------------------------
    # MR metadata (structured dict for internal use)
    # ------------------------------------------------------------------

    async def get_mr_metadata(self, project_id: str, mr_iid: int) -> dict:
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests/{mr_iid}"
        )
        resp.raise_for_status()
        mr = resp.json()
        diff_refs = mr.get("diff_refs") or {}
        return {
            "source_branch": mr.get("source_branch", ""),
            "target_branch": mr.get("target_branch", "main"),
            "web_url": mr.get("web_url", ""),
            "title": mr.get("title", ""),
            "state": mr.get("state", ""),
            "author": mr.get("author", {}).get("username", ""),
            "base_sha": diff_refs.get("base_sha", ""),
            "head_sha": diff_refs.get("head_sha", ""),
            "start_sha": diff_refs.get("start_sha", ""),
        }

    # ------------------------------------------------------------------
    # Branch and file operations (for fix MR creation)
    # ------------------------------------------------------------------

    async def create_branch(self, project_id: str, branch: str, ref: str) -> str:
        resp = await self._client.post(
            f"{self._base}/projects/{self._pid(project_id)}/repository/branches",
            json={"branch": branch, "ref": ref},
        )
        resp.raise_for_status()
        data = resp.json()
        log.info("rest_branch_created", branch=data.get("name"))
        return json.dumps({"name": data.get("name"), "commit": data.get("commit", {}).get("id")})

    async def commit_file(
        self, project_id: str, branch: str, file_path: str, content: str, message: str
    ) -> str:
        # Determine whether to create or update: GitLab's commits API requires the
        # correct action — "update" fails with 400 if the file doesn't exist yet.
        encoded_path = quote(file_path, safe="")
        check = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/repository/files/{encoded_path}",
            params={"ref": branch},
        )
        action = "create" if check.status_code == 404 else "update"

        resp = await self._client.post(
            f"{self._base}/projects/{self._pid(project_id)}/repository/commits",
            json={
                "branch": branch,
                "commit_message": message,
                "actions": [{"action": action, "file_path": file_path, "content": content}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        log.info("rest_file_committed", short_id=data.get("short_id"), file=file_path, action=action)
        return json.dumps({"id": data.get("id"), "short_id": data.get("short_id")})

    # ------------------------------------------------------------------
    # Pipeline status (for CI failure correlation)
    # ------------------------------------------------------------------

    async def get_mr_pipelines(self, project_id: str, mr_iid: int) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests/{mr_iid}/pipelines",
            params={"per_page": 5},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_pipeline_jobs(self, project_id: str, pipeline_id: int) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/pipelines/{pipeline_id}/jobs",
            params={"scope[]": "failed", "per_page": 5},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Issue filing
    # ------------------------------------------------------------------

    async def check_repo_metadata(self, project_id: str) -> dict:
        """Return key project flags: issues_enabled, visibility, default_branch."""
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}"
        )
        if resp.status_code == 404:
            return {"has_issues": False, "error": "project_not_found"}
        resp.raise_for_status()
        p = resp.json()
        return {
            "has_issues": p.get("issues_enabled", False),
            "has_discussions": False,  # GitLab uses issues for discussions
            "is_fork": p.get("forked_from_project") is not None,
            "visibility": p.get("visibility", "public"),
            "default_branch": p.get("default_branch", "main"),
        }

    async def get_project_permissions(self, project_id: str) -> dict:
        """Return the caller's access level on the project.

        Reads ``permissions.project_access`` / ``permissions.group_access`` from
        ``GET /projects/:id``. ``can_write`` is True at Developer (30) or above —
        the minimum needed to post MR comments reliably and to push fix branches.
        Used as a pre-flight check so reviews on read-only repos fall back to a
        local report instead of crashing on a 403.
        """
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}"
        )
        if resp.status_code in (401, 403, 404):
            return {"access_level": 0, "can_write": False, "error": f"http_{resp.status_code}"}
        resp.raise_for_status()
        perms = (resp.json().get("permissions") or {})
        levels = [
            (perms.get(k) or {}).get("access_level", 0) or 0
            for k in ("project_access", "group_access")
        ]
        level = max(levels) if levels else 0
        return {"access_level": level, "can_write": level >= 30}

    async def create_issue(self, project_id: str, title: str, body: str, labels: list[str] | None = None) -> dict:
        """Create a GitLab issue. Returns {'number': N, 'url': '...', 'blocked': False}."""
        payload: dict = {"title": title, "description": body}
        if labels:
            payload["labels"] = ",".join(labels)
        resp = await self._client.post(
            f"{self._base}/projects/{self._pid(project_id)}/issues",
            json=payload,
        )
        if resp.status_code == 403:
            return {"blocked": True, "reason": "issues_disabled_or_insufficient_permissions", "url": None, "number": None}
        if resp.status_code == 404:
            return {"blocked": True, "reason": "project_not_found", "url": None, "number": None}
        resp.raise_for_status()
        issue = resp.json()
        iid = issue.get("iid")
        url = issue.get("web_url")
        log.info("gitlab_issue_created", iid=iid, url=url)
        return {"blocked": False, "number": iid, "url": url}

    # ------------------------------------------------------------------
    # Phase C — MCP tool usage improvements
    # ------------------------------------------------------------------

    async def list_mr_notes(self, project_id: str, mr_iid: int) -> list[dict]:
        """Return top-level notes on an MR (for duplicate-review detection)."""
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests/{mr_iid}/notes",
            params={"per_page": 100, "sort": "desc"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_project_languages(self, project_id: str) -> dict[str, float]:
        """Return language percentages, e.g. {'Python': 68.3, 'JavaScript': 31.7}."""
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/languages"
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        # GitLab returns percentages directly: {"Python": 68.34, "JavaScript": 31.66}
        raw = resp.json()
        return {lang: float(pct) for lang, pct in raw.items()} if raw else {}

    async def apply_mr_labels(self, project_id: str, mr_iid: int, labels: list[str]) -> None:
        """Additively apply labels to an MR (existing labels are preserved)."""
        # Fetch current labels first to avoid overwriting
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests/{mr_iid}"
        )
        resp.raise_for_status()
        existing = resp.json().get("labels", [])
        combined = list(set(existing) | set(labels))
        put = await self._client.put(
            f"{self._base}/projects/{self._pid(project_id)}/merge_requests/{mr_iid}",
            json={"labels": ",".join(combined)},
        )
        if put.status_code in (200, 201):
            log.info("mr_labels_applied", labels=combined)
        else:
            log.warning("mr_labels_failed", status=put.status_code)

    async def get_file_contributors(self, project_id: str, file_path: str) -> list[str]:
        """Return usernames of the last 5 committers to file_path (most recent first)."""
        resp = await self._client.get(
            f"{self._base}/projects/{self._pid(project_id)}/repository/commits",
            params={"path": file_path, "per_page": 5},
        )
        if resp.status_code in (404, 400):
            return []
        resp.raise_for_status()
        commits = resp.json()
        seen: list[str] = []
        for c in commits:
            # Prefer committer_name then author_name; skip bots / CI accounts
            username = (
                (c.get("author", {}) or {}).get("username")
                or c.get("committer_name")
                or c.get("author_name")
                or ""
            )
            username = username.strip()
            if username and username not in seen:
                seen.append(username)
        return seen


# ---------------------------------------------------------------------------
# Factory — three-tier client selection
# ---------------------------------------------------------------------------

def _glab_available() -> bool:
    """Return True if `glab` is found on PATH."""
    import shutil as _shutil
    return _shutil.which("glab") is not None


def make_client(
    settings: object,
    rest_only: bool = False,
    project_id: str | None = None,
    mcp_mode: str | None = None,
) -> "GitLabGlabMCPClient | GitLabYodaMCPClient | GitLabRESTClient":
    """
    Return the appropriate GitLab client for this environment.

    Tier selection (overridable via mcp_mode / QUORUM_MCP_MODE):

      "glab"     → GitLabGlabMCPClient  — official CLI, semantic search,
                   requires glab v1.80+ and Ultimate for AI search
      "zereight" → GitLabYodaMCPClient  — community npm fallback,
                   107 tools, no semantic search, requires Node.js
      "rest"     → GitLabRESTClient     — pure Python, lexical search,
                   no external dependencies

    Auto-detection when mcp_mode is None:
      glab installed → "glab"  (best)
      else           → "rest"  (safest, no Node.js required)
    """
    from quorum.config import Settings
    s: Settings = settings  # type: ignore[assignment]

    # Explicit override: --rest-only CLI flag
    if rest_only:
        return GitLabRESTClient(s.gitlab_url, s.gitlab_token)

    # Explicit mode from settings or parameter
    mode = mcp_mode or getattr(s, "mcp_mode", None) or ("glab" if _glab_available() else "rest")

    if mode == "glab":
        if not _glab_available():
            log.warning("glab_not_found_falling_back_to_rest")
            return GitLabRESTClient(s.gitlab_url, s.gitlab_token)
        pid = project_id or ""
        return GitLabGlabMCPClient(s.gitlab_url, s.gitlab_token, project_id=pid)

    if mode == "zereight":
        import shlex
        cmd = shlex.split(s.mcp_server_cmd)
        return GitLabYodaMCPClient(s.gitlab_url, s.gitlab_token, server_cmd=cmd)

    return GitLabRESTClient(s.gitlab_url, s.gitlab_token)
