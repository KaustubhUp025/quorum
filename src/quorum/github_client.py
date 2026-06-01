"""GitHub REST API client for Quorum.

Same async interface as GitLabRESTClient — the agent and formatter are unchanged.

  project_id  → "owner/repo"  (e.g. "KaustubhUp025/quorum-demo")
  mr_iid      → pull request number

Auth: QUORUM_GITHUB_TOKEN (personal access token with repo + read:discussion scope).
"""

from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import quote

import httpx
import structlog

log = structlog.get_logger(__name__)

_BASE = "https://api.github.com"


class GitHubRESTClient:
    """
    GitHub REST API client satisfying the same interface as GitLabRESTClient.

    project_id → "owner/repo"
    mr_iid     → pull request number
    """

    def __init__(self, token: str, base_url: str = _BASE) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._http: httpx.AsyncClient | None = None

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator["GitHubRESTClient", None]:
        async with httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        ) as client:
            self._http = client
            log.info("github_rest_client_ready", base=self._base)
            yield self
            self._http = None

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("Not connected — use `async with client.connect()`")
        return self._http

    # ------------------------------------------------------------------
    # PR diffs
    # ------------------------------------------------------------------

    async def get_merge_request_diffs(self, project_id: str, mr_iid: int) -> str:
        resp = await self._client.get(
            f"{self._base}/repos/{project_id}/pulls/{mr_iid}/files",
            params={"per_page": 100},
        )
        resp.raise_for_status()
        files = resp.json()
        parts: list[str] = []
        for f in files:
            filename = f.get("filename", "")
            status = f.get("status", "modified")
            patch = f.get("patch", "")
            parts.append(f"--- a/{filename}")
            if status == "added":
                parts.append(f"+++ b/{filename}  [new file]")
            elif status == "removed":
                parts.append("+++ /dev/null  [deleted]")
            else:
                parts.append(f"+++ b/{filename}")
            if patch:
                parts.append(patch)
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # PR info
    # ------------------------------------------------------------------

    async def get_merge_request(self, project_id: str, mr_iid: int) -> str:
        resp = await self._client.get(
            f"{self._base}/repos/{project_id}/pulls/{mr_iid}"
        )
        resp.raise_for_status()
        pr = resp.json()
        return json.dumps({
            "title": pr.get("title"),
            "description": pr.get("body"),
            "author": pr.get("user", {}).get("login"),
            "source_branch": pr.get("head", {}).get("ref"),
            "target_branch": pr.get("base", {}).get("ref"),
            "state": pr.get("state"),
            "web_url": pr.get("html_url"),
        }, indent=2)

    async def get_mr_metadata(self, project_id: str, mr_iid: int) -> dict:
        resp = await self._client.get(
            f"{self._base}/repos/{project_id}/pulls/{mr_iid}"
        )
        resp.raise_for_status()
        pr = resp.json()
        return {
            "source_branch": pr.get("head", {}).get("ref", ""),
            "target_branch": pr.get("base", {}).get("ref", "main"),
            "web_url": pr.get("html_url", ""),
            "title": pr.get("title", ""),
            "state": pr.get("state", ""),
            "head_sha": pr.get("head", {}).get("sha", ""),
        }

    # ------------------------------------------------------------------
    # Code search
    # ------------------------------------------------------------------

    async def semantic_code_search(
        self, project_id: str, query: str, max_results: int = 5
    ) -> str:
        q = f"{query} repo:{project_id}"
        resp = await self._client.get(
            f"{self._base}/search/code",
            params={"q": q, "per_page": max_results},
            headers={
                "Accept": "application/vnd.github.text-match+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if resp.status_code == 422:
            return f"[GitHub code search: query invalid for: {query!r}]"
        if resp.status_code == 403:
            return f"[GitHub code search: rate limit exceeded or auth required]"
        if resp.status_code == 451:
            return f"[GitHub code search: unavailable for legal reasons]"
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        if not items:
            return f"No code found matching: {query!r}"
        parts = [f"GitHub code search results for: {query!r}\n"]
        for item in items:
            path = item.get("path", "")
            url = item.get("html_url", "")
            parts.append(f"File: {path}  ({url})")
            for match in item.get("text_matches", []):
                fragment = match.get("fragment", "")
                if fragment:
                    parts.append(fragment)
            parts.append("---")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Comment posting
    # ------------------------------------------------------------------

    async def create_workitem_note(
        self, project_id: str, mr_iid: int, body: str, note_type: str = "PullRequest"
    ) -> str:
        resp = await self._client.post(
            f"{self._base}/repos/{project_id}/issues/{mr_iid}/comments",
            json={"body": body},
        )
        resp.raise_for_status()
        comment = resp.json()
        comment_id = comment.get("id")
        log.info("github_comment_posted", comment_id=comment_id)
        return json.dumps({"id": comment_id})

    async def manage_pipeline(self, project_id: str, pipeline_id: int, action: str) -> str:
        log.warning("manage_pipeline_not_supported_github", action=action)
        return "[GitHub mode: pipeline gating not supported via REST]"

    # ------------------------------------------------------------------
    # File contents
    # ------------------------------------------------------------------

    async def get_file_contents(
        self, project_id: str, file_path: str, ref: str = "HEAD"
    ) -> str:
        params: dict = {}
        if ref and ref != "HEAD":
            params["ref"] = ref
        encoded_path = quote(file_path, safe="/")  # preserve path separators, encode rest
        resp = await self._client.get(
            f"{self._base}/repos/{project_id}/contents/{encoded_path}",
            params=params,
        )
        if resp.status_code == 404:
            return f"[File not found: {file_path}]"
        resp.raise_for_status()
        data = resp.json()
        content_b64 = data.get("content", "").replace("\n", "")
        try:
            return base64.b64decode(content_b64).decode("utf-8", errors="replace")
        except Exception:
            return content_b64

    # ------------------------------------------------------------------
    # Branch and file operations (fix PR creation)
    # ------------------------------------------------------------------

    async def create_branch(self, project_id: str, branch: str, ref: str) -> str:
        # Resolve the source branch to a commit SHA
        ref_resp = await self._client.get(
            f"{self._base}/repos/{project_id}/git/ref/heads/{ref}"
        )
        ref_resp.raise_for_status()
        sha = ref_resp.json()["object"]["sha"]

        create_resp = await self._client.post(
            f"{self._base}/repos/{project_id}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )
        create_resp.raise_for_status()
        log.info("github_branch_created", branch=branch, sha=sha[:8])
        return json.dumps({"name": branch, "sha": sha})

    async def commit_file(
        self, project_id: str, branch: str, file_path: str, content: str, message: str
    ) -> str:
        encoded_path = quote(file_path, safe="/")
        # Check whether the file already exists (need its SHA for updates)
        get_resp = await self._client.get(
            f"{self._base}/repos/{project_id}/contents/{encoded_path}",
            params={"ref": branch},
        )
        body: dict = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if get_resp.status_code == 200:
            body["sha"] = get_resp.json().get("sha", "")

        resp = await self._client.put(
            f"{self._base}/repos/{project_id}/contents/{encoded_path}",
            json=body,
        )
        resp.raise_for_status()
        commit_sha = resp.json().get("commit", {}).get("sha", "")
        log.info("github_file_committed", file=file_path, sha=commit_sha[:8])
        return json.dumps({"sha": commit_sha})

    async def create_merge_request(
        self,
        project_id: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
    ) -> str:
        resp = await self._client.post(
            f"{self._base}/repos/{project_id}/pulls",
            json={
                "title": title,
                "head": source_branch,
                "base": target_branch,
                "body": description,
                "draft": True,
            },
        )
        resp.raise_for_status()
        pr = resp.json()
        pr_number = pr.get("number")
        pr_url = pr.get("html_url")
        log.info("github_pr_created", pr_number=pr_number, url=pr_url)
        return json.dumps({
            "iid": pr_number,
            "title": pr.get("title"),
            "web_url": pr_url,
            "state": pr.get("state"),
        }, indent=2)

    # ------------------------------------------------------------------
    # CI / Actions (for CI failure correlation)
    # ------------------------------------------------------------------

    async def get_mr_pipelines(self, project_id: str, mr_iid: int) -> list[dict]:
        pr_resp = await self._client.get(
            f"{self._base}/repos/{project_id}/pulls/{mr_iid}"
        )
        pr_resp.raise_for_status()
        head_sha = pr_resp.json().get("head", {}).get("sha", "")
        if not head_sha:
            return []
        runs_resp = await self._client.get(
            f"{self._base}/repos/{project_id}/actions/runs",
            params={"head_sha": head_sha, "per_page": 5},
        )
        if runs_resp.status_code == 404:
            return []
        runs_resp.raise_for_status()
        runs = runs_resp.json().get("workflow_runs", [])
        return [
            {
                "id": r.get("id"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "name": r.get("name"),
            }
            for r in runs
        ]

    async def get_pipeline_jobs(self, project_id: str, pipeline_id: int) -> list[dict]:
        resp = await self._client.get(
            f"{self._base}/repos/{project_id}/actions/runs/{pipeline_id}/jobs",
            params={"filter": "latest", "per_page": 10},
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        failed = [j for j in jobs if j.get("conclusion") == "failure"]
        return [
            {"id": j.get("id"), "name": j.get("name"), "status": j.get("conclusion")}
            for j in failed
        ]

    async def get_pipeline_job_output(self, project_id: str, job_id: int) -> str:
        resp = await self._client.get(
            f"{self._base}/repos/{project_id}/actions/jobs/{job_id}/logs"
        )
        if resp.status_code in (404, 410):
            return f"[Job log not available for job_id={job_id}]"
        resp.raise_for_status()
        log_text = resp.text
        if len(log_text) > 8000:
            log_text = f"[...truncated — last 8000 chars shown...]\n{log_text[-8000:]}"
        return log_text


def make_github_client(settings: object) -> GitHubRESTClient:
    """Return a GitHubRESTClient using QUORUM_GITHUB_TOKEN from settings."""
    from quorum.config import Settings
    s: Settings = settings  # type: ignore[assignment]
    token = s.github_token
    if not token:
        raise ValueError(
            "QUORUM_GITHUB_TOKEN is required for --platform github. "
            "Set it in your .env file or environment."
        )
    return GitHubRESTClient(token=token)
