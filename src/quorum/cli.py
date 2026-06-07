"""Click CLI entry point for local use and GitLab CI."""

from __future__ import annotations

import asyncio
import sys

import click
import structlog
from rich.console import Console
from rich.table import Table

from quorum import __version__
from quorum.config import get_settings

console = Console()
log = structlog.get_logger(__name__)


def _configure_logging(level: str) -> None:
    import logging
    import sys
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )


@click.group()
@click.version_option(__version__, prog_name="quorum")
def main() -> None:
    """Quorum — distributed-coordination MR linter."""


@main.command("review")
@click.option("--project-id", "-p", required=False, help="Project path (e.g. myorg/myrepo) or GitHub owner/repo")
@click.option("--mr-iid", "-m", required=False, type=int, help="Merge request IID (GitLab) or PR number (GitHub)")
@click.option("--no-comment", is_flag=True, default=False, help="Analyse but don't post an MR/PR comment")
@click.option("--dry-run", is_flag=True, default=False, help="Print the comment instead of posting it")
@click.option(
    "--rest-only", is_flag=True, default=False,
    help="Use GitLab REST API instead of MCP server (no Node.js required; lexical search only)",
)
@click.option(
    "--list-tools", is_flag=True, default=False,
    help="Connect to the MCP server, print its tool list, and exit (useful for debugging)",
)
@click.option(
    "--platform", default=None,
    type=click.Choice(["gitlab", "github"], case_sensitive=False),
    help="Platform to review on (default: gitlab). Overrides QUORUM_PLATFORM.",
)
@click.option(
    "--format", "output_format", default="text",
    type=click.Choice(["text", "sarif"], case_sensitive=False),
    help="Output format: 'text' (default) or 'sarif' (SARIF 2.1.0 for GitHub Code Scanning).",
)
@click.option(
    "--force", is_flag=True, default=False,
    help="Post a new review even if Quorum has already reviewed this MR (bypasses duplicate guard).",
)
@click.option(
    "--verify-fix", "verify_fix", is_flag=True, default=False,
    help=(
        "After creating a fix MR, poll its CI pipeline and post a ✅/❌ verification comment "
        "on the original MR when the pipeline completes. Blocks until done or --verify-timeout."
    ),
)
@click.option(
    "--verify-timeout", "verify_timeout", default=None, type=int, metavar="SECONDS",
    help="Seconds to wait for fix CI pipeline (default: QUORUM_VERIFY_FIX_TIMEOUT or 600).",
)
def review_cmd(
    project_id: str | None,
    mr_iid: int | None,
    no_comment: bool,
    dry_run: bool,
    rest_only: bool,
    list_tools: bool,
    platform: str | None,
    output_format: str,
    force: bool,
    verify_fix: bool,
    verify_timeout: int | None,
) -> None:
    """Review a merge request / pull request for distributed coordination anti-patterns."""
    settings = get_settings()
    _configure_logging(settings.log_level)

    # Apply .quorum.yml project config on top of env-var settings
    from quorum.project_config import load_project_config, apply_project_config
    project_cfg = load_project_config()
    settings = apply_project_config(settings, project_cfg)

    if list_tools:
        asyncio.run(_async_list_tools(settings))
        return

    # Resolve platform (CLI flag overrides .quorum.yml, which overrides QUORUM_PLATFORM)
    effective_platform = platform or getattr(settings, "platform", "gitlab") or "gitlab"

    # Fall back to CI environment variables (GitLab only)
    if effective_platform == "gitlab":
        project_id = project_id or settings.ci_project_path or settings.ci_project_id
        mr_iid_val = mr_iid or (
            int(settings.ci_merge_request_iid) if settings.ci_merge_request_iid else None
        )
    else:
        mr_iid_val = mr_iid

    if not project_id or not mr_iid_val:
        click.echo(
            "Error: --project-id and --mr-iid are required "
            "(or set CI_PROJECT_PATH / CI_MERGE_REQUEST_IID env vars for GitLab CI).",
            err=True,
        )
        sys.exit(2)

    asyncio.run(
        _async_review(
            project_id=project_id,
            mr_iid=mr_iid_val,
            settings=settings,
            post_comment=not (no_comment or dry_run),
            dry_run=dry_run,
            rest_only=rest_only,
            platform=effective_platform,
            output_format=output_format,
            force=force,
            verify_fix=verify_fix,
            verify_timeout=verify_timeout,
        )
    )


