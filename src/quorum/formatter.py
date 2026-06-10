"""Stage 3 of the Quorum agent pipeline — formats and renders review results.

ReportFormatterAgent converts a ReviewResult into a structured Markdown MR comment.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from quorum.models import Finding, ReviewResult, Severity

if TYPE_CHECKING:
    pass

_SEP = "\n\n---\n\n"

_HEADING_RE = re.compile(r"^(#+\s)", re.MULTILINE)


def _sanitise_llm_prose(text: str) -> str:
    """Escape markdown heading markers in LLM-generated prose to prevent structural injection.

    A compromised LLM output containing '## Quorum · ...' would spoof a second
    review section at the same visual level as the real one. Escaping the leading
    '#' renders it as literal text instead of a heading.
    """
    return _HEADING_RE.sub(r"\\\1", text)


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
        emit: Any = None,
    ) -> str | None:
        """Post inline finding comments + one top-level summary.

        For each non-PASS finding that has a file_path and line_number, an inline
        discussion thread is posted on the exact diff line. A compact summary table
        is always posted as a top-level note so the full review is visible in one
        place even if inline comments fail or are unsupported.

        `emit(stage, **data)` (optional) streams progress to the live demo. Returns
        the URL of the posted summary comment (best-effort), or None.
        """
        _emit = emit or (lambda *a, **k: None)
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

        mr_author = mr_meta.get("author", "")

        for finding in non_pass:
            if finding.file_path and finding.line_number and has_diff_refs:
                body = _inline_comment_body(finding)
                _emit("inline_comment", rule_id=finding.rule_id,
                      file_path=finding.file_path, line=finding.line_number,
                      severity=finding.severity.value)

                # C4 — Reviewer suggestion for CRITICAL findings
                if finding.severity.value == "CRITICAL" and finding.file_path:
                    try:
                        _emit("reviewer_lookup", rule_id=finding.rule_id, file_path=finding.file_path)
                        file_contribs = await client.get_file_contributors(
                            project_id, finding.file_path
                        )
                        if not isinstance(file_contribs, list):
                            file_contribs = []

                        # Fallback: new files have no history — check the parent directory.
                        # Catches the common case: a new consumer added alongside existing
                        # consumers that share the same directory expert.
                        used_dir_fallback = False
                        contributors = file_contribs
                        if not contributors:
                            parent_dir = "/".join(finding.file_path.split("/")[:-1])
                            if parent_dir:
                                dir_contribs = await client.get_file_contributors(
                                    project_id, parent_dir
                                )
                                if isinstance(dir_contribs, list) and dir_contribs:
                                    contributors = dir_contribs
                                    used_dir_fallback = True

                        # Exclude the MR author (not useful to suggest "review your own code")
                        reviewers = [u for u in contributors if isinstance(u, str) and u and u != mr_author]
                        if reviewers:
                            mention = ", ".join(f"@{u}" for u in reviewers[:2])
                            context = (
                                f"the `{'/'.join(finding.file_path.split('/')[:-1])}/` directory"
                                if used_dir_fallback
                                else f"`{finding.file_path}`"
                            )
                            body += (
                                f"\n\n---\n📋 **Suggested reviewer:** {mention} "
                                f"has recent commits to {context} — "
                                "consider requesting their review."
                            )
                    except Exception:
                        pass  # Contributor lookup is best-effort; never block posting

                await client.create_mr_discussion(
                    project_id,
                    mr_iid,
                    body,
                    file_path=finding.file_path,
                    line_number=finding.line_number,
                    diff_refs=diff_refs,
                )
                inline_posted.add(f"{finding.rule_id}:{finding.file_path}:{finding.line_number}")

        # Always post the summary comment
        _emit("posting_summary", inline_count=len(inline_posted))
        summary = _format_summary_comment(result, inline_posted, sorted_findings, pass_findings)
        note_result = await client.create_workitem_note(project_id, mr_iid, summary)

        # Best-effort: pull a clickable URL out of the note result (glab returns the
        # note URL as text; REST returns JSON). Fall back to the MR's own web_url.
        comment_url = None
        if note_result:
            m = re.search(r"https?://\S+", str(note_result))
            if m:
                comment_url = m.group(0).rstrip(".\"'")
        if not comment_url:
            comment_url = mr_meta.get("web_url") or None
        _emit("comment_posted", url=comment_url, inline_count=len(inline_posted))
        return comment_url


def _inline_comment_body(f: Finding) -> str:
    """Full finding block for an inline diff comment."""
    lines: list[str] = []

    header = f"{f.severity.emoji} **{f.severity.value}** — {f.rule_id}: {f.title}"
    meta = f"**Confidence: {f.confidence}%**"
    if f.file_path:
        loc = f"`{f.file_path}`" + (f":{f.line_number}" if f.line_number else "")
        meta += f" | {loc}"
    lines.append(f"### {header}\n{meta}")
    lines.append(_sanitise_llm_prose(f.explanation))

    if f.diff_snippet:
        lines.append(f"**In your diff:**\n```\n{f.diff_snippet.strip()}\n```")

    if f.search_evidence:
        lines.append(f"**Found via semantic search:**\n```\n{f.search_evidence.strip()}\n```")

    if f.suggested_fix:
        lines.append(f"**Suggested fix:** {_sanitise_llm_prose(f.suggested_fix)}")

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
            inline_marker = " ↗" if f"{f.rule_id}:{f.file_path}:{f.line_number}" in inline_posted else ""
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
        non_inline = [f for f in non_pass if f"{f.rule_id}:{f.file_path}:{f.line_number}" not in inline_posted]
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
    lines.append(_sanitise_llm_prose(f.explanation))

    if f.diff_snippet:
        lines.append(f"**In your diff:**\n```\n{f.diff_snippet.strip()}\n```")

    if f.search_evidence:
        lines.append(f"**Found via semantic search:**\n```\n{f.search_evidence.strip()}\n```")

    if f.suggested_fix:
        lines.append(f"**Suggested fix:** {_sanitise_llm_prose(f.suggested_fix)}")

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


def format_verification_comment(
    rule_id: str,
    fix_mr_iid: int,
    fix_mr_url: str | None,
    pipeline_status: str,
    pipeline_url: str | None = None,
) -> str:
    """Produce a follow-up comment posted to the original MR after the fix pipeline completes."""
    mr_ref = f"MR !{fix_mr_iid}"
    if fix_mr_url:
        mr_ref += f" · [view fix MR]({fix_mr_url})"
    pipe_link = f" · [[view pipeline]]({pipeline_url})" if pipeline_url else ""

    if pipeline_status == "success":
        body = (
            f"✅ **Fix verified** — CI passes on {mr_ref}{pipe_link}.\n"
            f"The `{rule_id}` fix is safe to merge."
        )
    elif pipeline_status in ("failed", "failure"):
        body = (
            f"❌ **Fix needs review** — CI failed on {mr_ref}{pipe_link}.\n"
            f"The auto-generated `{rule_id}` fix may need adjustment before merging."
        )
    elif pipeline_status in ("canceled", "cancelled"):
        body = (
            f"⚠️ **Pipeline canceled** on {mr_ref}{pipe_link}.\n"
            f"Check the `{rule_id}` fix branch manually before merging."
        )
    else:
        body = (
            f"⏱ **Verification timed out** — CI still running on {mr_ref}.\n"
            f"Check the pipeline status manually before merging the `{rule_id}` fix."
        )

    return f"## Quorum · Fix Verification\n\n{body}\n\n{_footer()}"


def _footer() -> str:
    return (
        "*Powered by [Quorum](https://github.com/KaustubhUp025/quorum) · "
        "Gemini 2.5 Pro + GitLab MCP · "
        "[Docs](docs/RULES.md) · "
        "[Add a rule](docs/CONTRIBUTING.md)*"
    )
