"""Format a ReviewResult as a GitLab-flavoured Markdown MR comment."""

from __future__ import annotations

from quorum.models import Finding, ReviewResult, Severity

_SEP = "\n\n---\n\n"


def _finding_block(f: Finding) -> str:
    lines: list[str] = []

    header = f"{f.severity.emoji} **{f.severity.value}** — {f.rule_id}: {f.title}"
    meta = f"**Confidence: {f.confidence}%**"
    if f.file_path:
        loc = f"`{f.file_path}`" + (f":{f.line_number}" if f.line_number else "")
        meta += f" | {loc}"
    lines.append(f"### {header}\n{meta}")
    lines.append(f.explanation)

    if f.diff_snippet:
        lines.append(f"**In your diff:**\n```\n{f.diff_snippet.strip()}\n```")

    if f.search_evidence:
        lines.append(f"**Found via semantic search:**\n```\n{f.search_evidence.strip()}\n```")

    if f.suggested_fix:
        lines.append(f"**Suggested fix:** {f.suggested_fix}")

    if f.reference:
        lines.append(f"**Reference:** {f.reference}")

    return "\n\n".join(lines)


def format_comment(result: ReviewResult) -> str:
    """Produce the full Markdown body for the MR comment."""
    lines: list[str] = []

    # Header
    lines.append("## Quorum · Distributed Coordination Review")

    if not result.findings:
        lines.append(
            "> ✅ All coordination surfaces checked. No issues found.\n\n"
            f"Scanned **{result.surfaces_detected}** coordination surface(s), "
            f"checked **{result.rules_checked}** rule(s)."
        )
        lines.append(_footer())
        return "\n\n".join(lines)

    # Summary line
    parts = []
    if result.critical_count:
        parts.append(f"**{result.critical_count} critical**")
    if result.high_count:
        parts.append(f"**{result.high_count} high**")
    if result.medium_count:
        parts.append(f"{result.medium_count} medium")
    if result.low_count:
        parts.append(f"{result.low_count} low")
    if result.pass_count:
        parts.append(f"{result.pass_count} pass")

    summary = ", ".join(parts) if parts else "no issues"
    lines.append(
        f"> Scanned **{result.surfaces_detected}** coordination surface(s) · "
        f"{summary} · checked {result.rules_checked} rule(s)"
    )

    if result.blocked:
        lines.append(
            "> ⛔ **Pipeline blocked** — resolve 🔴 CRITICAL findings before merging."
        )

    lines.append("---")

    # Findings — CRITICAL and HIGH first, then the rest
    priority_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.PASS]
    sorted_findings = sorted(
        result.findings, key=lambda f: priority_order.index(f.severity)
    )

    for finding in sorted_findings:
        lines.append(_finding_block(finding))
        lines.append("---")

    lines.append(_footer())
    return "\n\n".join(lines)


def _footer() -> str:
    return (
        "*Powered by [Quorum](https://github.com/yourusername/quorum) · "
        "Gemini 2.5 Pro + GitLab MCP · "
        "[Docs](docs/RULES.md) · "
        "[Add a rule](docs/CONTRIBUTING.md)*"
    )
