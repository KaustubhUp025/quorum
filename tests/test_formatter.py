"""Unit tests for the MR comment formatter."""

from quorum.formatter import format_comment
from quorum.models import Finding, ReviewResult, Severity


def _make_result(**kwargs) -> ReviewResult:
    defaults = dict(mr_iid=42, project_id="myorg/myrepo", blocked=False)
    defaults.update(kwargs)
    return ReviewResult(**defaults)


class TestFormatter:
    def test_empty_findings_produces_pass_comment(self):
        result = _make_result(surfaces_detected=2, rules_checked=2)
        body = format_comment(result)
        assert "Quorum" in body
        assert "No issues found" in body or "All coordination surfaces" in body

    def test_critical_finding_in_comment(self):
        finding = Finding(
            rule_id="RULE_01",
            rule_name="Fencing Token Missing",
            severity=Severity.CRITICAL,
            confidence=91,
            title="Lock acquired without fencing token",
            explanation="Static lock value allows stale writes after lease expiry.",
            diff_snippet='client.set(key, "locked", nx=True)',
            reference="Kleppmann (2016)",
        )
        result = _make_result(
            findings=[finding], surfaces_detected=1, rules_checked=1, blocked=True
        )
        body = format_comment(result)
        assert "🔴" in body
        assert "RULE_01" in body
        assert "91%" in body
        assert "Pipeline blocked" in body or "blocked" in body.lower()
        assert "Kleppmann" in body

    def test_pass_finding_renders_green(self):
        finding = Finding(
            rule_id="RULE_04",
            rule_name="Idempotency Key",
            severity=Severity.PASS,
            confidence=95,
            title="Idempotency key received from client",
            explanation="Key is taken from request header; deduplication check precedes write.",
        )
        result = _make_result(findings=[finding], surfaces_detected=1, rules_checked=1)
        body = format_comment(result)
        assert "🟢" in body
        assert "PASS" in body

    def test_findings_ordered_by_severity(self):
        findings = [
            Finding(rule_id="RULE_06", rule_name="Retry", severity=Severity.HIGH,
                    confidence=80, title="No jitter", explanation="x"),
            Finding(rule_id="RULE_01", rule_name="Fencing", severity=Severity.CRITICAL,
                    confidence=92, title="No fencing", explanation="x"),
            Finding(rule_id="RULE_07", rule_name="Sleep", severity=Severity.MEDIUM,
                    confidence=70, title="Sleep in test", explanation="x"),
        ]
        result = _make_result(findings=findings, surfaces_detected=3, rules_checked=3)
        body = format_comment(result)
        # CRITICAL should appear before HIGH, which should appear before MEDIUM
        pos_critical = body.index("CRITICAL")
        pos_high = body.index("HIGH")
        pos_medium = body.index("MEDIUM")
        assert pos_critical < pos_high < pos_medium

    def test_footer_contains_project_links(self):
        result = _make_result()
        body = format_comment(result)
        assert "Quorum" in body
        assert "Gemini" in body
