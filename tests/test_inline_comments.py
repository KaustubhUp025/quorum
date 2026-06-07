"""Tests for Phase B — inline MR comments via create_mr_discussion."""

from __future__ import annotations

import pytest

from quorum.formatter import ReportFormatterAgent, _format_summary_comment, _inline_comment_body
from quorum.models import Finding, ReviewResult, Severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding(
    rule_id: str = "RULE_06",
    severity: Severity = Severity.HIGH,
    file_path: str | None = "src/service.py",
    line_number: int | None = 42,
    confidence: int = 95,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        rule_name="Retry Backoff Missing Jitter",
        title="Retry Backoff Missing Jitter",
        severity=severity,
        confidence=confidence,
        explanation="Deterministic backoff causes thundering herd.",
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


def _diff_refs(has_shas: bool = True) -> dict:
    if has_shas:
        return {"base_sha": "abc", "head_sha": "def", "start_sha": "abc"}
    return {"base_sha": "", "head_sha": "", "start_sha": ""}


# ---------------------------------------------------------------------------
# Unit tests — _inline_comment_body
# ---------------------------------------------------------------------------

def test_inline_comment_body_contains_rule_and_severity():
    f = _finding()
    body = _inline_comment_body(f)
    assert "RULE_06" in body
    assert "HIGH" in body
    assert "95%" in body


def test_inline_comment_body_contains_quorum_attribution():
    body = _inline_comment_body(_finding())
    assert "Quorum" in body


def test_inline_comment_body_includes_explanation():
    body = _inline_comment_body(_finding())
    assert "thundering herd" in body


def test_inline_comment_body_includes_fix_mr_link():
    f = _finding()
    f.fix_mr_url = "https://gitlab.com/org/repo/-/merge_requests/5"
    f.fix_mr_iid = 5
    body = _inline_comment_body(f)
    assert "MR !5" in body


# ---------------------------------------------------------------------------
# Unit tests — _format_summary_comment
# ---------------------------------------------------------------------------

def test_summary_contains_findings_table():
    f = _finding()
    result = _result([f])
    priority_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.PASS]
    sorted_f = sorted(result.findings, key=lambda x: priority_order.index(x.severity))
    pass_f = [x for x in sorted_f if x.severity == Severity.PASS]
    summary = _format_summary_comment(result, set(), sorted_f, pass_f)
    assert "RULE_06" in summary
    assert "HIGH" in summary


def test_summary_marks_inline_posted_findings():
    f = _finding()
    result = _result([f])
    priority_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.PASS]
    sorted_f = sorted(result.findings, key=lambda x: priority_order.index(x.severity))
    pass_f = []
    summary = _format_summary_comment(result, {"RULE_06"}, sorted_f, pass_f)
    assert "↗" in summary
    assert "inline diff comment" in summary


def test_summary_does_not_repeat_full_block_for_inline_findings():
    f = _finding()
    result = _result([f])
    priority_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.PASS]
    sorted_f = sorted(result.findings, key=lambda x: priority_order.index(x.severity))
    pass_f = []
    # When finding is posted inline, full block must not appear in summary
    summary = _format_summary_comment(result, {"RULE_06"}, sorted_f, pass_f)
    # full block has "### 🟠 **HIGH** — RULE_06:" header
    assert "### 🟠 **HIGH** — RULE_06:" not in summary


def test_summary_includes_full_block_for_non_inline_findings():
    f = _finding(file_path=None, line_number=None)
    result = _result([f])
    priority_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.PASS]
    sorted_f = sorted(result.findings, key=lambda x: priority_order.index(x.severity))
    pass_f = []
    summary = _format_summary_comment(result, set(), sorted_f, pass_f)
    # full block rendered since no inline comment was posted
    assert "### 🟠 **HIGH** — RULE_06:" in summary


def test_summary_passes_in_collapsible_section():
    pass_f = _finding(rule_id="RULE_07", severity=Severity.PASS)
    result = _result([pass_f])
    priority_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.PASS]
    sorted_f = sorted(result.findings, key=lambda x: priority_order.index(x.severity))
    summary = _format_summary_comment(result, set(), sorted_f, [pass_f])
    assert "<details>" in summary
    assert "Passed checks" in summary
    assert "RULE_07" in summary


