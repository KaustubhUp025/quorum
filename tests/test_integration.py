"""Integration tests for the full agent review loop.

These tests mock the MCP session and Gemini client so the entire
agent.review() pipeline can be exercised without real API calls.

Coverage:
- Full agent loop: diff fetch → surface detect → Gemini tool-call turns → parse → post comment
- Multi-turn tool calling (Gemini requests semantic_code_search, gets result, then responds)
- Confidence threshold filtering
- CRITICAL finding → blocked=True
- No surfaces → early exit (zero Gemini calls)
- Gemini JSON parse failure → graceful degradation (empty findings, not crash)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from quorum.agent import QuorumAgent, _parse_findings
from quorum.config import Settings
from quorum.formatter import format_comment
from quorum.gitlab_client import GitLabMCPClient
from quorum.models import Finding, ReviewResult, Severity


# ---------------------------------------------------------------------------
# Helpers — fake Gemini response objects
# ---------------------------------------------------------------------------


def _make_function_call(name: str, args: dict) -> MagicMock:
    fc = MagicMock()
    fc.name = name
    fc.args = args
    return fc


def _make_part(*, text: str | None = None, function_call=None) -> MagicMock:
    part = MagicMock()
    part.text = text
    part.function_call = function_call
    return part


def _make_content(parts: list, role: str = "model") -> MagicMock:
    content = MagicMock()
    content.parts = parts
    content.role = role
    return content


def _make_candidate(content) -> MagicMock:
    candidate = MagicMock()
    candidate.content = content
    return candidate


def _make_response(parts: list) -> MagicMock:
    response = MagicMock()
    content = _make_content(parts)
    response.candidates = [_make_candidate(content)]
    return response


def _findings_json(findings: list[dict]) -> str:
    payload = {"surfaces_found": ["lock acquisition in service.py:142"], "findings": findings}
    return f"After investigation:\n\n```json\n{json.dumps(payload)}\n```"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings(
        gemini_api_key="test-key",
        gitlab_token="test-token",
        min_confidence=60,
        block_on_critical=True,
    )


@pytest.fixture
def mock_mcp() -> AsyncMock:
    """A fully mocked GitLabMCPClient."""
    mcp = AsyncMock(spec=GitLabMCPClient)
    mcp.get_merge_request_diffs.return_value = _BAD_DIFF
    mcp.get_merge_request.return_value = '{"title": "Add order lock", "iid": 42}'
    mcp.semantic_code_search.return_value = (
        "Found OrderRepository.save(order) — no if_version parameter in method signature."
    )
    mcp.create_workitem_note.return_value = '{"id": 1001}'
    return mcp


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

_FENCING_FINDING = {
    "rule_id": "RULE_01",
    "rule_name": "Fencing Token Missing",
    "severity": "CRITICAL",
    "confidence": 92,
    "title": "Lock acquired without fencing token",
    "explanation": (
        "The lock value 'locked' is a static string. "
        "Another process can acquire the lock after TTL expiry and OrderRepository.save "
        "has no conditional check to reject a stale write."
    ),
    "diff_snippet": 'self.redis.set(f"order:{order_id}", "locked", nx=True, px=30_000)',
    "search_evidence": "OrderRepository.save(order) — no if_version parameter found",
    "reference": "Kleppmann (2016) — How to do distributed locking",
    "suggested_fix": "Use uuid.uuid4() as the lock value and pass it to save() as if_version.",
    "file_path": "src/order/service.py",
    "line_number": 13,
}


# ---------------------------------------------------------------------------
# Test: full two-turn agent loop (tool call + final answer)
# ---------------------------------------------------------------------------


class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_two_turn_loop_produces_findings(self, settings, mock_mcp):
        """Gemini requests a semantic search, gets the result, then returns findings."""
        turn1_parts = [
            _make_part(function_call=_make_function_call(
                "semantic_code_search",
                {"query": "fencing token returned from lock acquisition"},
            ))
        ]
        turn2_parts = [_make_part(text=_findings_json([_FENCING_FINDING]))]

        gemini_responses = [_make_response(turn1_parts), _make_response(turn2_parts)]

        agent = QuorumAgent(settings)

        with patch.object(agent, "_generate", new=AsyncMock(side_effect=gemini_responses)):
            result = await agent.review(
                project_id="myorg/myrepo",
                mr_iid=42,
                mcp=mock_mcp,
                post_comment=True,
            )

        assert len(result.findings) == 1
        assert result.findings[0].rule_id == "RULE_01"
        assert result.findings[0].severity == Severity.CRITICAL
        assert result.findings[0].confidence == 92
        assert result.blocked is True

        # Verify MCP calls were made
        mock_mcp.get_merge_request_diffs.assert_called_once_with("myorg/myrepo", 42)
        mock_mcp.semantic_code_search.assert_called_once()
        mock_mcp.create_workitem_note.assert_called_once()

        # Verify the comment body contains the finding
        call_args = mock_mcp.create_workitem_note.call_args
        comment_body: str = call_args[0][2] if call_args[0] else call_args[1]["body"]
        assert "RULE_01" in comment_body
        assert "🔴" in comment_body
        assert "Pipeline blocked" in comment_body or "blocked" in comment_body.lower()

    @pytest.mark.asyncio
    async def test_single_turn_no_tool_calls(self, settings, mock_mcp):
        """Gemini returns findings directly without calling any tools."""
        parts = [_make_part(text=_findings_json([_FENCING_FINDING]))]
        agent = QuorumAgent(settings)

        with patch.object(agent, "_generate", new=AsyncMock(return_value=_make_response(parts))):
            result = await agent.review("myorg/myrepo", 42, mock_mcp, post_comment=False)

        assert result.critical_count == 1
        assert result.blocked is True
        mock_mcp.semantic_code_search.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_surfaces_skips_gemini(self, settings):
        """A diff with no coordination surfaces should skip all API calls."""
        mcp = AsyncMock(spec=GitLabMCPClient)
        mcp.get_merge_request_diffs.return_value = (
            "diff --git a/utils.py\n+def add(a, b):\n+    return a + b\n"
        )
        mcp.create_workitem_note.return_value = '{"id": 1}'

        agent = QuorumAgent(settings)

        with patch.object(agent, "_generate", new=AsyncMock()) as mock_generate:
            result = await agent.review("myorg/myrepo", 42, mcp, post_comment=True)

        mock_generate.assert_not_called()
        assert result.findings == []
        assert result.blocked is False
        assert result.surfaces_detected == 0
        # Should still post a "no surfaces found" comment
        mcp.create_workitem_note.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_findings_only_critical_blocks(self, settings, mock_mcp):
        """Only CRITICAL findings should set blocked=True."""
        high_finding = {
            **_FENCING_FINDING,
            "rule_id": "RULE_06",
            "rule_name": "Retry Without Jitter",
            "severity": "HIGH",
            "confidence": 78,
            "title": "Retry uses deterministic backoff",
        }
        parts = [_make_part(text=_findings_json([high_finding]))]
        agent = QuorumAgent(settings)

        with patch.object(agent, "_generate", new=AsyncMock(return_value=_make_response(parts))):
            result = await agent.review("myorg/myrepo", 42, mock_mcp, post_comment=False)

        assert result.high_count == 1
        assert result.critical_count == 0
        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_confidence_threshold_filters_low_confidence(self, mock_mcp):
        """Findings below min_confidence should be filtered out."""
        low_confidence_finding = {
            **_FENCING_FINDING,
            "severity": "HIGH",
            "confidence": 45,  # below default threshold of 60
        }
        parts = [_make_part(text=_findings_json([low_confidence_finding]))]

        settings = Settings(
            gemini_api_key="test",
            gitlab_token="test",
            min_confidence=60,
            block_on_critical=True,
        )
        agent = QuorumAgent(settings)

        with patch.object(agent, "_generate", new=AsyncMock(return_value=_make_response(parts))):
            result = await agent.review("myorg/myrepo", 42, mock_mcp, post_comment=False)

        assert result.findings == []
        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_pass_findings_bypass_confidence_filter(self, mock_mcp):
        """PASS findings should always be included regardless of confidence."""
        pass_finding = {
            **_FENCING_FINDING,
            "severity": "PASS",
            "confidence": 30,  # below threshold — but PASS is exempt
            "title": "Idempotency key received from client",
        }
        parts = [_make_part(text=_findings_json([pass_finding]))]

        settings = Settings(
            gemini_api_key="test",
            gitlab_token="test",
            min_confidence=60,
            block_on_critical=True,
        )
        agent = QuorumAgent(settings)

        with patch.object(agent, "_generate", new=AsyncMock(return_value=_make_response(parts))):
            result = await agent.review("myorg/myrepo", 42, mock_mcp, post_comment=False)

        assert result.pass_count == 1

    @pytest.mark.asyncio
    async def test_malformed_json_response_is_graceful(self, settings, mock_mcp):
        """If Gemini returns unparseable JSON, review completes with empty findings."""
        parts = [_make_part(text="I looked at the code. It seems fine. No issues found.")]
        agent = QuorumAgent(settings)

        with patch.object(agent, "_generate", new=AsyncMock(return_value=_make_response(parts))):
            result = await agent.review("myorg/myrepo", 42, mock_mcp, post_comment=False)

        # Should not raise — should return empty findings
        assert result.findings == []
        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_block_on_critical_false_does_not_block(self, mock_mcp):
        """When block_on_critical=False, CRITICAL findings don't set blocked."""
        settings = Settings(
            gemini_api_key="test",
            gitlab_token="test",
            min_confidence=0,
            block_on_critical=False,
        )
        parts = [_make_part(text=_findings_json([_FENCING_FINDING]))]
        agent = QuorumAgent(settings)

        with patch.object(agent, "_generate", new=AsyncMock(return_value=_make_response(parts))):
            result = await agent.review("myorg/myrepo", 42, mock_mcp, post_comment=False)

        assert result.critical_count == 1
        assert result.blocked is False