async def _async_list_tools(settings) -> None:
    """Connect to the MCP server and print all available tool names."""
    from quorum.gitlab_client import GitLabYodaMCPClient

    import shlex
    cmd = shlex.split(settings.mcp_server_cmd)
    client = GitLabYodaMCPClient(settings.gitlab_url, settings.gitlab_token, server_cmd=cmd)

    console.print("[bold cyan]Connecting to yoda-digital MCP server...[/bold cyan]")
    async with client.connect():
        tools = await client.list_available_tools()
    console.print(f"\n[bold green]{len(tools)} tools available:[/bold green]")
    for name in tools:
        console.print(f"  • {name}")


async def _async_review(
    project_id: str,
    mr_iid: int,
    settings,
    post_comment: bool,
    dry_run: bool,
    rest_only: bool = False,
    platform: str = "gitlab",
    output_format: str = "text",
    force: bool = False,
    verify_fix: bool = False,
    verify_timeout: int | None = None,
) -> None:
    from quorum.agent import QuorumAgent
    from quorum.formatter import format_comment
    from quorum.gitlab_client import make_client
    from quorum.github_client import make_github_client

    # In SARIF mode stdout is reserved for machine-readable JSON; use stderr for banners.
    from rich.console import Console as _Console
    _out = _Console(stderr=True) if output_format == "sarif" else console

    agent = QuorumAgent(settings)

    llm_label = settings.llm_backend
    if platform == "github":
        client = make_github_client(settings)
        _out.print(f"[blue]ℹ  GitHub mode — GitHub REST API · LLM: {llm_label}[/blue]")
    else:
        client = make_client(settings, rest_only=rest_only, project_id=project_id)
        if rest_only:
            _out.print(f"[yellow]ℹ  REST mode — GitLab REST API · LLM: {llm_label}[/yellow]")
        elif hasattr(client, "_server_cmd"):
            _out.print(f"[cyan]ℹ  MCP mode — @zereight/mcp-gitlab · LLM: {llm_label}[/cyan]")
        elif hasattr(client, "_make_git_context"):
            _out.print(f"[green]ℹ  MCP mode — glab mcp serve (official, 191 tools) · LLM: {llm_label}[/green]")
        else:
            _out.print(f"[yellow]ℹ  REST mode · LLM: {llm_label}[/yellow]")

    # Auto-filing needs the live client, so keep it inside the connect block.
    filed_issue_url: str | None = None
    async with client.connect():
        result = await agent.review(
            project_id=project_id,
            mr_iid=mr_iid,
            client=client,
            post_comment=post_comment and not dry_run,
            force=force,
        )

        # Auto-file issues when enabled and there are HIGH+ findings
        if settings.auto_file_issues and not dry_run and result.findings:
            from quorum.issue_filer import file_finding_issue
            filing_results = await file_finding_issue(
                client, result,
                platform=platform,
                min_severity=settings.auto_file_issues_min_severity,
            )
            for fr in filing_results:
                if fr.url:
                    filed_issue_url = fr.url

        # Phase D: poll fix pipeline and post verification comment (opt-in via --verify-fix)
        if verify_fix and not dry_run:
            from quorum.agent import _verify_fix_pipeline
            fix_findings = [f for f in result.findings if f.fix_mr_iid]
            if fix_findings:
                timeout = verify_timeout or settings.verify_fix_timeout
                _out.print(
                    f"[cyan]ℹ  --verify-fix: polling fix pipeline for "
                    f"{len(fix_findings)} fix MR(s) (timeout {timeout}s)…[/cyan]"
                )
                for finding in fix_findings:
                    await _verify_fix_pipeline(
                        finding, client, project_id, mr_iid,
                        timeout_seconds=timeout,
                    )

    # SARIF output — write to stdout, skip the rich table
    if output_format == "sarif":
        from quorum.sarif import format_sarif
        from quorum.audit_log import append_entry
        append_entry(result, platform=platform, comment_posted=post_comment and not dry_run)
        click.echo(format_sarif(result))
        if result.blocked:
            sys.exit(1)
        return

    if dry_run:
        pr_label = "PR" if platform == "github" else "MR"
        _out.print(f"\n[bold cyan]--- DRY RUN: {pr_label} comment body ---[/bold cyan]\n")
        _out.print(format_comment(result))
        _out.print("[bold cyan]--- END ---[/bold cyan]\n")

    # Summary table
    pr_label = "PR" if platform == "github" else "MR"
    table = Table(title=f"Quorum Review — {pr_label} !{mr_iid}", show_lines=True)
    table.add_column("Rule", style="cyan")
    table.add_column("Severity", style="bold")
    table.add_column("Confidence")
    table.add_column("Title")

    for f in result.findings:
        table.add_row(
            f.rule_id,
            f"{f.severity.emoji} {f.severity.value}",
            f"{f.confidence}%",
            f.title,
        )

    _out.print(table)

    if filed_issue_url:
        _out.print(f"[cyan]  → Filed issue: {filed_issue_url}[/cyan]")
    elif settings.auto_file_issues and not dry_run and result.findings:
        _out.print(
            "[yellow]  ⚠  Could not file issue automatically. "
            "Use `quorum file-issue` to retry or file manually.[/yellow]"
        )

    # Append to audit log regardless of outcome
    from quorum.audit_log import append_entry
    append_entry(
        result,
        platform=platform,
        comment_posted=post_comment and not dry_run,
        issue_filed=filed_issue_url,
    )

    if result.blocked:
        _out.print("\n[bold red]⛔  CRITICAL findings found — pipeline blocked.[/bold red]")
        sys.exit(1)
    else:
        _out.print(
            f"\n[bold green]✅  Review complete. {result.critical_count} critical, "
            f"{result.high_count} high.[/bold green]"
        )


