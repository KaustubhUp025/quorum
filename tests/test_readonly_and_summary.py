"""Tests for two robustness fixes surfaced by the 3-mode consistency comparison:

1. The forced final-summary turn must still yield findings when the first
   attempt returns no usable text (the bug that made the webhook return 0
   findings while the CLI correctly found a CRITICAL).
2. A 401/403 (no write access) on comment posting must fall back to a local
   report instead of crashing or silently losing the findings.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from quorum.agent import DeepReasoningAgent, _is_forbidden
from quorum.config import Settings
from quorum.models import Finding, ReviewResult, Severity


def _make_agent(**overrides):
    settings = Settings(
        gemini_api_key="test-key",
        gitlab_token="test-token",
        llm_backend="gemini/gemini-2.5-pro",
        **overrides,
    )
    with patch("quorum.agent._make_gemini_client"), \
         patch("quorum.agent._init_context_cache", return_value=None):
        return DeepReasoningAgent(settings)


# --- mock google-genai response shapes (loop only reads attributes) ----------

def _fc_response(name: str):
    part = SimpleNamespace(function_call=SimpleNamespace(name=name, args={}),
                           text=None, thought=False)
    cand = SimpleNamespace(content=SimpleNamespace(parts=[part]), finish_reason="STOP")
    return SimpleNamespace(candidates=[cand])


def _text_response(text: str):
    part = SimpleNamespace(function_call=None, text=text, thought=False)
    cand = SimpleNamespace(content=SimpleNamespace(parts=[part]), finish_reason="STOP")
    return SimpleNamespace(candidates=[cand])


def _empty_response():
    # A thought-only / textless turn: no usable text parts.
    cand = SimpleNamespace(content=SimpleNamespace(parts=[]), finish_reason="STOP")
    return SimpleNamespace(candidates=[cand])


class TestSummaryRetry:
    """Bug 1 — forced summary must recover findings instead of returning empty."""

    @pytest.mark.asyncio
    async def test_empty_summary_is_retried_and_returns_findings(self):
        agent = _make_agent(max_tool_rounds=1)
        json_findings = '{"findings": [{"rule_id": "RULE_09", "severity": "CRITICAL", "confidence": 100, "title": "Outbox missing"}]}'

        # Round 0 makes a tool call (exhausts the single round), the forced
        # summary comes back empty, and the strict retry returns the JSON.
        agent._generate = AsyncMock(side_effect=[
            _fc_response("get_merge_request"),
            _empty_response(),
            _text_response(json_findings),
        ])
        agent._run_tool = AsyncMock(return_value="<tool result>")

        out = await agent._agent_loop("review this", AsyncMock(), "proj", 1)

        assert "RULE_09" in out
        # in-loop generate (1) + summary (1) + retry (1) = 3 calls
        assert agent._generate.await_count == 3
        # the summary calls must run in summary mode (thinking capped, no tools)
        assert agent._generate.await_args_list[1].kwargs.get("summary") is True
        assert agent._generate.await_args_list[2].kwargs.get("summary") is True

    @pytest.mark.asyncio
    async def test_summary_succeeds_first_try_no_retry(self):
        agent = _make_agent(max_tool_rounds=1)
        json_findings = '{"findings": []}'
        agent._generate = AsyncMock(side_effect=[
            _fc_response("get_merge_request"),
            _text_response(json_findings),
        ])
        agent._run_tool = AsyncMock(return_value="<tool result>")

        out = await agent._agent_loop("review this", AsyncMock(), "proj", 1)

        assert out == json_findings
        assert agent._generate.await_count == 2  # no retry needed


class TestIsForbidden:
    """Bug 2 building block — recognise 401/403 from posting failures."""

    def test_403_is_forbidden(self):
        req = httpx.Request("POST", "https://gitlab.com")
        err = httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, request=req))
        assert _is_forbidden(err) is True

    def test_401_is_forbidden(self):
        req = httpx.Request("POST", "https://gitlab.com")
        err = httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))
        assert _is_forbidden(err) is True

    def test_500_is_not_forbidden(self):
        req = httpx.Request("POST", "https://gitlab.com")
        err = httpx.HTTPStatusError("500", request=req, response=httpx.Response(500, request=req))
        assert _is_forbidden(err) is False

    def test_plain_exception_is_not_forbidden(self):
        assert _is_forbidden(ValueError("boom")) is False


class TestLocalReportFallback:
    """Bug 2 — the read-only writer and the review() 403 fallback."""

    def test_write_local_report_creates_md_and_sarif(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent = _make_agent()
        result = ReviewResult(
            mr_iid=16,
            project_id="some-org/some-repo",
            findings=[Finding(
                rule_id="RULE_09", rule_name="Transactional Outbox Missing",
                severity=Severity.CRITICAL, confidence=100,
                title="DB write + publish in one tx", explanation="dual write",
                file_path="src/Foo.java",
            )],
            blocked=True,
        )
        path = agent._write_local_report(result, "some-org/some-repo", 16)
        assert path is not None
        md = tmp_path / "quorum-report-some-org-some-repo-16.md"
        sarif = tmp_path / "quorum-report-some-org-some-repo-16.sarif"
        assert md.exists() and sarif.exists()
        assert "RULE_09" in md.read_text()
        assert "RULE_09" in sarif.read_text()

    @pytest.mark.asyncio
    async def test_review_403_on_post_falls_back_to_local_report(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent = _make_agent()

        # Findings come back parsed; posting then fails with 403.
        agent._agent_loop = AsyncMock(return_value=(
            '{"findings": [{"rule_id": "RULE_01", "rule_name": "Fencing Token Missing", '
            '"severity": "CRITICAL", "confidence": 95, "title": "no fence", '
            '"explanation": "lock without token", "file_path": "a.py"}]}'
        ))
        agent._enrich_with_citations = AsyncMock(side_effect=lambda f: f)

        client = AsyncMock()
        client.get_merge_request_diffs.return_value = "+ redis.setnx('lock', '1')\n"
        client.get_mr_metadata.return_value = {"source_branch": "feat", "target_branch": "main"}
        client.get_project_languages.return_value = {}
        client.get_project_permissions.return_value = {"access_level": 30, "can_write": True}

        req = httpx.Request("POST", "https://gitlab.com")
        forbidden = httpx.HTTPStatusError("403", request=req, response=httpx.Response(403, request=req))

        with patch("quorum.agent.ReportFormatterAgent.post_review",
                   new=AsyncMock(side_effect=forbidden)):
            result = await agent.review("some-org/some-repo", 16, client,
                                        post_comment=True, force=True)

        assert result.delivery == "read_only"
        assert result.report_path is not None
        assert (tmp_path / result.report_path).exists()
        assert result.critical_count == 1  # findings were NOT lost

    @pytest.mark.asyncio
    async def test_review_readonly_preflight_skips_post(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        agent = _make_agent()
        agent._agent_loop = AsyncMock(return_value=(
            '{"findings": [{"rule_id": "RULE_01", "rule_name": "Fencing Token Missing", '
            '"severity": "CRITICAL", "confidence": 95, "title": "no fence", '
            '"explanation": "lock without token", "file_path": "a.py"}]}'
        ))
        agent._enrich_with_citations = AsyncMock(side_effect=lambda f: f)

        client = AsyncMock()
        client.get_merge_request_diffs.return_value = "+ redis.setnx('lock', '1')\n"
        client.get_mr_metadata.return_value = {"source_branch": "feat", "target_branch": "main"}
        client.get_project_languages.return_value = {}
        # read-only: pre-flight reports no write access
        client.get_project_permissions.return_value = {"access_level": 0, "can_write": False}

        with patch("quorum.agent.ReportFormatterAgent.post_review",
                   new=AsyncMock()) as mock_post:
            result = await agent.review("some-org/some-repo", 16, client,
                                        post_comment=True, force=True)

        assert result.delivery == "read_only"
        mock_post.assert_not_awaited()  # never even attempted the post
        assert (tmp_path / result.report_path).exists()

    @pytest.mark.asyncio
    async def test_no_surfaces_readonly_does_not_post(self):
        # The no-surfaces early-return path must ALSO respect read-only access
        # (this was the gap: it posted to third-party MRs regardless).
        agent = _make_agent()
        client = AsyncMock()
        client.get_merge_request_diffs.return_value = "+ x = 1\n+ print('hello')\n"  # no surfaces
        client.get_mr_metadata.return_value = {"source_branch": "f", "target_branch": "main"}
        client.get_project_languages.return_value = {}
        client.get_project_permissions.return_value = {"access_level": 0, "can_write": False}

        result = await agent.review("some-org/some-repo", 16, client,
                                    post_comment=True, force=True)

        assert result.surfaces_detected == 0
        assert result.delivery == "read_only"
        client.create_workitem_note.assert_not_awaited()  # no spam on a read-only repo

    @pytest.mark.asyncio
    async def test_no_surfaces_writable_posts(self):
        agent = _make_agent()
        client = AsyncMock()
        client.get_merge_request_diffs.return_value = "+ x = 1\n+ print('hello')\n"
        client.get_mr_metadata.return_value = {"source_branch": "f", "target_branch": "main"}
        client.get_project_languages.return_value = {}
        client.get_project_permissions.return_value = {"access_level": 40, "can_write": True}

        result = await agent.review("my-org/my-repo", 16, client,
                                    post_comment=True, force=True)

        assert result.surfaces_detected == 0
        assert result.delivery == "posted"
        client.create_workitem_note.assert_awaited_once()
