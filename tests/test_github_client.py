"""Unit tests for GitHubRESTClient (all HTTP calls mocked)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from quorum.github_client import GitHubRESTClient


def _mock_response(data, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def client():
    c = GitHubRESTClient(token="ghp_fake_token")
    c._http = AsyncMock()
    return c


# ---------------------------------------------------------------------------
# get_merge_request_diffs
# ---------------------------------------------------------------------------


class TestGetMergeRequestDiffs:
    @pytest.mark.asyncio
    async def test_formats_patch_correctly(self, client):
        client._http.get.return_value = _mock_response(
            [
                {
                    "filename": "src/lock.py",
                    "status": "modified",
                    "patch": "@@ -1,3 +1,4 @@\n+import redis",
                }
            ]
        )
        result = await client.get_merge_request_diffs("owner/repo", 42)
        assert "--- a/src/lock.py" in result
        assert "+++ b/src/lock.py" in result
        assert "@@ -1,3 +1,4 @@" in result
        assert "+import redis" in result

    @pytest.mark.asyncio
    async def test_new_file_label(self, client):
        client._http.get.return_value = _mock_response(
            [{"filename": "newfile.py", "status": "added", "patch": "+print('hello')"}]
        )
        result = await client.get_merge_request_diffs("owner/repo", 1)
        assert "[new file]" in result

    @pytest.mark.asyncio
    async def test_deleted_file_label(self, client):
        client._http.get.return_value = _mock_response(
            [{"filename": "old.py", "status": "removed", "patch": "-import os"}]
        )
        result = await client.get_merge_request_diffs("owner/repo", 1)
        assert "/dev/null" in result

    @pytest.mark.asyncio
    async def test_empty_files_list(self, client):
        client._http.get.return_value = _mock_response([])
        result = await client.get_merge_request_diffs("owner/repo", 1)
        assert result == ""


# ---------------------------------------------------------------------------
# get_merge_request
# ---------------------------------------------------------------------------


class TestGetMergeRequest:
    @pytest.mark.asyncio
    async def test_returns_json_with_expected_keys(self, client):
        client._http.get.return_value = _mock_response({
            "title": "Add payment service",
            "body": "Implements payment flow",
            "user": {"login": "alice"},
            "head": {"ref": "feat/payment", "sha": "abc123"},
            "base": {"ref": "main"},
            "state": "open",
            "html_url": "https://github.com/owner/repo/pull/1",
        })
        result = await client.get_merge_request("owner/repo", 1)
        data = json.loads(result)
        assert data["title"] == "Add payment service"
        assert data["source_branch"] == "feat/payment"
        assert data["target_branch"] == "main"
        assert data["state"] == "open"

    @pytest.mark.asyncio
    async def test_get_mr_metadata_returns_dict(self, client):
        client._http.get.return_value = _mock_response({
            "title": "Fix bug",
            "body": "",
            "user": {"login": "bob"},
            "head": {"ref": "fix/bug", "sha": "def456"},
            "base": {"ref": "develop"},
            "state": "open",
            "html_url": "https://github.com/owner/repo/pull/2",
        })
        meta = await client.get_mr_metadata("owner/repo", 2)
        assert meta["source_branch"] == "fix/bug"
        assert meta["target_branch"] == "develop"
        assert meta["head_sha"] == "def456"


# ---------------------------------------------------------------------------
# semantic_code_search
# ---------------------------------------------------------------------------


class TestSemanticCodeSearch:
    @pytest.mark.asyncio
    async def test_returns_file_paths(self, client):
        client._http.get.return_value = _mock_response({
            "total_count": 1,
            "items": [
                {
                    "path": "src/kafka_consumer.py",
                    "html_url": "https://github.com/owner/repo/blob/main/src/kafka_consumer.py",
                    "text_matches": [{"fragment": "enable_auto_commit=True"}],
                }
            ],
        })
        result = await client.semantic_code_search("owner/repo", "enable_auto_commit")
        assert "src/kafka_consumer.py" in result
        assert "enable_auto_commit=True" in result

    @pytest.mark.asyncio
    async def test_no_results_message(self, client):
        client._http.get.return_value = _mock_response({
            "total_count": 0,
            "items": [],
        })
        result = await client.semantic_code_search("owner/repo", "cancelShipment")
        assert "No code found" in result

    @pytest.mark.asyncio
    async def test_handles_422_gracefully(self, client):
        resp = _mock_response({}, status_code=422)
        resp.raise_for_status = MagicMock()  # don't raise on 422 — handled explicitly
        client._http.get.return_value = resp
        result = await client.semantic_code_search("owner/repo", "x")
        assert "invalid" in result.lower() or "query" in result.lower()


# ---------------------------------------------------------------------------
# create_workitem_note
# ---------------------------------------------------------------------------


class TestCreateWorkitemNote:
    @pytest.mark.asyncio
    async def test_posts_comment_and_returns_id(self, client):
        client._http.post.return_value = _mock_response({"id": 99887766})
        result = await client.create_workitem_note("owner/repo", 1, "# Quorum Review\n...")
        data = json.loads(result)
        assert data["id"] == 99887766

    @pytest.mark.asyncio
    async def test_posts_to_correct_url(self, client):
        client._http.post.return_value = _mock_response({"id": 1})
        await client.create_workitem_note("my-org/my-repo", 7, "body text")
        call_args = client._http.post.call_args
        url = call_args[0][0]
        assert "my-org/my-repo" in url
        assert "/issues/7/comments" in url


# ---------------------------------------------------------------------------
# get_file_contents
# ---------------------------------------------------------------------------


class TestGetFileContents:
    @pytest.mark.asyncio
    async def test_decodes_base64_content(self, client):
        import base64
        content = "print('hello world')"
        encoded = base64.b64encode(content.encode()).decode()
        client._http.get.return_value = _mock_response({"content": encoded + "\n"})
        result = await client.get_file_contents("owner/repo", "src/app.py")
        assert result == content

    @pytest.mark.asyncio
    async def test_returns_not_found_message(self, client):
        resp = _mock_response({}, status_code=404)
        resp.raise_for_status = MagicMock()
        client._http.get.return_value = resp
        result = await client.get_file_contents("owner/repo", "missing.py")
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# manage_pipeline
# ---------------------------------------------------------------------------


class TestManagePipeline:
    @pytest.mark.asyncio
    async def test_returns_unsupported_message(self, client):
        result = await client.manage_pipeline("owner/repo", 123, "cancel")
        assert "not supported" in result.lower() or "github" in result.lower()


# ---------------------------------------------------------------------------
# get_project_permissions (read-only pre-flight parity with GitLab)
# ---------------------------------------------------------------------------


class TestGetProjectPermissions:
    @pytest.mark.asyncio
    async def test_push_means_can_write(self, client):
        client._http.get.return_value = _mock_response({"permissions": {"admin": False, "push": True, "pull": True}})
        p = await client.get_project_permissions("owner/repo")
        assert p["can_write"] is True and p["access_level"] == 30

    @pytest.mark.asyncio
    async def test_pull_only_is_read_only(self, client):
        client._http.get.return_value = _mock_response({"permissions": {"admin": False, "push": False, "pull": True}})
        p = await client.get_project_permissions("owner/repo")
        assert p["can_write"] is False and p["access_level"] == 0

    @pytest.mark.asyncio
    async def test_404_is_read_only(self, client):
        client._http.get.return_value = _mock_response({}, status_code=404)
        p = await client.get_project_permissions("owner/repo")
        assert p["can_write"] is False
