"""Pydantic data models shared across the Quorum codebase."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    PASS = "PASS"

    @property
    def emoji(self) -> str:
        return {
            Severity.CRITICAL: "🔴",
            Severity.HIGH: "🟠",
            Severity.MEDIUM: "🟡",
            Severity.LOW: "🔵",
            Severity.PASS: "🟢",
        }[self]

    @property
    def is_blocking(self) -> bool:
        return self in (Severity.CRITICAL,)


class Finding(BaseModel):
    rule_id: str = Field(description="e.g. RULE_01")
    rule_name: str
    severity: Severity
    confidence: int = Field(ge=0, le=100)
    title: str
    explanation: str
    diff_snippet: str | None = None
    search_evidence: str | None = None
    reference: str | None = None
    suggested_fix: str | None = None
    file_path: str | None = None
    line_number: int | None = None
    # Populated after fix MR creation (when QUORUM_CREATE_FIX_MRS=true)
    fix_mr_url: str | None = None
    fix_mr_iid: int | None = None


class ReviewResult(BaseModel):
    mr_iid: int
    project_id: str
    findings: list[Finding] = Field(default_factory=list)
    surfaces_detected: int = 0
    rules_checked: int = 0
    blocked: bool = False
    # Populated when QUORUM_CORRELATE_CI=true and a failing pipeline is found
    ci_correlation: str | None = None
    # How the findings were delivered: "posted" (comment on the MR/PR),
    # "read_only" (no write access → written to a local report instead), or
    # "skipped" (post_comment was False, e.g. dry-run).
    delivery: str = "posted"
    # Path to the local Markdown report when delivery == "read_only".
    report_path: str | None = None

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.HIGH)

    @property
    def medium_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.MEDIUM)

    @property
    def low_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.LOW)

    @property
    def pass_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.PASS)


class MCPToolCall(BaseModel):
    name: str
    arguments: dict


class MCPToolResult(BaseModel):
    name: str
    content: str
    is_error: bool = False