@main.command("list-rules")
def list_rules_cmd() -> None:
    """List all available coordination rules."""
    from quorum.rules.registry import REGISTRY

    table = Table(title="Quorum Rules", show_lines=True)
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Reference")

    for rule in REGISTRY.values():
        table.add_row(rule.id, rule.name, rule.reference)

    console.print(table)


@main.command("history")
@click.option("--last", "-n", default=None, type=int, metavar="N", help="Show only the last N runs")
@click.option("--repo", default=None, help="Filter by repo name (partial match)")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output raw JSON for scripting")
def history_cmd(last: int | None, repo: str | None, as_json: bool) -> None:
    """Show the audit log of all past review runs with a severity breakdown."""
    import json as _json
    from quorum.audit_log import load_entries, render_history

    entries = load_entries()

    if as_json:
        click.echo(_json.dumps([e.model_dump() for e in entries], indent=2))
        return

    render_history(entries, last_n=last, repo_filter=repo)


@main.command("file-issue")
@click.option("--project-id", "-p", required=True, help="Project path (owner/repo or group/project)")
@click.option("--mr-iid", "-m", required=True, type=int, help="MR / PR number that was reviewed")
@click.option(
    "--platform", default=None,
    type=click.Choice(["gitlab", "github"], case_sensitive=False),
    help="Platform (default: gitlab). Overrides QUORUM_PLATFORM.",
)
@click.option(
    "--min-severity", default="HIGH",
    type=click.Choice(["CRITICAL", "HIGH", "MEDIUM"], case_sensitive=True),
    help="Minimum severity to file an issue for (default: HIGH).",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be filed without creating anything")
def file_issue_cmd(
    project_id: str,
    mr_iid: int,
    platform: str | None,
    min_severity: str,
    dry_run: bool,
) -> None:
    """File GitHub/GitLab issues for findings from a previous review.

    Reads the audit log to find the most recent review of PROJECT_ID / MR_IID,
    then files issues (or a draft fix PR if issues are disabled) for each
    finding at or above MIN_SEVERITY.

    \b
    Examples:
      quorum file-issue -p atlanhq/atlas-metastore -m 6699 --platform github
      quorum file-issue -p myorg/myrepo -m 42 --min-severity CRITICAL --dry-run
    """
    settings = get_settings()
    _configure_logging(settings.log_level)

    effective_platform = platform or getattr(settings, "platform", "gitlab") or "gitlab"

    import asyncio
    asyncio.run(_async_file_issue(
        project_id=project_id,
        mr_iid=mr_iid,
        platform=effective_platform,
        min_severity=min_severity,
        dry_run=dry_run,
        settings=settings,
    ))


async def _async_file_issue(
    project_id: str,
    mr_iid: int,
    platform: str,
    min_severity: str,
    dry_run: bool,
    settings,
) -> None:
    from quorum.audit_log import load_entries
    from quorum.issue_filer import file_finding_issue, _build_issue_title, _build_issue_body
    from quorum.github_client import make_github_client
    from quorum.gitlab_client import make_client
    from quorum.models import ReviewResult, Finding, Severity

    # Find the most recent matching audit entry
    entries = load_entries()
    matching = [
        e for e in reversed(entries)
        if e.repo == project_id and e.pr == mr_iid
    ]
    if not matching:
        console.print(f"[red]No audit log entry found for {project_id} MR/PR #{mr_iid}.[/red]")
        console.print("[dim]Run `quorum review` first, then `quorum file-issue`.[/dim]")
        return

    entry = matching[0]

    # Reconstruct minimal ReviewResult-like object from audit entry
    _SEV = {s.value: s for s in Severity}
    findings = [
        Finding(
            rule_id=f.rule,
            rule_name=f.rule,
            severity=_SEV.get(f.severity, Severity.HIGH),
            confidence=f.confidence,
            title=f.title,
            explanation=f.title,
            file_path=f.file_path,
        )
        for f in entry.findings
    ]
    result = ReviewResult(
        mr_iid=mr_iid,
        project_id=project_id,
        findings=findings,
        blocked=entry.blocked,
    )

    if dry_run:
        console.print(f"[bold cyan]DRY RUN — would file issues for {project_id} MR/PR #{mr_iid}[/bold cyan]")
        _SEVERITY_ORDER = [s.value for s in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.PASS]]
        threshold_idx = _SEVERITY_ORDER.index(min_severity) if min_severity in _SEVERITY_ORDER else 1
        eligible = [f for f in findings if f.severity != Severity.PASS and _SEVERITY_ORDER.index(f.severity.value) <= threshold_idx]
        if not eligible:
            console.print(f"[yellow]No findings at or above {min_severity} severity.[/yellow]")
            return
        for f in eligible:
            title = _build_issue_title(f, project_id)
            console.print(f"\n  [bold]Title:[/bold] {title}")
            console.print(f"  [bold]Severity:[/bold] {f.severity.emoji} {f.severity.value}")
        return

    # Connect and file
    if platform == "github":
        client = make_github_client(settings)
    else:
        client = make_client(settings, rest_only=True)

    async with client.connect():
        # Check repo metadata first
        try:
            meta = await client.check_repo_metadata(project_id)
            has_issues = meta.get("has_issues", True)
            console.print(
                f"[{'green' if has_issues else 'yellow'}]"
                f"Repo metadata: issues_enabled={has_issues}, "
                f"visibility={meta.get('visibility', '?')}"
                f"[/{'green' if has_issues else 'yellow'}]"
            )
        except AttributeError:
            console.print("[dim]Client does not support check_repo_metadata — proceeding anyway[/dim]")

        filing_results = await file_finding_issue(
            client, result,
            platform=platform,
            min_severity=min_severity,
        )

    if not filing_results:
        console.print(f"[yellow]No eligible findings at {min_severity}+ to file.[/yellow]")
        return

    for i, fr in enumerate(filing_results, 1):
        if fr.method == "issue":
            console.print(f"[green]✅  Issue #{fr.issue_number} filed: {fr.url}[/green]")
        elif fr.method == "fix_pr":
            console.print(f"[cyan]✅  Draft fix PR created (issues disabled): {fr.url}[/cyan]")
        else:
            console.print(
                f"[red]❌  Could not file automatically ({fr.blocked_reason}). "
                f"File manually in the project's external tracker.[/red]"
            )


