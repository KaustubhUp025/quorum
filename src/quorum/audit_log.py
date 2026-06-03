"""Persistent audit log — appended after every review run.

Entries are stored as a JSON array at ``~/.quorum/audit_log.json``
(overridable via the ``QUORUM_AUDIT_LOG`` environment variable).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from quorum.models import ReviewResult


class AuditFinding(BaseModel):
    rule: str
    severity: str
    confidence: int
    title: str
    file_path: str | None = None


class AuditEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    platform: str
    repo: str
    pr: int
    findings: list[AuditFinding] = Field(default_factory=list)
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    passes: int = 0
    blocked: bool = False
    comment_posted: bool = False
    issue_filed: str | None = None
    fix_mr_url: str | None = None


def _log_path() -> Path:
    """Return path to the audit log, respecting the QUORUM_AUDIT_LOG env var."""
    env_path = os.environ.get("QUORUM_AUDIT_LOG")
    if env_path:
        return Path(env_path)
    return Path.home() / ".quorum" / "audit_log.json"


def _load_raw() -> list[dict]:
    p = _log_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def load_entries() -> list[AuditEntry]:
    """Return all audit log entries in chronological order (oldest first)."""
    return [AuditEntry(**e) for e in _load_raw()]


def append_entry(
    result: ReviewResult,
    *,
    platform: str,
    comment_posted: bool = False,
    issue_filed: str | None = None,
) -> AuditEntry:
    """Append one entry to the audit log and return it."""
    from quorum.models import Severity

    entry = AuditEntry(
        platform=platform,
        repo=result.project_id,
        pr=result.mr_iid,
        findings=[
            AuditFinding(
                rule=f.rule_id,
                severity=f.severity.value,
                confidence=f.confidence,
                title=f.title,
                file_path=f.file_path,
            )
            for f in result.findings
            if f.severity != Severity.PASS
        ],
        critical=result.critical_count,
        high=result.high_count,
        medium=result.medium_count,
        low=result.low_count,
        passes=result.pass_count,
        blocked=result.blocked,
        comment_posted=comment_posted,
        issue_filed=issue_filed,
        fix_mr_url=next((f.fix_mr_url for f in result.findings if f.fix_mr_url), None),
    )

    raw = _load_raw()
    raw.append(entry.model_dump())

    p = _log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw, indent=2))

    return entry


def render_history(
    entries: list[AuditEntry],
    *,
    last_n: int | None = None,
    repo_filter: str | None = None,
) -> None:
    """Render the audit log as a Rich table with a severity bar chart."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    console = Console()

    if repo_filter:
        entries = [e for e in entries if repo_filter.lower() in e.repo.lower()]

    if not entries:
        console.print("[yellow]No audit log entries found.[/yellow]")
        return

    # Most-recent first for display
    display = list(reversed(entries))
    if last_n:
        display = display[:last_n]

    total = len(entries)
    total_critical = sum(e.critical for e in entries)
    total_high = sum(e.high for e in entries)
    total_medium = sum(e.medium for e in entries)
    total_low = sum(e.low for e in entries)
    total_passes = sum(e.passes for e in entries)
    total_findings = total_critical + total_high + total_medium + total_low

    # ── Header panel ──────────────────────────────────────────────────────────
    hdr = Text()
    hdr.append(f"  {total} review{'s' if total != 1 else ''}  ·  ", style="bold white")
    hdr.append(f"{total_findings} findings  ·  ", style="bold white")
    hdr.append(f"🔴 {total_critical} critical  ", style="bold red")
    hdr.append(f"🟠 {total_high} high  ", style="bold yellow")
    hdr.append(f"🟡 {total_medium} medium  ", style="bold")
    hdr.append(f"🟢 {total_passes} pass", style="bold green")
    console.print(Panel(hdr, title="[bold cyan]Quorum Audit Log[/bold cyan]", expand=False))

    # ── Severity bar chart ────────────────────────────────────────────────────
    console.print()
    console.print("  [bold]Findings by severity[/bold]")
    console.rule(style="dim")

    BAR_WIDTH = 24
    denominator = max(total_findings + total_passes, 1)
    chart_max = max(total_critical, total_high, total_medium, total_passes, 1)

    def _bar(count: int) -> str:
        filled = round((count / chart_max) * BAR_WIDTH)
        return "█" * filled + "░" * (BAR_WIDTH - filled)

    chart_rows = [
        ("🔴 CRITICAL", total_critical, "bold red"),
        ("🟠 HIGH     ", total_high, "bold yellow"),
        ("🟡 MEDIUM   ", total_medium, "bold"),
        ("🟢 PASS     ", total_passes, "bold green"),
    ]
    for label, count, style in chart_rows:
        pct = round(count / denominator * 100)
        bar = _bar(count)
        line = f"  {label}  [dim]{bar}[/dim]  [{style}]{count:3d}[/{style}]  [dim]({pct}%)[/dim]"
        console.print(line)

    console.print()

    # ── Reviews table ─────────────────────────────────────────────────────────
    table = Table(
        title=f"Reviews  (showing {len(display)} of {total})",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Date", width=11)
    table.add_column("Platform", width=8)
    table.add_column("Repo", max_width=38)
    table.add_column("PR/MR", width=7, justify="right")
    table.add_column("🔴", width=3, justify="right")
    table.add_column("🟠", width=3, justify="right")
    table.add_column("Blocked", width=8)
    table.add_column("Issue / Fix MR")

    for i, entry in enumerate(display):
        run_num = total - i
        date_str = entry.timestamp[:10]
        pr_label = f"#{entry.pr}" if entry.platform == "github" else f"!{entry.pr}"
        blocked_cell = "[bold red]⛔ YES[/bold red]" if entry.blocked else "[dim]—[/dim]"
        crit_cell = f"[bold red]{entry.critical}[/bold red]" if entry.critical else "[dim]0[/dim]"
        high_cell = f"[bold yellow]{entry.high}[/bold yellow]" if entry.high else "[dim]0[/dim]"

        links: list[str] = []
        if entry.issue_filed:
            parts = entry.issue_filed.rstrip("/").split("/")
            links.append(f"Issue #{parts[-1]}" if parts else entry.issue_filed)
        if entry.fix_mr_url:
            parts = entry.fix_mr_url.rstrip("/").split("/")
            links.append(f"Fix !{parts[-1]}" if parts else entry.fix_mr_url)
        link_cell = "  ".join(links) if links else "[dim]—[/dim]"

        table.add_row(
            str(run_num),
            date_str,
            entry.platform,
            entry.repo,
            pr_label,
            crit_cell,
            high_cell,
            blocked_cell,
            link_cell,
        )

    console.print(table)
    console.print(f"\n  [dim]Log: {_log_path()}[/dim]")
