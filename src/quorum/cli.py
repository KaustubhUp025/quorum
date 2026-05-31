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
def review_cmd(
    project_id: str | None,
    mr_iid: int | None,
    no_comment: bool,
    dry_run: bool,
    rest_only: bool,
    list_tools: bool,
    platform: str | None,
    output_format: str,
) -> None:
    """Review a merge request / pull request for distributed coordination anti-patterns."""
    settings = get_settings()
    _configure_logging(settings.log_level)

    if list_tools:
        asyncio.run(_async_list_tools(settings))
        return

    # Resolve platform (CLI flag overrides QUORUM_PLATFORM)
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
        )
    )


async def _async_list_tools(settings) -> None:
    """Connect to the MCP server and print all available tool names."""
    from quorum.gitlab_client import GitLabYodaMCPClient

    cmd = [p for p in settings.mcp_server_cmd.split() if p]
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
) -> None:
    from quorum.agent import QuorumAgent
    from quorum.formatter import format_comment
    from quorum.gitlab_client import make_client
    from quorum.github_client import make_github_client

    # In SARIF mode stdout is reserved for machine-readable JSON; use stderr for banners.
    from rich.console import Console as _Console
    _out = _Console(stderr=True) if output_format == "sarif" else console

    agent = QuorumAgent(settings)

    if platform == "github":
        client = make_github_client(settings)
        _out.print("[blue]ℹ  GitHub mode — GitHub REST API[/blue]")
    else:
        client = make_client(settings, rest_only=rest_only, project_id=project_id)
        if rest_only:
            _out.print("[yellow]ℹ  REST mode — GitLab REST API (lexical search, no binary needed)[/yellow]")
        elif hasattr(client, "_server_cmd"):
            _out.print("[cyan]ℹ  MCP mode — @zereight/mcp-gitlab (community, 107 tools)[/cyan]")
        elif hasattr(client, "_make_git_context"):
            _out.print("[green]ℹ  MCP mode — glab mcp serve (official GitLab CLI, 191 tools)[/green]")
        else:
            _out.print("[yellow]ℹ  REST mode[/yellow]")

    async with client.connect():
        result = await agent.review(
            project_id=project_id,
            mr_iid=mr_iid,
            client=client,
            post_comment=post_comment and not dry_run,
        )

    # SARIF output — write to stdout, skip the rich table
    if output_format == "sarif":
        from quorum.sarif import format_sarif
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