@main.command("check")
@click.option(
    "--staged", "mode", flag_value="staged", default=True,
    help="Check staged changes (git diff --cached). Default.",
)
@click.option(
    "--last-commit", "mode", flag_value="last-commit",
    help="Check the last commit (git diff HEAD~1 HEAD).",
)
@click.option(
    "--diff", "diff_file", default=None, metavar="FILE",
    help="Read diff from FILE (use '-' for stdin).",
)
@click.option(
    "--install-hook", is_flag=True, default=False,
    help="Install quorum check as a git pre-commit hook in .git/hooks/pre-commit.",
)
@click.option(
    "--quiet", "-q", is_flag=True, default=False,
    help="Suppress output — only set exit code.",
)
def check_cmd(
    mode: str,
    diff_file: str | None,
    install_hook: bool,
    quiet: bool,
) -> None:
    """Fast pre-commit surface scan — no API calls, ~5ms.

    Runs the surface detector (Stage 1 only) against staged changes, the last
    commit, or a supplied diff. Exits 0 when no coordination surfaces are found,
    exits 1 when surfaces are detected so the developer knows to run
    `quorum review` before merging.

    \b
    Install as a git hook:
      quorum check --install-hook

    \b
    Manual usage:
      quorum check --staged          # default: staged changes
      quorum check --last-commit     # last committed diff
      git diff main... | quorum check --diff -   # arbitrary diff on stdin
    """
    if install_hook:
        _install_precommit_hook(quiet)
        return

    diff = _get_diff(mode, diff_file, quiet)
    if diff is None:
        return  # error already printed

    from quorum.detector import detect_surfaces
    triggered = detect_surfaces(diff)

    if not triggered:
        if not quiet:
            console.print("[green]✅  quorum check: no coordination surfaces detected.[/green]")
        sys.exit(0)

    if not quiet:
        table = Table(
            title="⚠  Coordination surfaces detected — consider running `quorum review`",
            show_lines=True,
        )
        table.add_column("Rule", style="cyan")
        table.add_column("Name")
        table.add_column("Description (summary)")

        for rule in triggered:
            table.add_row(rule.id, rule.name, rule.description[:90] + "…")

        console.print(table)
        console.print(
            "\n[yellow]Run [bold]quorum review[/bold] to get a full AI-powered "
            "analysis before merging.[/yellow]"
        )

    sys.exit(1)


