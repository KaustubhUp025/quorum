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
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
    )


@click.group()
@click.version_option(__version__, prog_name="quorum")
def main() -> None:
    """Quorum — distributed-coordination MR linter."""


@main.command("review")
@click.option("--project-id", "-p", required=False, help="GitLab project ID or path (e.g. myorg/myrepo)")
@click.option("--mr-iid", "-m", required=False, type=int, help="Merge request IID")
@click.option("--no-comment", is_flag=True, default=False, help="Analyse but don't post an MR comment")
@click.option("--dry-run", is_flag=True, default=False, help="Print the comment instead of posting it")
@click.option(
    "--rest-only", is_flag=True, default=False,
    help="Use GitLab REST API instead of MCP server (no Node.js required; lexical search only)",
)
@click.option(
    "--list-tools", is_flag=True, default=False,
    help="Connect to the MCP server, print its tool list, and exit (useful for debugging)",
)
def review_cmd(
    project_id: str | None,
    mr_iid: int | None,
    no_comment: bool,
    dry_run: bool,
    rest_only: bool,
    list_tools: bool,
) -> None:
    """Review a merge request for distributed coordination anti-patterns."""
    settings = get_settings()
    _configure_logging(settings.log_level)

    if list_tools:
        asyncio.run(_async_list_tools(settings))
        return

    # Fall back to CI environment variables
    project_id = project_id or settings.ci_project_path or settings.ci_project_id
    mr_iid_val = mr_iid or (
        int(settings.ci_merge_request_iid) if settings.ci_merge_request_iid else None
    )

    if not project_id or not mr_iid_val:
        click.echo(
            "Error: --project-id and --mr-iid are required "
            "(or set CI_PROJECT_PATH / CI_MERGE_REQUEST_IID env vars).",
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
) -> None:
    from quorum.agent import QuorumAgent
    from quorum.formatter import format_comment
    from quorum.gitlab_client import GitLabYodaMCPClient, GitLabRESTClient, make_client

    agent = QuorumAgent(settings)
    gitlab = make_client(settings, rest_only=rest_only, project_id=project_id)

    if rest_only:
        console.print("[yellow]ℹ  REST mode — GitLab REST API (lexical search, no binary needed)[/yellow]")
    elif hasattr(gitlab, "_server_cmd"):
        console.print("[cyan]ℹ  MCP mode — @zereight/mcp-gitlab (community, 107 tools)[/cyan]")
    elif hasattr(gitlab, "_tmpdir") or hasattr(gitlab, "_make_git_context"):
        console.print("[green]ℹ  MCP mode — glab mcp serve (official GitLab CLI, 191 tools)[/green]")
    else:
        console.print("[yellow]ℹ  REST mode[/yellow]")

    async with gitlab.connect():
        result = await agent.review(
            project_id=project_id,
            mr_iid=mr_iid,
            client=gitlab,
            post_comment=post_comment and not dry_run,
        )

    if dry_run:
        console.print("\n[bold cyan]--- DRY RUN: MR comment body ---[/bold cyan]\n")
        console.print(format_comment(result))
        console.print("[bold cyan]--- END ---[/bold cyan]\n")

    # Summary table
    table = Table(title=f"Quorum Review — MR !{mr_iid}", show_lines=True)
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

    console.print(table)

    if result.blocked:
        console.print("\n[bold red]⛔  CRITICAL findings found — pipeline blocked.[/bold red]")
        sys.exit(1)
    else:
        console.print(
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
