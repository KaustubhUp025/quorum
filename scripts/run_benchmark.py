#!/usr/bin/env python3
"""Benchmark Quorum against real open-source PRs/MRs.

Fetches the diff for each target via the GitHub/GitLab REST API (read-only,
public repos — no auth required for the diff fetch), runs the full three-stage
Quorum pipeline, and writes results to docs/BENCHMARK.md.

Usage:
    python scripts/run_benchmark.py [--dry-run] [--output docs/BENCHMARK.md]

Flags:
    --dry-run     Run surface detector only (no Gemini calls). Fast sanity check.
    --output      Path to write Markdown results (default: docs/BENCHMARK.md)
    --target ID   Run only the benchmark target with this ID (e.g. "vllm-34981")
    --no-comment  Do not post review comments (default: True — benchmark never posts)
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Benchmark targets
# ---------------------------------------------------------------------------
# Each target is one real PR/MR. Expected outcome is manually verified:
# - FINDING targets: expect ≥1 HIGH/CRITICAL finding for the listed rules
# - PASS targets: expect 0 findings (or only PASS) — proves low false-positive rate
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkTarget:
    id: str
    platform: str          # "github" | "gitlab"
    project_id: str        # "owner/repo" or "group/project"
    mr_iid: int            # PR/MR number
    expected_rules: list[str]  # rule IDs expected to fire
    expected_outcome: str  # "FINDING" | "PASS"
    description: str       # human summary of what the target tests
    notes: str = ""

TARGETS: list[BenchmarkTarget] = [
    # --- FINDING targets (real bugs already confirmed) -----------------------
    BenchmarkTarget(
        id="aiokafka-1164",
        platform="github",
        project_id="aio-libs/aiokafka",
        mr_iid=1164,
        expected_rules=["RULE_06"],
        expected_outcome="FINDING",
        description="Retry backoff without jitter in _proc_fetch_request",
        notes="Fixed delay in request_fetch_backoff; issue filed #1165",
    ),
    BenchmarkTarget(
        id="vllm-34981",
        platform="github",
        project_id="vllm-project/vllm",
        mr_iid=34981,
        expected_rules=["RULE_06"],
        expected_outcome="FINDING",
        description="LoRA config loading retry uses interval *= 2 with no jitter",
        notes="Multiple vLLM workers loading same adapter → thundering herd; issue filed #44245",
    ),
    BenchmarkTarget(
        id="nerdbox-218",
        platform="github",
        project_id="containerd/nerdbox",
        mr_iid=218,
        expected_rules=["RULE_06", "RULE_11"],
        expected_outcome="FINDING",
        description="Windows shim pipe wait: fixed retryDelay + ctx.Done masking",
        notes=(
            "RULE_06: time.Sleep(retryDelay) 10ms fixed, no jitter. "
            "RULE_11: dialCtx.Done branch returns hardcoded string not ctx.Err(). "
            "Issue filed #219."
        ),
    ),
    BenchmarkTarget(
        id="atlas-6699",
        platform="github",
        project_id="atlanhq/atlas-metastore",
        mr_iid=6699,
        expected_rules=["RULE_09", "RULE_06"],
        expected_outcome="FINDING",
        description="Dual write JanusGraph + Kafka without transactional outbox",
        notes="Java. calculateExponentialBackoff() is pure deterministic. Issues disabled on repo.",
    ),
    BenchmarkTarget(
        id="django-logpipe-943",
        platform="gitlab",
        project_id="thelabnyc/django-logpipe",
        mr_iid=943,
        expected_rules=["RULE_06"],
        expected_outcome="FINDING",
        description="Kinesis re-seek retry with fixed time.sleep(5) on throttle errors",
        notes="Issue #15 filed at gitlab.com/thelabnyc/django-logpipe/-/work_items/15",
    ),
    # --- PASS targets (correct implementations — low false-positive proof) ---
    BenchmarkTarget(
        id="temporalio-sagas",
        platform="github",
        project_id="temporalio/samples-go",
        mr_iid=161,
        expected_rules=[],
        expected_outcome="PASS",
        description="Temporal Go saga sample — correct compensation handlers",
        notes="RULE_03 should PASS (compensation handlers present). RULE_06 may surface on workflow timers.",
    ),
]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkRun:
    target: BenchmarkTarget
    surfaces_detected: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    outcome: str = "UNKNOWN"   # "PASS" | "FINDING" | "ERROR"
    correct: bool = False
    error: str = ""
    duration_s: float = 0.0


async def run_target_dry(target: BenchmarkTarget) -> BenchmarkRun:
    """Surface-detector only (no Gemini). Fast sanity check."""
    import httpx
    from quorum.detector import detect_surfaces

    run = BenchmarkRun(target=target)
    t0 = asyncio.get_event_loop().time()

    try:
        if target.platform == "github":
            url = f"https://api.github.com/repos/{target.project_id}/pulls/{target.mr_iid}/files"
            headers = {"Accept": "application/vnd.github.v3+json"}
        else:
            url = (
                f"https://gitlab.com/api/v4/projects/"
                f"{target.project_id.replace('/', '%2F')}"
                f"/merge_requests/{target.mr_iid}/diffs"
            )
            headers = {}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # Reconstruct a unified diff text from the API response
        if target.platform == "github":
            diff_lines = []
            for f in data:
                diff_lines.append(f"--- a/{f['filename']}\n+++ b/{f['filename']}")
                diff_lines.append(f.get("patch", ""))
            diff = "\n".join(diff_lines)
        else:
            diff_lines = []
            for d in data:
                diff_lines.append(f"--- a/{d.get('old_path', '')}\n+++ b/{d.get('new_path', '')}")
                diff_lines.append(d.get("diff", ""))
            diff = "\n".join(diff_lines)

        triggered = detect_surfaces(diff)
        run.surfaces_detected = [r.id for r in triggered]

        # Evaluate: for PASS targets expect no FINDING rules; for FINDING targets expect ≥1 match
        if target.expected_outcome == "PASS":
            run.outcome = "PASS"
            run.correct = len(run.surfaces_detected) == 0 or all(
                r not in target.expected_rules for r in run.surfaces_detected
            )
        else:
            run.outcome = "FINDING" if run.surfaces_detected else "PASS"
            run.correct = any(r in run.surfaces_detected for r in target.expected_rules)

    except Exception as exc:
        run.outcome = "ERROR"
        run.error = str(exc)[:200]

    run.duration_s = asyncio.get_event_loop().time() - t0
    return run


async def run_target_full(target: BenchmarkTarget, settings) -> BenchmarkRun:
    """Full three-stage pipeline (Gemini review, no comment posted)."""
    from quorum.agent import QuorumAgent
    from quorum.gitlab_client import make_client
    from quorum.github_client import make_github_client

    run = BenchmarkRun(target=target)
    t0 = asyncio.get_event_loop().time()

    try:
        agent = QuorumAgent(settings)
        client = (
            make_github_client(settings)
            if target.platform == "github"
            else make_client(settings, rest_only=True)
        )

        async with client.connect():
            result = await agent.review(
                project_id=target.project_id,
                mr_iid=target.mr_iid,
                client=client,
                post_comment=False,
            )

        run.surfaces_detected = [f.rule_id for f in result.findings]
        run.findings = [
            {
                "rule_id": f.rule_id,
                "severity": f.severity.value,
                "confidence": f.confidence,
                "title": f.title,
            }
            for f in result.findings
            if f.severity.value != "PASS"
        ]

        actual_finding_rules = {f["rule_id"] for f in run.findings}

        if target.expected_outcome == "PASS":
            run.outcome = "PASS" if not run.findings else "FINDING"
            run.correct = run.outcome == "PASS"
        else:
            run.outcome = "FINDING" if run.findings else "PASS"
            run.correct = any(r in actual_finding_rules for r in target.expected_rules)

    except Exception as exc:
        run.outcome = "ERROR"
        run.error = str(exc)[:300]

    run.duration_s = asyncio.get_event_loop().time() - t0
    return run


# ---------------------------------------------------------------------------
# Report renderer
# ---------------------------------------------------------------------------

def render_markdown(runs: list[BenchmarkRun], dry_run: bool) -> str:
    date_str = datetime.date.today().isoformat()
    mode_str = "surface detector only (Stage 1 — no Gemini)" if dry_run else "full pipeline (all 3 stages)"

    correct = sum(1 for r in runs if r.correct)
    errors = sum(1 for r in runs if r.outcome == "ERROR")
    total = len(runs)

    lines = [
        "# Quorum Benchmark Results",
        "",
        f"**Date:** {date_str}  ",
        f"**Mode:** {mode_str}  ",
        f"**Targets:** {total}  ",
        f"**Correct:** {correct}/{total}  ",
        f"**Errors:** {errors}  ",
        "",
        "---",
        "",
        "## Results",
        "",
        "| ID | Platform | Project | Expected | Outcome | Correct | Surfaces | Duration |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for run in runs:
        t = run.target
        correct_icon = "✅" if run.correct else ("❌" if run.outcome != "ERROR" else "⚠️")
        surfaces = ", ".join(run.surfaces_detected) if run.surfaces_detected else "—"
        dur = f"{run.duration_s:.1f}s"
        lines.append(
            f"| {t.id} | {t.platform} | `{t.project_id}` | {t.expected_outcome} "
            f"| {run.outcome} | {correct_icon} | {surfaces} | {dur} |"
        )

    lines += ["", "---", "", "## Per-target detail", ""]

    for run in runs:
        t = run.target
        lines.append(f"### {t.id}")
        lines.append(f"**{t.platform.upper()} · `{t.project_id}` · #{t.mr_iid}**  ")
        lines.append(f"_{t.description}_")
        if t.notes:
            lines.append(f"> {t.notes}")
        lines.append("")
        if run.outcome == "ERROR":
            lines.append(f"**Error:** `{run.error}`")
        else:
            lines.append(f"**Surfaces detected:** {', '.join(run.surfaces_detected) or 'none'}  ")
            lines.append(f"**Expected:** {t.expected_outcome}  ")
            lines.append(f"**Result:** {'✅ correct' if run.correct else '❌ incorrect'}  ")
            if run.findings:
                lines.append("")
                lines.append("Findings:")
                for f in run.findings:
                    lines.append(
                        f"- **{f['rule_id']}** {f['severity']} {f['confidence']}% — {f['title']}"
                    )
        lines.append("")

    lines += [
        "---",
        "",
        f"_Generated by `scripts/run_benchmark.py` · Quorum v{_get_version()} · {date_str}_",
    ]

    return "\n".join(lines)


def _get_version() -> str:
    try:
        from quorum import __version__
        return __version__
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Surface detector only, no Gemini calls")
    parser.add_argument("--output", default="docs/BENCHMARK.md", help="Output Markdown path")
    parser.add_argument("--target", default=None, help="Run only this target ID")
    parser.add_argument("--json", action="store_true", help="Also write JSON results alongside Markdown")
    args = parser.parse_args()

    targets = TARGETS
    if args.target:
        targets = [t for t in TARGETS if t.id == args.target]
        if not targets:
            print(f"Unknown target '{args.target}'. Available: {[t.id for t in TARGETS]}", file=sys.stderr)
            sys.exit(1)

    async def _run_all():
        if args.dry_run:
            print(f"Running surface detector only on {len(targets)} target(s)…", file=sys.stderr)
            runs = await asyncio.gather(*[run_target_dry(t) for t in targets])
        else:
            from quorum.config import get_settings
            settings = get_settings()
            print(f"Running full pipeline on {len(targets)} target(s) (Gemini API calls)…", file=sys.stderr)
            runs = []
            for t in targets:
                print(f"  → {t.id}…", file=sys.stderr)
                run = await run_target_full(t, settings)
                runs.append(run)
                status = "✅" if run.correct else ("⚠️" if run.outcome == "ERROR" else "❌")
                print(f"     {status} {run.outcome} ({run.duration_s:.1f}s)", file=sys.stderr)
        return list(runs)

    runs = asyncio.run(_run_all())

    md = render_markdown(runs, dry_run=args.dry_run)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md)
    print(f"\nResults written to {out_path}", file=sys.stderr)

    if args.json:
        json_path = out_path.with_suffix(".json")
        json_data = [
            {
                "id": r.target.id,
                "platform": r.target.platform,
                "project_id": r.target.project_id,
                "mr_iid": r.target.mr_iid,
                "expected_outcome": r.target.expected_outcome,
                "actual_outcome": r.outcome,
                "correct": r.correct,
                "surfaces": r.surfaces_detected,
                "findings": r.findings,
                "duration_s": r.duration_s,
                "error": r.error,
            }
            for r in runs
        ]
        json_path.write_text(json.dumps(json_data, indent=2))
        print(f"JSON results written to {json_path}", file=sys.stderr)

    correct = sum(1 for r in runs if r.correct)
    total = len(runs)
    print(f"\nScore: {correct}/{total} correct", file=sys.stderr)
    if correct < total:
        sys.exit(1)


if __name__ == "__main__":
    main()
