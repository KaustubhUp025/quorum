"""GitLab MCP client.

Connects to the official GitLab MCP server via streamable-HTTP transport
(MCP spec 2025-06-18) or SSE transport (spec 2025-03-26) and exposes
the subset of tools that Quorum uses as typed Python methods.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

log = structlog.get_logger(__name__)


class GitLabMCPClient:
    """Thin async wrapper around the GitLab MCP server."""

    def __init__(self, mcp_url: str, token: str) -> None:
        self._url = mcp_url
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._session: ClientSession | None = None

    # ------------------------------------------------------------------
    # Context-manager lifecycle
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[GitLabMCPClient, None]:
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call(self, tool_name: str, arguments: dict) -> str:
        """Call an MCP tool and return its text content as a string."""
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
                parts.append(json.dumps(content.model_dump() if hasattr(content, "model_dump") else str(content)))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # GitLab MCP tool wrappers
    # ------------------------------------------------------------------

    async def get_merge_request_diffs(
        self, project_id: str, mr_iid: int
    ) -> str:
        return await self._call(
            "get_merge_request_diffs",
            {"project_id": project_id, "iid": mr_iid},
        )

    async def get_merge_request(
        self, project_id: str, mr_iid: int
    ) -> str:
        return await self._call(
            "get_merge_request",
            {"project_id": project_id, "iid": mr_iid},
        )

    async def semantic_code_search(
        self, project_id: str, query: str, max_results: int = 5
    ) -> str:
        return await self._call(
            "semantic_code_search",
            {
                "project_id": project_id,
                "search_query": query,
                "per_page": max_results,
            },
        )

    async def create_workitem_note(
        self, project_id: str, mr_iid: int, body: str, note_type: str = "MergeRequest"
    ) -> str:
        return await self._call(
            "create_workitem_note",
            {
                "project_id": project_id,
                "noteable_type": note_type,
                "noteable_iid": mr_iid,
                "body": body,
            },
        )

    async def manage_pipeline(
        self,
        project_id: str,
        pipeline_id: int,
        action: str,
    ) -> str:
        return await self._call(
            "manage_pipeline",
            {
                "project_id": project_id,
                "pipeline_id": pipeline_id,
                "action": action,
            },
        )

    async def list_available_tools(self) -> list[str]:
        if self._session is None:
            raise RuntimeError("Not connected")
        result = await self._session.list_tools()
        return [t.name for t in result.tools]
