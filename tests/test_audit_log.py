"""Unit tests for the audit log module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quorum.audit_log import AuditEntry, append_entry, load_entries, _log_path
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
        explanation="All consumers retry at the same instant after leader election.",
    )
    defaults.update(kwargs)
    return Finding(**defaults)


class TestAuditLog:
    def test_append_creates_file(self, tmp_path, monkeypatch):
        log_file = tmp_path / "audit.json"
        monkeypatch.setenv("QUORUM_AUDIT_LOG", str(log_file))
        result = _make_result()
        append_entry(result, platform="github")
        assert log_file.exists()
        raw = json.loads(log_file.read_text())
        assert len(raw) == 1
        assert raw[0]["repo"] == "myorg/myrepo"
        assert raw[0]["pr"] == 42
        assert raw[0]["platform"] == "github"

    def test_append_accumulates_entries(self, tmp_path, monkeypatch):
        log_file = tmp_path / "audit.json"
        monkeypatch.setenv("QUORUM_AUDIT_LOG", str(log_file))
        append_entry(_make_result(mr_iid=1), platform="github")
        append_entry(_make_result(mr_iid=2), platform="gitlab")
        raw = json.loads(log_file.read_text())
        assert len(raw) == 2
        assert raw[0]["pr"] == 1
        assert raw[1]["pr"] == 2

    def test_load_entries_returns_empty_for_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QUORUM_AUDIT_LOG", str(tmp_path / "nonexistent.json"))
        assert load_entries() == []

    def test_entry_captures_finding_fields(self, tmp_path, monkeypatch):
        log_file = tmp_path / "audit.json"
        monkeypatch.setenv("QUORUM_AUDIT_LOG", str(log_file))
        finding = _make_finding(severity=Severity.HIGH, confidence=90, file_path="src/foo.py")
        result = _make_result(findings=[finding])
        entry = append_entry(result, platform="github", comment_posted=True)
        assert entry.high == 1
        assert entry.critical == 0
        assert entry.comment_posted is True
        assert len(entry.findings) == 1
        assert entry.findings[0].rule == "RULE_06"
        assert entry.findings[0].file_path == "src/foo.py"

    def test_pass_findings_excluded_from_audit_findings_list(self, tmp_path, monkeypatch):
        log_file = tmp_path / "audit.json"
        monkeypatch.setenv("QUORUM_AUDIT_LOG", str(log_file))
        pass_finding = _make_finding(severity=Severity.PASS, confidence=100)
        result = _make_result(findings=[pass_finding])
        entry = append_entry(result, platform="github")
        # PASS findings should not appear in the audit finding list (only in passes count)
        assert len(entry.findings) == 0
        assert entry.passes == 1

    def test_env_var_overrides_default_path(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom_audit.json"
        monkeypatch.setenv("QUORUM_AUDIT_LOG", str(custom))
        assert _log_path() == custom