# ---------------------------------------------------------------------------
# Integration tests — post_review() method (async, mocked client)
# ---------------------------------------------------------------------------

class _MockClient:
    def __init__(self):
        self.inline_calls: list[dict] = []
        self.note_calls: list[str] = []

    async def create_mr_discussion(self, project_id, mr_iid, body,
                                   file_path=None, line_number=None, diff_refs=None):
        self.inline_calls.append({
            "body": body, "file_path": file_path,
            "line_number": line_number, "diff_refs": diff_refs,
        })
        return '{"id": "disc-1"}'

    async def create_workitem_note(self, project_id, mr_iid, body, note_type="MergeRequest"):
        self.note_calls.append(body)
        return '{"id": 999}'


@pytest.mark.asyncio
async def test_post_review_posts_inline_for_located_findings():
    client = _MockClient()
    f = _finding()
    result = _result([f])
    mr_meta = _diff_refs()
    await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)
    assert len(client.inline_calls) == 1
    assert client.inline_calls[0]["file_path"] == "src/service.py"
    assert client.inline_calls[0]["line_number"] == 42


@pytest.mark.asyncio
async def test_post_review_always_posts_summary_note():
    client = _MockClient()
    f = _finding()
    result = _result([f])
    mr_meta = _diff_refs()
    await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)
    assert len(client.note_calls) == 1
    assert "Quorum · Distributed Coordination Review" in client.note_calls[0]


@pytest.mark.asyncio
async def test_post_review_no_inline_when_no_diff_refs():
    client = _MockClient()
    f = _finding()
    result = _result([f])
    mr_meta = _diff_refs(has_shas=False)
    await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)
    # No SHAs → no inline calls, just the summary note
    assert len(client.inline_calls) == 0
    assert len(client.note_calls) == 1


@pytest.mark.asyncio
async def test_post_review_no_inline_when_finding_has_no_location():
    client = _MockClient()
    f = _finding(file_path=None, line_number=None)
    result = _result([f])
    mr_meta = _diff_refs()
    await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)
    assert len(client.inline_calls) == 0
    assert len(client.note_calls) == 1


@pytest.mark.asyncio
async def test_post_review_pass_findings_not_posted_inline():
    client = _MockClient()
    findings = [
        _finding(rule_id="RULE_06", severity=Severity.HIGH),
        _finding(rule_id="RULE_07", severity=Severity.PASS),
    ]
    result = _result(findings)
    mr_meta = _diff_refs()
    await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)
    # Only 1 inline (RULE_06); RULE_07 PASS never gets inline
    assert len(client.inline_calls) == 1
    assert all("RULE_06" in c["body"] for c in client.inline_calls)


@pytest.mark.asyncio
async def test_post_review_multiple_findings():
    client = _MockClient()
    findings = [
        _finding(rule_id="RULE_01", severity=Severity.CRITICAL, file_path="lock.py", line_number=10),
        _finding(rule_id="RULE_06", severity=Severity.HIGH, file_path="retry.py", line_number=55),
        _finding(rule_id="RULE_07", severity=Severity.PASS, file_path=None, line_number=None),
    ]
    result = _result(findings)
    mr_meta = _diff_refs()
    await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)
    assert len(client.inline_calls) == 2
    assert len(client.note_calls) == 1
    summary = client.note_calls[0]
    assert "↗" in summary  # both findings marked as inline in table


@pytest.mark.asyncio
async def test_post_review_empty_findings_posts_summary_only():
    client = _MockClient()
    result = ReviewResult(
        mr_iid=1,
        project_id="org/repo",
        findings=[],
        surfaces_detected=0,
        rules_checked=0,
        blocked=False,
    )
    mr_meta = _diff_refs()
    await ReportFormatterAgent().post_review(result, client, "org/repo", 1, mr_meta)
    assert len(client.inline_calls) == 0
    assert len(client.note_calls) == 1
    assert "No issues found" in client.note_calls[0]
