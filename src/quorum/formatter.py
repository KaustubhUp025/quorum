"""Stage 3 of the Quorum agent pipeline — formats and renders review results.

ReportFormatterAgent converts a ReviewResult into a structured Markdown MR comment.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from quorum.models import Finding, ReviewResult, Severity

if TYPE_CHECKING:
    pass

_SEP = "\n\n---\n\n"


class ReportFormatterAgent:
    """Stage 3 — renders a ReviewResult as a structured Markdown comment.

    Produces the final human-readable output posted to the MR/PR. Handles
    severity ordering, fix MR links, CI correlation notes, and the footer.
    """

    def format(self, result: ReviewResult) -> str:
        """Return the full Markdown body for the MR comment (single top-level post)."""
        return format_comment(result)

    async def post_review(
        self,
        result: ReviewResult,
        client: Any,
        project_id: str,
        mr_iid: int,
        mr_meta: dict,
    ) -> None:
        """Post inline finding comments + one top-level summary.

        For each non-PASS finding that has a file_path and line_number, an inline
        discussion thread is posted on the exact diff line. A compact summary table
        is always posted as a top-level note so the full review is visible in one
        place even if inline comments fail or are unsupported.
        """
        diff_refs = {
            "base_sha": mr_meta.get("base_sha", ""),
            "head_sha": mr_meta.get("head_sha", ""),
            "start_sha": mr_meta.get("start_sha", ""),
        }
        has_diff_refs = bool(
            diff_refs["head_sha"]
            and (diff_refs["base_sha"] or diff_refs["start_sha"])
        )

        priority_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.PASS]
        sorted_findings = sorted(
            result.findings, key=lambda f: priority_order.index(f.severity)
        )

        # Track which findings got an inline comment (to mark them in summary)
        inline_posted: set[str] = set()

        non_pass = [f for f in sorted_findings if f.severity != Severity.PASS]
        pass_findings = [f for f in sorted_findings if f.severity == Severity.PASS]

        for finding in non_pass:
            if finding.file_path and finding.line_number and has_diff_refs:
                body = _inline_comment_body(finding)
                await client.create_mr_discussion(
                    project_id,
                    mr_iid,
                    body,
                    file_path=finding.file_path,
                    line_number=finding.line_number,
                    diff_refs=diff_refs,
                )
                inline_posted.add(finding.rule_id)

        # Always post the summary comment
        summary = _format_summary_comment(result, inline_posted, sorted_findings, pass_findings)
        await client.create_workitem_note(project_id, mr_iid, summary)


def _inline_comment_body(f: Finding) -> str:
    """Full finding block for an inline diff comment."""
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

    if f.fix_mr_url:
        lines.append(
            f"**→ Draft fix:** [MR !{f.fix_mr_iid}]({f.fix_mr_url}) "
            f"— auto-generated corrected code, ready for review"
        )

    if f.reference:
        lines.append(f"**Reference:** {f.reference}")

    lines.append("---\n*[Quorum](https://github.com/KaustubhUp025/quorum) — distributed coordination review*")
    return "\n\n".join(lines)


def _format_summary_comment(
    result: ReviewResult,
    inline_posted: set[str],
    sorted_findings: list[Finding],
    pass_findings: list[Finding],
) -> str:
    """Compact top-level summary with a findings table and collapsible PASSes."""
    lines: list[str] = []

    lines.append("## Quorum · Distributed Coordination Review")

    if not result.findings:
        lines.append(
            "> ✅ All coordination surfaces checked. No issues found.\n\n"
            f"Scanned **{result.surfaces_detected}** coordination surface(s), "
            f"checked **{result.rules_checked}** rule(s)."
        )
        lines.append(_footer())
        return "\n\n".join(lines)

    # Summary counts
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

    non_pass = [f for f in sorted_findings if f.severity != Severity.PASS]

    if non_pass:
        # Findings table — compact row per finding
        table_rows = ["| Sev | Rule | File | Line | Conf |",
                      "|---|---|---|---|---|"]
        for f in non_pass:
            file_col = f"`{f.file_path}`" if f.file_path else "—"
            line_col = str(f.line_number) if f.line_number else "—"
            inline_marker = " ↗" if f.rule_id in inline_posted else ""
            table_rows.append(
                f"| {f.severity.emoji} {f.severity.value} "
                f"| {f.rule_id}{inline_marker} "
                f"| {file_col} "
                f"| {line_col} "
                f"| {f.confidence}% |"
            )
        lines.append("\n".join(table_rows))

        if inline_posted:
            lines.append("*↗ = detailed finding posted as an inline diff comment*")

        lines.append("---")

        # Full blocks only for findings that did NOT get an inline comment
        non_inline = [f for f in non_pass if f.rule_id not in inline_posted]
        for finding in non_inline:
            lines.append(_finding_block(finding))
            lines.append("---")

    if pass_findings:
        compact_lines = "\n".join(_pass_compact_line(f) for f in pass_findings)
        lines.append(
            f"<details>\n<summary>🟢 Passed checks ({len(pass_findings)})</summary>\n\n"
            f"{compact_lines}\n\n</details>"
        )
        lines.append("---")

    if result.ci_correlation:
        lines.append(f"### 🔍 CI Failure Correlation\n\n{result.ci_correlation}")
        lines.append("---")

    lines.append(_footer())
    return "\n\n".join(lines)


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

    if f.fix_mr_url:
        lines.append(
            f"**→ Draft fix:** [MR !{f.fix_mr_iid}]({f.fix_mr_url}) "
            f"— auto-generated corrected code, ready for review"
        )

    if f.reference:
        lines.append(f"**Reference:** {f.reference}")

    return "\n\n".join(lines)


def _pass_compact_line(f: Finding) -> str:
    loc = ""
    if f.file_path:
        loc = f" · `{f.file_path}`" + (f":{f.line_number}" if f.line_number else "")
    return f"🟢 **PASS** — {f.rule_id}: {f.title}{loc} ({f.confidence}%)"


def format_comment(result: ReviewResult) -> str:
    """Produce the full Markdown body for a single top-level MR comment (no inline posts)."""
    lines: list[str] = []

    lines.append("## Quorum · Distributed Coordination Review")

    if not result.findings:
        lines.append(
            "> ✅ All coordination surfaces checked. No issues found.\n\n"
            f"Scanned **{result.surfaces_detected}** coordination surface(s), "
            f"checked **{result.rules_checked}** rule(s)."
        )
        lines.append(_footer())
        return "\n\n".join(lines)

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

    priority_order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.PASS]
    sorted_findings = sorted(
        result.findings, key=lambda f: priority_order.index(f.severity)
    )

    non_pass = [f for f in sorted_findings if f.severity != Severity.PASS]
    pass_findings = [f for f in sorted_findings if f.severity == Severity.PASS]

    for finding in non_pass:
        lines.append(_finding_block(finding))
        lines.append("---")

    if pass_findings:
        compact_lines = "\n".join(_pass_compact_line(f) for f in pass_findings)
        lines.append(
            f"<details>\n<summary>🟢 Passed checks ({len(pass_findings)})</summary>\n\n"
            f"{compact_lines}\n\n</details>"
        )
        lines.append("---")

    if result.ci_correlation:
        lines.append(f"### 🔍 CI Failure Correlation\n\n{result.ci_correlation}")
        lines.append("---")

    lines.append(_footer())
    return "\n\n".join(lines)


def _footer() -> str:
    return (
        "*Powered by [Quorum](https://github.com/KaustubhUp025/quorum) · "
        "Gemini 2.5 Pro + GitLab MCP · "
        "[Docs](docs/RULES.md) · "
        "[Add a rule](docs/CONTRIBUTING.md)*"
    )
