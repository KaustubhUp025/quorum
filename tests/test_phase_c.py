"""Tests for Phase C features:
  C1 — Duplicate Discussion Guard
  C2 — Language-Aware Rule Suppression
  C3 — MR Label Application
  C4 — Reviewer Suggestion for CRITICAL findings
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from quorum.agent import (
    QuorumAgent,
    _QUORUM_FINGERPRINT,
    _RULE_REQUIRES_LANGUAGE,
    _already_reviewed,
    _get_language_suppressions,
)
from quorum.config import Settings
from quorum.formatter import ReportFormatterAgent, _inline_comment_body
from quorum.gitlab_client import GitLabYodaMCPClient
from quorum.models import Finding, ReviewResult, Severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _settings(**overrides) -> Settings:
    base = dict(gemini_api_key="test-key", gitlab_token="test-token", min_confidence=60)
    base.update(overrides)
    return Settings(**base)  # type: ignore[call-arg]


def _finding(
    rule_id: str = "RULE_01",
    severity: Severity = Severity.CRITICAL,
    file_path: str | None = "src/order/service.py",
    line_number: int | None = 10,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        rule_name="Lock Missing Fencing Token",
        title="Lock acquired without fencing token",
        severity=severity,
        confidence=92,
        explanation="Static lock value — another process can corrupt state after TTL expiry.",
        file_path=file_path,
        line_number=line_number,
    )


def _result(findings: list[Finding]) -> ReviewResult:
    return ReviewResult(
        mr_iid=1,
        project_id="org/repo",
        findings=findings,
        surfaces_detected=len(findings),
        rules_checked=len(findings),
        blocked=any(f.severity == Severity.CRITICAL for f in findings),
    )


def _diff_refs() -> dict:
    return {"base_sha": "aaa", "head_sha": "bbb", "start_sha": "aaa", "author": ""}


# Minimal Gemini response mocks
def _make_function_call(name: str, args: dict) -> MagicMock:
    fc = MagicMock()
    fc.name = name
    fc.args = args
    return fc


def _make_part(*, text: str | None = None, function_call=None) -> MagicMock:
    part = MagicMock()
    part.text = text
    part.function_call = function_call
    part.thought = None
    return part


def _make_response(parts: list) -> MagicMock:
    content = MagicMock()
    content.parts = parts
    content.role = "model"
    candidate = MagicMock()
    candidate.content = content
    response = MagicMock()
    response.candidates = [candidate]
    return response


def _findings_json(findings: list[dict]) -> str:
    payload = {"surfaces_found": ["lock acquisition in service.py:10"], "findings": findings}
    return f"Analysis complete:\n\n```json\n{json.dumps(payload)}\n```"


_CRITICAL_FINDING_DICT = {
    "rule_id": "RULE_01",
    "rule_name": "Fencing Token Missing",
    "severity": "CRITICAL",
    "confidence": 92,
    "title": "Lock acquired without fencing token",
    "explanation": "Static lock value allows stale writes.",
    "file_path": "src/order/service.py",
    "line_number": 10,
}

_HIGH_FINDING_DICT = {
    "rule_id": "RULE_06",
    "rule_name": "Retry Backoff Missing Jitter",
    "severity": "HIGH",
    "confidence": 80,
    "title": "Retry without jitter",
    "explanation": "Deterministic backoff causes thundering herd.",
    "file_path": "src/retry.py",
    "line_number": 25,
}

_BAD_DIFF = """\
diff --git a/src/order/service.py b/src/order/service.py
--- a/src/order/service.py
+++ b/src/order/service.py
@@ -10,6 +10,12 @@ class OrderService:
+    def process(self, order_id: str) -> None:
+        acquired = self.redis.set(f"order:{order_id}", "locked", nx=True, px=30_000)
+        if not acquired:
+            raise LockError("could not acquire lock")
+        self._write_to_db(order_id)
+        self.redis.delete(f"order:{order_id}")
"""


def _mcp_mock(*, fingerprint: bool = False, languages: dict | None = None) -> AsyncMock:
    """Build a minimal client mock for Phase C tests."""
    mcp = AsyncMock(spec=GitLabYodaMCPClient)
    mcp.get_merge_request_diffs.return_value = _BAD_DIFF
    mcp.get_mr_metadata.return_value = {
        "source_branch": "feat/test",
        "target_branch": "main",
        "web_url": "https://gitlab.com/org/repo/-/merge_requests/1",
        "title": "Add lock",
        "state": "opened",
        "author": "alice",
        "base_sha": "aaa",
        "head_sha": "bbb",
        "start_sha": "aaa",
    }
    mcp.semantic_code_search.return_value = "no fencing token found"
    mcp.create_workitem_note.return_value = '{"id": 1001}'
    mcp.create_mr_discussion.return_value = '{"id": "disc-1"}'
    mcp.get_file_contents.return_value = "# not used"
    mcp.create_branch.return_value = '{"name": "branch"}'
    mcp.commit_file.return_value = '{"id": "abc"}'
    mcp.create_merge_request.return_value = json.dumps({"iid": 99, "web_url": ""})
    mcp.get_mr_pipelines.return_value = []
    mcp.get_pipeline_jobs.return_value = []

    notes = (
        [{"body": f"Some text containing {_QUORUM_FINGERPRINT} already."}]
        if fingerprint
        else []
    )
    mcp.list_mr_notes.return_value = notes
    mcp.get_project_languages.return_value = languages if languages is not None else {}
    mcp.apply_mr_labels.return_value = None
    mcp.get_file_contributors.return_value = []
    return mcp


# ---------------------------------------------------------------------------
# C1 — Duplicate Discussion Guard: unit tests for _already_reviewed
# ---------------------------------------------------------------------------

class TestAlreadyReviewed:
    @pytest.mark.asyncio
    async def test_returns_true_when_fingerprint_in_notes(self):
        client = AsyncMock()
        client.list_mr_notes.return_value = [
            {"body": f"Intro text. {_QUORUM_FINGERPRINT} — v1.0"},
        ]
        assert await _already_reviewed(client, "org/repo", 1) is True

    @pytest.mark.asyncio
    async def test_returns_false_when_no_fingerprint(self):
        client = AsyncMock()
        client.list_mr_notes.return_value = [
            {"body": "Just a regular code review comment."},
        ]
        assert await _already_reviewed(client, "org/repo", 1) is False

    @pytest.mark.asyncio
    async def test_returns_false_for_empty_notes(self):
        client = AsyncMock()
        client.list_mr_notes.return_value = []
        assert await _already_reviewed(client, "org/repo", 1) is False

    @pytest.mark.asyncio
    async def test_returns_false_when_list_mr_notes_raises(self):
        client = AsyncMock()
        client.list_mr_notes.side_effect = RuntimeError("API error")
        # Should not propagate — returns False to allow review to proceed
        assert await _already_reviewed(client, "org/repo", 1) is False

    @pytest.mark.asyncio
    async def test_returns_false_when_result_is_not_a_list(self):
        client = AsyncMock()
        client.list_mr_notes.return_value = MagicMock()  # wrong type
        assert await _already_reviewed(client, "org/repo", 1) is False

    @pytest.mark.asyncio
    async def test_returns_false_when_note_has_no_body_key(self):
        client = AsyncMock()
        client.list_mr_notes.return_value = [{"id": 1}]  # no "body" key
        assert await _already_reviewed(client, "org/repo", 1) is False


# ---------------------------------------------------------------------------
# C1 — Duplicate Discussion Guard: integration tests via review()
# ---------------------------------------------------------------------------

class TestDuplicateGuardIntegration:
    @pytest.mark.asyncio
    async def test_review_skips_when_already_reviewed(self):
        mcp = _mcp_mock(fingerprint=True)
        agent = QuorumAgent(_settings())

        result = await agent.review("org/repo", 1, mcp, post_comment=True)

        # Early-exit: no diff fetch, no Gemini, no new comment
        mcp.get_merge_request_diffs.assert_not_called()
        mcp.create_workitem_note.assert_not_called()
        assert result.surfaces_detected == 0
        assert result.rules_checked == 0
        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_force_flag_overrides_duplicate_guard(self):
        mcp = _mcp_mock(fingerprint=True)
        agent = QuorumAgent(_settings())
        response = _make_response([_make_part(text=_findings_json([_CRITICAL_FINDING_DICT]))])

        with patch.object(agent, "_generate", new=AsyncMock(return_value=response)):
            result = await agent.review("org/repo", 1, mcp, post_comment=True, force=True)

        mcp.get_merge_request_diffs.assert_called_once()
        assert result.critical_count == 1

    @pytest.mark.asyncio
    async def test_force_review_setting_overrides_duplicate_guard(self):
        mcp = _mcp_mock(fingerprint=True)
        agent = QuorumAgent(_settings(force_review=True))
        response = _make_response([_make_part(text=_findings_json([_HIGH_FINDING_DICT]))])

        with patch.object(agent, "_generate", new=AsyncMock(return_value=response)):
            result = await agent.review("org/repo", 1, mcp, post_comment=True)

        mcp.get_merge_request_diffs.assert_called_once()
        assert result.high_count == 1

    @pytest.mark.asyncio
    async def test_post_comment_false_skips_duplicate_check(self):
        """When not posting, skip the fingerprint check (no duplicate concern)."""
        mcp = _mcp_mock(fingerprint=True)
        agent = QuorumAgent(_settings())
        response = _make_response([_make_part(text=_findings_json([_CRITICAL_FINDING_DICT]))])

        with patch.object(agent, "_generate", new=AsyncMock(return_value=response)):
            result = await agent.review("org/repo", 1, mcp, post_comment=False)

        # Review proceeds even when fingerprint exists (no comment will be posted anyway)
        mcp.list_mr_notes.assert_not_called()
        assert result.critical_count == 1


# ---------------------------------------------------------------------------
# C2 — Language-Aware Rule Suppression: unit tests
# ---------------------------------------------------------------------------

class TestLanguageSuppressions:
    def test_empty_dict_returns_empty_set(self):
        assert _get_language_suppressions({}) == set()

    def test_non_dict_returns_empty_set(self):
        assert _get_language_suppressions(MagicMock()) == set()  # type: ignore[arg-type]
        assert _get_language_suppressions(None) == set()  # type: ignore[arg-type]
        assert _get_language_suppressions([]) == set()  # type: ignore[arg-type]

    def test_java_absent_suppresses_rule05(self):
        # RULE_05 requires Java; a Python-only project should suppress it
        suppressed = _get_language_suppressions({"Python": 80.0, "Shell": 20.0})
        assert "RULE_05" in suppressed

    def test_java_present_does_not_suppress_rule05(self):
        suppressed = _get_language_suppressions({"Java": 60.0, "Python": 40.0})
        assert "RULE_05" not in suppressed

    def test_go_dominant_suppresses_rule03(self):
        # Go >50% triggers dominant-lang suppression for RULE_03
        suppressed = _get_language_suppressions({"Go": 80.0, "Shell": 20.0})
        assert "RULE_03" in suppressed

    def test_go_not_dominant_does_not_suppress_rule03(self):
        # Go at 40% → not dominant → no RULE_03 suppression
        suppressed = _get_language_suppressions({"Go": 40.0, "Java": 60.0})
        assert "RULE_03" not in suppressed

    def test_go_dominant_also_suppresses_java_dependent_rules(self):
        # Go project has no Java, so RULE_05 should also be suppressed
        suppressed = _get_language_suppressions({"Go": 80.0, "Shell": 20.0})
        assert "RULE_05" in suppressed
        assert "RULE_03" in suppressed

    def test_python_dominant_no_extra_suppressions(self):
        # Python dominant does not appear in _DOMINANT_LANG_SUPPRESSIONS
        suppressed = _get_language_suppressions({"Python": 90.0})
        # RULE_05 suppressed (no Java); no dominant suppressions for Python
        assert "RULE_05" in suppressed
        assert "RULE_03" not in suppressed

    def test_exactly_50_percent_dominant_does_not_trigger(self):
        # >50% is required; 50% exactly should NOT trigger dominant suppression
        suppressed = _get_language_suppressions({"Go": 50.0, "Python": 50.0})
        assert "RULE_03" not in suppressed


# ---------------------------------------------------------------------------
# C3 — MR Label Application: integration tests
# ---------------------------------------------------------------------------

class TestLabelApplication:
    @pytest.mark.asyncio
    async def test_critical_finding_applies_critical_label(self):
        mcp = _mcp_mock()
        agent = QuorumAgent(_settings(apply_labels=True))
        response = _make_response([_make_part(text=_findings_json([_CRITICAL_FINDING_DICT]))])

        with patch.object(agent, "_generate", new=AsyncMock(return_value=response)):
            result = await agent.review("org/repo", 1, mcp, post_comment=True)

        assert result.critical_count == 1
        mcp.apply_mr_labels.assert_called_once_with("org/repo", 1, ["quorum-review::critical"])

    @pytest.mark.asyncio
    async def test_high_only_finding_applies_regular_label(self):
        mcp = _mcp_mock()
        agent = QuorumAgent(_settings(apply_labels=True))
        response = _make_response([_make_part(text=_findings_json([_HIGH_FINDING_DICT]))])

        with patch.object(agent, "_generate", new=AsyncMock(return_value=response)):
            result = await agent.review("org/repo", 1, mcp, post_comment=True)

        assert result.high_count == 1
        assert result.critical_count == 0
        mcp.apply_mr_labels.assert_called_once_with("org/repo", 1, ["quorum-review"])

    @pytest.mark.asyncio
    async def test_apply_labels_false_skips_labelling(self):
        mcp = _mcp_mock()
        agent = QuorumAgent(_settings(apply_labels=False))
        response = _make_response([_make_part(text=_findings_json([_CRITICAL_FINDING_DICT]))])

        with patch.object(agent, "_generate", new=AsyncMock(return_value=response)):
            await agent.review("org/repo", 1, mcp, post_comment=True)

        mcp.apply_mr_labels.assert_not_called()

    @pytest.mark.asyncio
    async def test_label_exception_does_not_break_review(self):
        mcp = _mcp_mock()
        mcp.apply_mr_labels.side_effect = RuntimeError("permission denied")
        agent = QuorumAgent(_settings(apply_labels=True))
        response = _make_response([_make_part(text=_findings_json([_CRITICAL_FINDING_DICT]))])

        with patch.object(agent, "_generate", new=AsyncMock(return_value=response)):
            result = await agent.review("org/repo", 1, mcp, post_comment=True)

        # Exception swallowed; result still contains the finding
        assert result.critical_count == 1
        assert result.blocked is True

    @pytest.mark.asyncio
    async def test_label_not_applied_when_post_comment_false(self):
        """Labels are only applied when posting a comment."""
        mcp = _mcp_mock()
        agent = QuorumAgent(_settings(apply_labels=True))
        response = _make_response([_make_part(text=_findings_json([_CRITICAL_FINDING_DICT]))])

        with patch.object(agent, "_generate", new=AsyncMock(return_value=response)):
            await agent.review("org/repo", 1, mcp, post_comment=False)

        mcp.apply_mr_labels.assert_not_called()


# ---------------------------------------------------------------------------
# C4 — Reviewer Suggestion: unit tests on post_review()
# ---------------------------------------------------------------------------

class _C4MockClient:
    """Minimal client stub for C4 formatter tests."""

    def __init__(self, contributors: list[str] | None = None, raise_on_contributors: bool = False):
        self.inline_calls: list[dict] = []
        self.note_calls: list[str] = []
        self._contributors = contributors or []
        self._raise = raise_on_contributors
        self.contributor_queries: list[tuple[str, str]] = []

    async def create_mr_discussion(self, project_id, mr_iid, body,
                                   file_path=None, line_number=None, diff_refs=None):
        self.inline_calls.append({"body": body, "file_path": file_path})
        return '{"id": "disc-1"}'

    async def create_workitem_note(self, project_id, mr_iid, body, note_type="MergeRequest"):
        self.note_calls.append(body)
        return '{"id": 999}'

    async def get_file_contributors(self, project_id: str, file_path: str) -> list[str]:
        self.contributor_queries.append((project_id, file_path))
        if self._raise:
            raise RuntimeError("contributor lookup failed")
        return self._contributors


class TestReviewerSuggestion:
    @pytest.mark.asyncio
    async def test_critical_finding_triggers_contributor_query(self):
        client = _C4MockClient(contributors=["bob", "carol"])
        f = _finding(rule_id="RULE_01", severity=Severity.CRITICAL, file_path="src/order/service.py")
        result = _result([f])
        mr_meta = {**_diff_refs(), "author": "alice"}

        await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)

        assert len(client.contributor_queries) == 1
        assert client.contributor_queries[0] == ("org/repo", "src/order/service.py")

    @pytest.mark.asyncio
    async def test_reviewer_suggestion_appended_to_inline_body(self):
        client = _C4MockClient(contributors=["bob", "carol"])
        f = _finding(rule_id="RULE_01", severity=Severity.CRITICAL, file_path="src/order/service.py")
        result = _result([f])
        mr_meta = {**_diff_refs(), "author": "alice"}

        await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)

        assert len(client.inline_calls) == 1
        body = client.inline_calls[0]["body"]
        assert "Suggested reviewer" in body
        assert "@bob" in body

    @pytest.mark.asyncio
    async def test_author_excluded_from_reviewer_suggestion(self):
        client = _C4MockClient(contributors=["alice", "bob"])  # alice is the MR author
        f = _finding(rule_id="RULE_01", severity=Severity.CRITICAL, file_path="src/order/service.py")
        result = _result([f])
        mr_meta = {**_diff_refs(), "author": "alice"}

        await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)

        body = client.inline_calls[0]["body"]
        assert "@alice" not in body
        assert "@bob" in body

    @pytest.mark.asyncio
    async def test_no_suggestion_when_no_contributors(self):
        client = _C4MockClient(contributors=[])
        f = _finding(rule_id="RULE_01", severity=Severity.CRITICAL, file_path="src/order/service.py")
        result = _result([f])
        mr_meta = {**_diff_refs(), "author": "alice"}

        await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)

        body = client.inline_calls[0]["body"]
        assert "Suggested reviewer" not in body

    @pytest.mark.asyncio
    async def test_high_finding_does_not_trigger_contributor_query(self):
        client = _C4MockClient(contributors=["bob"])
        f = _finding(rule_id="RULE_06", severity=Severity.HIGH, file_path="src/retry.py")
        result = _result([f])
        mr_meta = {**_diff_refs(), "author": "alice"}

        await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)

        assert len(client.contributor_queries) == 0
        body = client.inline_calls[0]["body"]
        assert "Suggested reviewer" not in body

    @pytest.mark.asyncio
    async def test_contributor_exception_does_not_block_inline_post(self):
        client = _C4MockClient(raise_on_contributors=True)
        f = _finding(rule_id="RULE_01", severity=Severity.CRITICAL, file_path="src/order/service.py")
        result = _result([f])
        mr_meta = {**_diff_refs(), "author": "alice"}

        # Should not raise — exception is swallowed, inline comment still posted
        await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)

        assert len(client.inline_calls) == 1
        assert len(client.note_calls) == 1
        body = client.inline_calls[0]["body"]
        assert "Suggested reviewer" not in body

    @pytest.mark.asyncio
    async def test_non_list_contributors_handled_gracefully(self):
        """If get_file_contributors returns a wrong type, no suggestion is added."""

        class _BadClient(_C4MockClient):
            async def get_file_contributors(self, project_id: str, file_path: str) -> Any:  # type: ignore[override]
                return MagicMock()  # wrong type

        client = _BadClient()
        f = _finding(rule_id="RULE_01", severity=Severity.CRITICAL, file_path="src/order/service.py")
        result = _result([f])
        mr_meta = {**_diff_refs(), "author": "alice"}

        await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)

        assert len(client.inline_calls) == 1
        body = client.inline_calls[0]["body"]
        assert "Suggested reviewer" not in body

    @pytest.mark.asyncio
    async def test_at_most_two_reviewers_suggested(self):
        client = _C4MockClient(contributors=["bob", "carol", "dan", "erin"])
        f = _finding(rule_id="RULE_01", severity=Severity.CRITICAL, file_path="src/order/service.py")
        result = _result([f])
        mr_meta = {**_diff_refs(), "author": "alice"}

        await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)

        body = client.inline_calls[0]["body"]
        # Only first two non-author contributors should appear
        assert "@bob" in body
        assert "@carol" in body
        assert "@dan" not in body