def _get_diff(mode: str, diff_file: str | None, quiet: bool) -> str | None:
    """Return diff text from the appropriate source, or None on error."""
    import subprocess

    if diff_file is not None:
        if diff_file == "-":
            return sys.stdin.read()
        try:
            with open(diff_file) as fh:
                return fh.read()
        except OSError as exc:
            if not quiet:
                console.print(f"[red]Error reading diff file: {exc}[/red]", err=True)
            sys.exit(2)

    if mode == "staged":
        cmd = ["git", "diff", "--cached"]
    else:
        cmd = ["git", "diff", "HEAD~1", "HEAD"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            if not quiet:
                console.print(
                    f"[red]git command failed: {result.stderr.strip()[:200]}[/red]",
                    err=True,
                )
            sys.exit(2)
        return result.stdout
    except FileNotFoundError:
        if not quiet:
            console.print("[red]git not found in PATH.[/red]", err=True)
        sys.exit(2)
    except subprocess.TimeoutExpired:
        if not quiet:
            console.print("[red]git diff timed out.[/red]", err=True)
        sys.exit(2)


def _install_precommit_hook(quiet: bool) -> None:
    """Write a pre-commit hook script into .git/hooks/pre-commit."""
    import stat
    from pathlib import Path

    hook_dir = Path(".git") / "hooks"
    if not hook_dir.is_dir():
        if not quiet:
            console.print(
                "[red]No .git/hooks directory found. "
                "Run this from the root of a git repository.[/red]",
                err=True,
            )
        sys.exit(2)

    hook_path = hook_dir / "pre-commit"
    hook_script = """\
#!/bin/sh
# Installed by `quorum check --install-hook`
# Runs the Quorum surface detector (Stage 1 only, no API calls) on staged changes.
# Exit 1 blocks the commit and prompts the developer to run `quorum review`.
quorum check --staged --quiet
status=$?
if [ $status -ne 0 ]; then
  echo ""
  echo "⚠  Quorum detected coordination surfaces in staged changes."
  echo "   Run 'quorum check --staged' for details, or"
  echo "   'quorum review' for a full AI-powered analysis."
  echo ""
fi
exit $status
"""
    hook_path.write_text(hook_script)
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    if not quiet:
        console.print(f"[green]✅  Pre-commit hook installed at {hook_path}[/green]")
        console.print("[dim]  Runs `quorum check --staged --quiet` before every commit.[/dim]")


@main.command("serve")
@click.option("--host", default="0.0.0.0", help="Bind host")
@click.option("--port", default=None, type=int, help="Bind port (default: from QUORUM_PORT env)")
def serve_cmd(host: str, port: int | None) -> None:
    """Start the Cloud Run webhook server."""
    import uvicorn
    from quorum.app import create_app

    settings = get_settings()
    _configure_logging(settings.log_level)
    app = create_app(settings)
    uvicorn.run(app, host=host, port=port or settings.port)
