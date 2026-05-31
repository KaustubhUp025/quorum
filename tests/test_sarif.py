"""Unit tests for SARIF 2.1.0 output formatter."""

from __future__ import annotations

import json

import pytest

from quorum.models import Finding, ReviewResult, Severity
from quorum.sarif import format_sarif, _rule_name_identifier


def _finding(
    rule_id: str = "RULE_01",
    severity: Severity = Severity.CRITICAL,
    file_path: str | None = "src/app.py",
    line_number: int | None = 10,
    fix_mr_url: str | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        rule_name="Fencing Token Missing",
        severity=severity,
        confidence=100,
        title="No fencing token",
        explanation="Static lock value used — no fencing token.",
        file_path=file_path,
        line_number=line_number,
        fix_mr_url=fix_mr_url,
    )


def _result(findings: list[Finding]) -> ReviewResult:
    return ReviewResult(
        mr_iid=1,
        project_id="owner/repo",
        findings=findings,
        surfaces_detected=len(findings),
        blocked=any(f.severity == Severity.CRITICAL for f in findings),
    )


class TestSarifStructure:
    def test_top_level_schema_and_version(self):
        sarif = json.loads(format_sarif(_result([_finding()])))
        assert sarif["version"] == "2.1.0"
        assert "sarif-2.1.0" in sarif["$schema"]

    def test_single_run_with_tool_driver(self):
        sarif = json.loads(format_sarif(_result([_finding()])))
        assert len(sarif["runs"]) == 1
        driver = sarif["runs"][0]["tool"]["driver"]
        assert driver["name"] == "Quorum"
        assert "version" in driver
        assert "informationUri" in driver

    def test_run_properties_populated(self):
        sarif = json.loads(format_sarif(_result([_finding()])))
        props = sarif["runs"][0]["properties"]
        assert props["mrIid"] == 1
        assert props["projectId"] == "owner/repo"

    def test_rule_appears_in_driver_rules(self):
        sarif = json.loads(format_sarif(_result([_finding("RULE_01")])))
        rules = sarif["runs"][0]["tool"]["driver"]["rules"]
        assert any(r["id"] == "RULE_01" for r in rules)


class TestSarifSeverityMapping:
    def test_critical_maps_to_error(self):
        sarif = json.loads(format_sarif(_result([_finding(severity=Severity.CRITICAL)])))
        assert sarif["runs"][0]["results"][0]["level"] == "error"

    def test_high_maps_to_warning(self):
        sarif = json.loads(format_sarif(_result([_finding(severity=Severity.HIGH)])))
        assert sarif["runs"][0]["results"][0]["level"] == "warning"

    def test_medium_maps_to_note(self):
        sarif = json.loads(format_sarif(_result([_finding(severity=Severity.MEDIUM)])))
        assert sarif["runs"][0]["results"][0]["level"] == "note"

    def test_low_maps_to_note(self):
        sarif = json.loads(format_sarif(_result([_finding(severity=Severity.LOW)])))
        assert sarif["runs"][0]["results"][0]["level"] == "note"

    def test_pass_findings_omitted(self):
        sarif = json.loads(format_sarif(_result([_finding(severity=Severity.PASS)])))
        assert sarif["runs"][0]["results"] == []

    def test_pass_finding_not_in_rules(self):
        sarif = json.loads(format_sarif(_result([_finding(severity=Severity.PASS)])))
        # PASS rule not included since no result references it
        assert sarif["runs"][0]["tool"]["driver"]["rules"] == []


class TestSarifLocation:
    def test_file_path_and_line_number_populated(self):
        sarif = json.loads(format_sarif(_result([_finding(file_path="src/lock.py", line_number=42)])))
        loc = sarif["runs"][0]["results"][0]["locations"][0]
        assert loc["physicalLocation"]["artifactLocation"]["uri"] == "src/lock.py"
        assert loc["physicalLocation"]["region"]["startLine"] == 42

    def test_line_number_defaults_to_1_when_none(self):
        sarif = json.loads(format_sarif(_result([_finding(file_path="src/app.py", line_number=None)])))
        region = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
        assert region["startLine"] == 1

    def test_no_location_when_file_path_is_none(self):
        sarif = json.loads(format_sarif(_result([_finding(file_path=None)])))
        result = sarif["runs"][0]["results"][0]
        assert "locations" not in result or result.get("locations") == []

    def test_uri_base_id_set(self):
        sarif = json.loads(format_sarif(_result([_finding()])))
        uri_base = sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uriBaseId"]
        assert uri_base == "%SRCROOT%"


class TestSarifFixLink:
    def test_fix_mr_url_appears_in_fixes(self):
        f = _finding(fix_mr_url="https://gitlab.com/owner/repo/-/merge_requests/8")
        sarif = json.loads(format_sarif(_result([f])))
        fixes = sarif["runs"][0]["results"][0].get("fixes", [])
        assert len(fixes) == 1
        assert "merge_requests/8" in fixes[0]["description"]["text"]

    def test_no_fixes_when_no_fix_mr(self):
        sarif = json.loads(format_sarif(_result([_finding(fix_mr_url=None)])))
        result = sarif["runs"][0]["results"][0]
        assert "fixes" not in result


class TestSarifMultipleFindings:
    def test_multiple_findings_all_included(self):
        findings = [
            _finding("RULE_01", Severity.CRITICAL),
            _finding("RULE_06", Severity.HIGH),
            _finding("RULE_08", Severity.HIGH),
        ]
        findings[1].rule_name = "Retry Backoff Jitter Missing"
        findings[2].rule_name = "Kafka Auto-Commit Enabled"
        sarif = json.loads(format_sarif(_result(findings)))
        assert len(sarif["runs"][0]["results"]) == 3

    def test_duplicate_rules_appear_once_in_driver(self):
        findings = [
            _finding("RULE_01", Severity.CRITICAL),
            _finding("RULE_01", Severity.CRITICAL),
        ]
        sarif = json.loads(format_sarif(_result(findings)))
        rules = sarif["runs"][0]["tool"]["driver"]["rules"]
        rule_ids = [r["id"] for r in rules]
        assert rule_ids.count("RULE_01") == 1

    def test_mixed_pass_and_findings(self):
        findings = [
            _finding("RULE_01", Severity.CRITICAL),
            _finding("RULE_07", Severity.PASS),
        ]
        sarif = json.loads(format_sarif(_result(findings)))
        assert len(sarif["runs"][0]["results"]) == 1
        assert sarif["runs"][0]["results"][0]["ruleId"] == "RULE_01"


class TestRuleNameIdentifier:
    def test_spaces_removed_and_capitalised(self):
        assert _rule_name_identifier("Fencing Token Missing") == "FencingTokenMissing"

    def test_slashes_treated_as_word_separators(self):
        assert _rule_name_identifier("SELECT/UPDATE") == "SelectUpdate"
