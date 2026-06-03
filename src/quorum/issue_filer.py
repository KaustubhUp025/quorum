"""Smart issue-filing with graceful fallback.

Fallback chain when issues are disabled or filing fails:
  1. Create a GitHub/GitLab issue  (primary)
  2. Open a draft fix PR with the finding body  (fallback when issues disabled)
  3. Log a structured warning with manual-filing instructions  (last resort)

Usage:
    result = await file_finding_issue(client, review_result, platform="github")
    # result.url     — the URL created (issue, PR, or None)
    # result.method  — "issue" | "fix_pr" | "manual"
    # result.blocked_reason — why primary path failed, or None
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from quorum.models import ReviewResult, Finding

log = structlog.get_logger(__name__)


@dataclass
class IssueFilingResult:
    url: str | None = None
    method: str = "manual"           # "issue" | "fix_pr" | "manual"
    blocked_reason: str | None = None
    issue_number: int | None = None


def _build_issue_title(finding: "Finding", repo: str) -> str:
    rule = finding.rule_id.lower().replace("_", "-")
    return f"{rule}: {finding.title} in {repo.split('/')[-1]}"


def _build_issue_body(finding: "Finding", repo: str, mr_iid: int, platform: str) -> str:
    pr_label = "PR" if platform == "github" else "MR"
    ref = f"#{mr_iid}" if platform == "github" else f"!{mr_iid}"
    file_ref = f"`{finding.file_path}`" if finding.file_path else "the diff"

    body = textwrap.dedent(f"""\
        ## Summary

        {pr_label} {ref} in `{repo}` introduces code in {file_ref} that matches \
**{finding.rule_id}: {finding.rule_name}** — a distributed coordination anti-pattern.

        **Confidence:** {finding.confidence}%  |  **Severity:** {finding.severity.value}

        ## Finding

        {finding.explanation}
        """)

    if finding.diff_snippet:
        body += f"\n## Code snippet\n\n```\n{finding.diff_snippet}\n```\n"

    if finding.suggested_fix:
        body += f"\n## Suggested fix\n\n{finding.suggested_fix}\n"

    if finding.reference:
        body += f"\n## Reference\n\n{finding.reference}\n"

    body += textwrap.dedent(f"""
        ## How this was found

        This issue was identified by [Quorum](https://github.com/KaustubhUp025/quorum), \
an open-source Gemini-powered agent that reviews merge requests for distributed \
coordination anti-patterns (thundering herds, missing saga compensation, lost updates, \
transactional outbox violations, and more). It uses code search to verify findings \
across the full repository before reporting.

        Happy to open a follow-up {pr_label} with a fix if that would be helpful.
        """)
    return body.strip()


async def file_finding_issue(
    client: object,
    result: "ReviewResult",
    *,
    platform: str,
    min_severity: str = "HIGH",
) -> list[IssueFilingResult]:
    """File one issue per HIGH+ finding.  Returns a list of IssueFilingResult."""
    from quorum.models import Severity

    _SEVERITY_ORDER = [s.value for s in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.PASS]]
    threshold_idx = _SEVERITY_ORDER.index(min_severity) if min_severity in _SEVERITY_ORDER else 1

    eligible = [
        f for f in result.findings
        if f.severity != Severity.PASS and _SEVERITY_ORDER.index(f.severity.value) <= threshold_idx
    ]
    if not eligible:
        log.info("issue_filer_no_eligible_findings", threshold=min_severity)
        return []

    # Check whether the repo supports issue filing
    metadata = {}
    try:
        metadata = await client.check_repo_metadata(result.project_id)  # type: ignore[attr-defined]
    except AttributeError:
        log.warning("issue_filer_client_no_metadata_method", client=type(client).__name__)

    has_issues = metadata.get("has_issues", True)  # default True — try anyway
    filing_results: list[IssueFilingResult] = []

    for finding in eligible:
        title = _build_issue_title(finding, result.project_id)
        body = _build_issue_body(finding, result.project_id, result.mr_iid, platform)
        r = IssueFilingResult()

        if has_issues:
            try:
                resp = await client.create_issue(result.project_id, title, body)  # type: ignore[attr-defined]
                if resp.get("blocked"):
                    r.blocked_reason = resp.get("reason", "unknown")
                    log.warning(
                        "issue_filer_issue_blocked",
                        repo=result.project_id,
                        reason=r.blocked_reason,
                    )
                    has_issues = False  # don't retry for subsequent findings
                else:
                    r.url = resp.get("url")
                    r.method = "issue"
                    r.issue_number = resp.get("number")
                    log.info("issue_filer_created", method="issue", url=r.url)
                    filing_results.append(r)
                    continue
            except Exception as exc:
                r.blocked_reason = str(exc)
                log.warning("issue_filer_exception", error=str(exc))
                has_issues = False

        # Fallback: try to open ONE draft fix PR for the first blocked finding only.
        # Subsequent findings skip the fix-PR path (same source→target branch would
        # cause a 409 duplicate-MR error) and go straight to manual warning.
        if not has_issues:
            fix_pr_url = await _try_fix_pr_fallback(client, result, finding, platform) if not filing_results else None
            if fix_pr_url:
                r.url = fix_pr_url
                r.method = "fix_pr"
                r.blocked_reason = r.blocked_reason or "issues_disabled"
                log.info("issue_filer_created", method="fix_pr", url=r.url)
            else:
                # Last resort: emit a structured warning
                r.method = "manual"
                pr_label = "PR" if platform == "github" else "MR"
                log.warning(
                    "issue_filer_manual_action_required",
                    repo=result.project_id,
                    rule=finding.rule_id,
                    severity=finding.severity.value,
                    suggestion=(
                        f"Issues are disabled on {result.project_id}. "
                        f"File a {pr_label} manually or open an issue in the project's "
                        f"external tracker (Jira, Linear, etc.)."
                    ),
                )
        filing_results.append(r)

    return filing_results


async def _try_fix_pr_fallback(
    client: object,
    result: "ReviewResult",
    finding: "Finding",
    platform: str,
) -> str | None:
    """Attempt to create a minimal draft PR whose body contains the finding."""
    try:
        meta = await client.get_mr_metadata(result.project_id, result.mr_iid)  # type: ignore[attr-defined]
        source = meta.get("source_branch", "")
        target = meta.get("target_branch", "main")
        if not source:
            return None

        # Draft PR / MR title and description
        pr_label = "PR" if platform == "github" else "MR"
        title = f"[Quorum finding] {finding.rule_id}: {finding.title}"
        body = (
            f"This draft {pr_label} was opened by [Quorum](https://github.com/KaustubhUp025/quorum) "
            f"because **issues are disabled** on this repository and the review of "
            f"{pr_label} #{result.mr_iid} found a **{finding.severity.value}** coordination issue.\n\n"
            + _build_issue_body(finding, result.project_id, result.mr_iid, platform)
        )
        resp_str = await client.create_merge_request(  # type: ignore[attr-defined]
            result.project_id,
            source_branch=source,
            target_branch=target,
            title=title,
            description=body,
        )
        import json as _json
        resp = _json.loads(resp_str) if isinstance(resp_str, str) else resp_str
        return resp.get("web_url") or resp.get("url")
    except Exception as exc:
        log.warning("issue_filer_fix_pr_failed", error=str(exc))
        return None
