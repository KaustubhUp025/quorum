#!/usr/bin/env python3
"""Deploy Quorum's ADK agent to Vertex AI Agent Engine.

This enables the Agent Platform Playground (console.cloud.google.com/agent-platform)
for interactive judge demos — the Playground only supports ADK-based agents.

Usage:
    pip install "google-adk>=1.0.0" google-cloud-aiplatform[agent_engines]
    gcloud auth application-default login
    python deploy/agent_engine_adk.py --project gen-lang-client-0294573094

The deployed ADK agent exposes three tools in the Playground chat:
    run_review(project_id, mr_iid, platform, dry_run)
    explain_rule(rule_id)
    list_rules()

Example Playground session:
    User:  "review quorum-hackathon/quorum-demo MR 1 dry run"
    Quorum: [calls run_review] "Found 5 issues: RULE_01 CRITICAL (100%)..."
    User:  "explain RULE_09"
    Quorum: [calls explain_rule] "RULE_09 — Transactional Outbox Missing..."

The original deploy/agent_engine.py (Queryable interface) is kept unchanged
and remains the deployed non-ADK engine used by the CLI and webhook.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def ensure_staging_bucket(project: str, region: str, bucket_name: str) -> str:
    result = subprocess.run(
        ["gcloud", "storage", "buckets", "describe", f"gs://{bucket_name}",
         "--project", project],
        capture_output=True,
    )
    if result.returncode != 0:
        print(f"==> Creating staging bucket: gs://{bucket_name}")
        subprocess.run(
            ["gcloud", "storage", "buckets", "create", f"gs://{bucket_name}",
             "--project", project,
             "--location", region,
             "--uniform-bucket-level-access"],
            check=True,
        )
    else:
        print(f"==> Using existing staging bucket: gs://{bucket_name}")
    return f"gs://{bucket_name}"


def deploy(project: str, region: str) -> str:
    import vertexai
    from vertexai.preview import reasoning_engines
    from vertexai.preview.reasoning_engines import AdkApp

    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from quorum.adk_app import _pull_secrets, root_agent  # noqa: F401

    if root_agent is None:
        print("❌ google-adk is not installed. Run: pip install 'google-adk>=1.0.0'")
        sys.exit(1)

    bucket_name = f"quorum-ae-staging-{project}"[:63]
    staging_bucket = ensure_staging_bucket(project, region, bucket_name)

    vertexai.init(project=project, location=region, staging_bucket=staging_bucket)

    print(f"==> Deploying ADK agent to Agent Engine ({project} / {region})...")
    print("    This uploads the package and provisions the engine — takes ~3 minutes.")

    adk_app = AdkApp(agent=root_agent, enable_tracing=True)

    # Runtime requirements for the Agent Engine container.
    # google-adk is the new requirement vs. the original agent_engine.py.
    requirements = [
        "quorum @ git+https://github.com/KaustubhUp025/quorum.git",
        "google-adk>=1.0.0",
        "google-cloud-secret-manager>=2.0.0",
        "google-genai>=2.6.0",
        "pydantic>=2.13.4",
        "pydantic-settings>=2.14.1",
        "httpx>=0.28.1",
        "structlog>=25.5.0",
        "tenacity>=9.1.4",
        "python-dotenv>=1.2.2",
        "rich>=15.0.0",
        "mcp>=1.27.1",
        "click>=8.4.1",
        "pyyaml>=6.0.3",
    ]

    engine = reasoning_engines.ReasoningEngine.create(
        adk_app,
        requirements=requirements,
        display_name="Quorum — ADK Coordination Reviewer",
        description=(
            "ADK-based Quorum agent. Three conversational tools: run_review, "
            "explain_rule, list_rules. Supports the Agent Platform Playground."
        ),
        sys_version="3.10",
    )

    resource_name = engine.resource_name
    engine_id = resource_name.split("/")[-1]

    print(f"\n✅ ADK Agent Engine deployed!")
    print(f"   Resource:   {resource_name}")
    print(f"   Playground: https://console.cloud.google.com/agent-platform/runtimes?project={project}")
    print()
    print("   In the Playground, click the engine and open the 'Playground' tab.")
    print("   Example prompts:")
    print("     • 'review quorum-hackathon/quorum-demo MR 1 dry run'")
    print("     • 'explain RULE_01'")
    print("     • 'list all rules'")
    print()
    print("   Test from Python:")
    print(f"     import vertexai")
    print(f"     from vertexai.preview import reasoning_engines")
    print(f"     vertexai.init(project='{project}', location='{region}')")
    print(f"     engine = reasoning_engines.ReasoningEngine('{resource_name}')")
    print(f"     for event in engine.stream_query(message='review quorum-hackathon/quorum-demo MR 1 dry run'):")
    print(f"         print(event)")

    return resource_name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy Quorum's ADK agent to Vertex AI Agent Engine"
    )
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument("--region", default="us-central1")
    args = parser.parse_args()

    deploy(args.project, args.region)


if __name__ == "__main__":
    main()