# ---------------------------------------------------------------------------
# Test: _parse_findings unit
# ---------------------------------------------------------------------------


class TestParseFindings:
    def test_parses_valid_findings(self):
        findings = _parse_findings({"findings": [_FENCING_FINDING]})
        assert len(findings) == 1
        f = findings[0]
        assert f.rule_id == "RULE_01"
        assert f.severity == Severity.CRITICAL
        assert f.confidence == 92

    def test_handles_empty_findings_list(self):
        assert _parse_findings({"findings": []}) == []

    def test_skips_malformed_items(self):
        raw = {
            "findings": [
                _FENCING_FINDING,
                {"severity": "INVALID_SEVERITY", "rule_id": "RULE_X"},  # bad severity
            ]
        }
        findings = _parse_findings(raw)
        # First item should parse fine; second should be skipped
        assert len(findings) == 1
        assert findings[0].rule_id == "RULE_01"

    def test_handles_missing_optional_fields(self):
        minimal = {
            "rule_id": "RULE_06",
            "rule_name": "Retry",
            "severity": "HIGH",
            "confidence": 75,
            "title": "No jitter",
            "explanation": "Deterministic backoff.",
        }
        findings = _parse_findings({"findings": [minimal]})
        assert len(findings) == 1
        f = findings[0]
        assert f.diff_snippet is None
        assert f.search_evidence is None
        assert f.file_path is None


# ---------------------------------------------------------------------------
# Test: ReviewResult computed properties
# ---------------------------------------------------------------------------


class TestReviewResult:
    def _make_findings(self, severities: list[Severity]) -> list[Finding]:
        return [
            Finding(
                rule_id=f"RULE_{i:02d}",
                rule_name=f"Rule {i}",
                severity=s,
                confidence=80,
                title=f"Finding {i}",
                explanation="x",
            )
            for i, s in enumerate(severities, 1)
        ]

    def test_counts_are_correct(self):
        findings = self._make_findings([
            Severity.CRITICAL, Severity.CRITICAL,
            Severity.HIGH,
            Severity.MEDIUM,
            Severity.PASS,
        ])
        result = ReviewResult(mr_iid=1, project_id="p", findings=findings)
        assert result.critical_count == 2
        assert result.high_count == 1
        assert result.medium_count == 1
        assert result.pass_count == 1

    def test_empty_findings_all_zero(self):
        result = ReviewResult(mr_iid=1, project_id="p")
        assert result.critical_count == 0
        assert result.blocked is False
