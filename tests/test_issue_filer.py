"""Unit tests for the issue_filer module."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from quorum.issue_filer import (
    IssueFilingResult,
    file_finding_issue,
    _build_issue_title,
    _build_issue_body,
)
from quorum.models import Finding, ReviewResult, Severity


def _make_result(**kwargs) -> ReviewResult:
    defaults = dict(mr_iid=42, project_id="myorg/myrepo", blocked=False)
    defaults.update(kwargs)
    return ReviewResult(**defaults)


def _make_finding(**kwargs) -> Finding:
    defaults = dict(
        rule_id="RULE_06",
        rule_name="Retry Without Jitter",
        severity=Severity.HIGH,
        confidence=95,
        title="Fixed retry backoff — thundering herd risk",
        explanation="All consumers retry simultaneously after the same fixed delay.",
        file_path="src/consumer.py",
    )
    defaults.update(kwargs)
    return Finding(**defaults)


def _make_client(has_issues: bool = True, issue_blocked: bool = False) -> MagicMock:
    client = MagicMock()
    client.check_repo_metadata = AsyncMock(return_value={
        "has_issues": has_issues,
        "has_discussions": False,
        "visibility": "public",
    })
    if issue_blocked:
        client.create_issue = AsyncMock(return_value={
            "blocked": True,
            "reason": "issues_disabled",
            "url": None,
            "number": None,
        })
    else:
        client.create_issue = AsyncMock(return_value={
            "blocked": False,
            "number": 42,
            "url": "https://github.com/myorg/myrepo/issues/42",
        })
    client.get_mr_metadata = AsyncMock(return_value={
        "source_branch": "feat/retry-fix",
        "target_branch": "main",
    })
    client.create_merge_request = AsyncMock(return_value=(
        '{"web_url": "https://github.com/myorg/myrepo/pull/99", "iid": 99}'
    ))
    return client


class TestIssueFiler:
    @pytest.mark.asyncio
    async def test_files_issue_when_enabled(self):
        finding = _make_finding()
        result = _make_result(findings=[finding])
        client = _make_client(has_issues=True)

        results = await file_finding_issue(client, result, platform="github")

        assert len(results) == 1
        assert results[0].method == "issue"
        assert results[0].url == "https://github.com/myorg/myrepo/issues/42"
        assert results[0].issue_number == 42
        client.create_issue.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_fix_pr_when_issues_disabled(self):
        finding = _make_finding()
        result = _make_result(findings=[finding])
        client = _make_client(has_issues=False)

        results = await file_finding_issue(client, result, platform="github")

        assert len(results) == 1
        assert results[0].method == "fix_pr"
        assert "pull/99" in (results[0].url or "")
        client.create_issue.assert_not_called()
        client.create_merge_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_falls_back_when_issue_creation_blocked(self):
        finding = _make_finding()
        result = _make_result(findings=[finding])
        client = _make_client(has_issues=True, issue_blocked=True)

        results = await file_finding_issue(client, result, platform="github")

        assert len(results) == 1
        assert results[0].method == "fix_pr"
        assert results[0].blocked_reason == "issues_disabled"

    @pytest.mark.asyncio
    async def test_skips_pass_findings(self):
        pass_finding = _make_finding(severity=Severity.PASS, confidence=100)
        result = _make_result(findings=[pass_finding])
        client = _make_client()

        results = await file_finding_issue(client, result, platform="github")

        assert results == []
        client.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_respects_min_severity_threshold(self):
        medium_finding = _make_finding(severity=Severity.MEDIUM, confidence=80)
        result = _make_result(findings=[medium_finding])
        client = _make_client()

        # With threshold=HIGH, MEDIUM should be skipped
        results = await file_finding_issue(client, result, platform="github", min_severity="HIGH")
        assert results == []

        # With threshold=MEDIUM, MEDIUM should be filed
        results = await file_finding_issue(client, result, platform="github", min_severity="MEDIUM")
        assert len(results) == 1
        assert results[0].method == "issue"

    def test_build_issue_title(self):
        finding = _make_finding()
        title = _build_issue_title(finding, "myorg/myrepo")
        assert "rule-06" in title.lower()
        assert "myrepo" in title

    def test_build_issue_body_contains_key_fields(self):
        finding = _make_finding()
        body = _build_issue_body(finding, "myorg/myrepo", 42, "github")
        assert "RULE_06" in body
        assert "Quorum" in body
        assert "thundering herd" in body.lower()
        assert "PR #42" in body
