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


class ReviewResult(BaseModel):
    mr_iid: int
    project_id: str
    findings: list[Finding] = Field(default_factory=list)
    surfaces_detected: int = 0
    rules_checked: int = 0
    blocked: bool = False

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
