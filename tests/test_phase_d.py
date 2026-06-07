"""Tests for Phase D — Fix pipeline verification loop."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from quorum.agent import _verify_fix_pipeline
from quorum.formatter import format_verification_comment
from quorum.models import Finding, Severity


# ---------------------------------------------------------------------------
# format_verification_comment — unit tests
# ---------------------------------------------------------------------------


def _make_finding(fix_mr_iid: int = 7, fix_mr_url: str = "https://gitlab.com/org/repo/-/merge_requests/7") -> Finding:
    return Finding(
        rule_id="RULE_06",
        rule_name="Deterministic Backoff without Jitter",
        severity=Severity.CRITICAL,
        confidence=95,
        title="Retry uses fixed delay — thundering herd risk",
        explanation="No jitter.",
        fix_mr_iid=fix_mr_iid,
        fix_mr_url=fix_mr_url,
    )


class TestFormatVerificationComment:
    def test_success_contains_checkmark(self):
        body = format_verification_comment(
            "RULE_06", 7, "https://example.com/mr/7", "success"
        )
        assert "✅" in body
        assert "RULE_06" in body
        assert "MR !7" in body
        assert "Quorum · Fix Verification" in body

    def test_failed_contains_cross(self):
        body = format_verification_comment(
            "RULE_06", 7, "https://example.com/mr/7", "failed"
        )
        assert "❌" in body
        assert "RULE_06" in body

    def test_canceled_contains_warning(self):
        body = format_verification_comment(
            "RULE_06", 7, None, "canceled"
        )
        assert "⚠️" in body or "canceled" in body.lower()

    def test_timeout_contains_clock(self):
        body = format_verification_comment(
            "RULE_06", 7, None, "timeout"
        )
        assert "⏱" in body or "timed out" in body.lower()

    def test_pipeline_url_included_when_provided(self):
        body = format_verification_comment(
            "RULE_09", 3, None, "success",
            pipeline_url="https://gitlab.com/org/repo/-/pipelines/99"
        )
        assert "view pipeline" in body

    def test_no_pipeline_url_when_absent(self):
        body = format_verification_comment("RULE_09", 3, None, "success")
        assert "view pipeline" not in body

    def test_fix_mr_url_linked(self):
        body = format_verification_comment(
            "RULE_01", 5, "https://gitlab.com/org/repo/-/merge_requests/5", "success"
        )
        assert "view fix MR" in body

    def test_footer_present(self):
        body = format_verification_comment("RULE_01", 5, None, "success")
        assert "Quorum" in body


# ---------------------------------------------------------------------------
# _verify_fix_pipeline — async unit tests
# ---------------------------------------------------------------------------


def _mock_client(pipelines_sequence: list[list[dict]]) -> AsyncMock:
    """Return a mock client whose get_mr_pipelines cycles through the given sequence."""
    client = AsyncMock()
    client.get_mr_pipelines = AsyncMock(side_effect=pipelines_sequence)
    client.create_workitem_note = AsyncMock(return_value="note_123")
    return client


class TestVerifyFixPipeline:
    @pytest.mark.asyncio
    async def test_no_fix_mr_iid_returns_immediately(self):
        finding = _make_finding()
        finding.fix_mr_iid = None
        client = AsyncMock()
        await _verify_fix_pipeline(finding, client, "org/repo", 42)
        client.get_mr_pipelines.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_pipeline_posts_comment(self):
        finding = _make_finding()
        pipeline = {"id": 1, "status": "success", "web_url": "https://gitlab.com/pipe/1"}
        client = _mock_client([[pipeline]])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _verify_fix_pipeline(
                finding, client, "org/repo", 42, timeout_seconds=60
            )

        client.create_workitem_note.assert_called_once()
        body = client.create_workitem_note.call_args[0][2]
        assert "✅" in body

    @pytest.mark.asyncio
    async def test_failed_pipeline_posts_comment(self):
        finding = _make_finding()
        pipeline = {"id": 2, "status": "failed"}
        client = _mock_client([[pipeline]])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _verify_fix_pipeline(
                finding, client, "org/repo", 42, timeout_seconds=60
            )

        body = client.create_workitem_note.call_args[0][2]
        assert "❌" in body

    @pytest.mark.asyncio
    async def test_canceled_pipeline_posts_comment(self):
        finding = _make_finding()
        pipeline = {"id": 3, "status": "canceled"}
        client = _mock_client([[pipeline]])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _verify_fix_pipeline(
                finding, client, "org/repo", 42, timeout_seconds=60
            )

        body = client.create_workitem_note.call_args[0][2]
        assert "⚠️" in body or "canceled" in body.lower()

    @pytest.mark.asyncio
    async def test_running_then_success_polls_twice(self):
        finding = _make_finding()
        running = {"id": 4, "status": "running"}
        success = {"id": 4, "status": "success", "web_url": "https://gitlab.com/pipe/4"}
        client = _mock_client([[running], [success]])

        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await _verify_fix_pipeline(
                finding, client, "org/repo", 42, timeout_seconds=120
            )

        assert client.get_mr_pipelines.call_count == 2
        body = client.create_workitem_note.call_args[0][2]
        assert "✅" in body

    @pytest.mark.asyncio
    async def test_empty_pipelines_list_keeps_polling(self):
        finding = _make_finding()
        success = {"id": 5, "status": "success"}
        client = _mock_client([[], [success]])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _verify_fix_pipeline(
                finding, client, "org/repo", 42, timeout_seconds=120
            )

        assert client.get_mr_pipelines.call_count == 2
        assert client.create_workitem_note.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_posts_timeout_comment(self):
        finding = _make_finding()
        running = {"id": 6, "status": "running"}
        # Always returns running — will time out after first poll since timeout=30=poll_interval
        client = _mock_client([[running]] * 10)

        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await _verify_fix_pipeline(
                finding, client, "org/repo", 42, timeout_seconds=30
            )

        # After one poll (elapsed=30 == timeout=30) → loop ends → timeout comment posted
        body = client.create_workitem_note.call_args[0][2]
        assert "⏱" in body or "timed out" in body.lower()

    @pytest.mark.asyncio
    async def test_github_success_conclusion_handled(self):
        finding = _make_finding()
        pipeline = {"id": 7, "status": "completed", "conclusion": "success"}
        client = _mock_client([[pipeline]])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _verify_fix_pipeline(
                finding, client, "org/repo", 42, timeout_seconds=60
            )

        body = client.create_workitem_note.call_args[0][2]
        assert "✅" in body

    @pytest.mark.asyncio
    async def test_github_failure_conclusion_handled(self):
        finding = _make_finding()
        pipeline = {"id": 8, "status": "completed", "conclusion": "failure"}
        client = _mock_client([[pipeline]])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _verify_fix_pipeline(
                finding, client, "org/repo", 42, timeout_seconds=60
            )

        body = client.create_workitem_note.call_args[0][2]
        assert "❌" in body

    @pytest.mark.asyncio
    async def test_poll_exception_continues_and_eventually_succeeds(self):
        finding = _make_finding()
        success = {"id": 9, "status": "success"}
        # First call raises, second call returns success
        client = AsyncMock()
        client.get_mr_pipelines = AsyncMock(side_effect=[Exception("timeout"), [success]])
        client.create_workitem_note = AsyncMock(return_value="ok")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await _verify_fix_pipeline(
                finding, client, "org/repo", 42, timeout_seconds=90
            )

        body = client.create_workitem_note.call_args[0][2]
        assert "✅" in body

    @pytest.mark.asyncio
    async def test_comment_post_exception_does_not_raise(self):
        finding = _make_finding()
        pipeline = {"id": 10, "status": "success"}
        client = AsyncMock()
        client.get_mr_pipelines = AsyncMock(return_value=[pipeline])
        client.create_workitem_note = AsyncMock(side_effect=Exception("network error"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            # Should not raise
            await _verify_fix_pipeline(
                finding, client, "org/repo", 42, timeout_seconds=60
            )
