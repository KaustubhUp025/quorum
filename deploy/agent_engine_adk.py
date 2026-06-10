#!/usr/bin/env python3
"""Deploy Quorum's ADK agent to Vertex AI Agent Engine.

This enables the Agent Platform Playground (console.cloud.google.com/agent-platform)
for interactive judge demos.

IMPORTANT: Uses vertexai.agent_engines (NEW API), not vertexai.preview.reasoning_engines
(OLD API). The Playground only recognises agents deployed via the new API.

Usage:
    pip install "google-adk>=1.0.0" "google-cloud-aiplatform[agent_engines]>=1.153.1"
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

The original deploy/agent_engine.py (Queryable interface) remains unchanged
and can be queried via Python SDK / REST API.
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


def _read_secret(project: str, secret_id: str) -> str:
    """Read a secret from Secret Manager at deploy time."""
    result = subprocess.run(
        ["gcloud", "secrets", "versions", "access", "latest",
         "--secret", secret_id, "--project", project],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"⚠️  Could not read secret '{secret_id}': {result.stderr.strip()}")
        return ""
    return result.stdout.strip()


def deploy(project: str, region: str) -> str:
    import os
    import vertexai
    # NEW API — required for Agent Platform Playground recognition
    from vertexai import agent_engines

    # Read tokens from Secret Manager FIRST and put them in the environment BEFORE
    # importing the agent. adk_app builds the GitLab MCPToolset at import time from
    # QUORUM_GITLAB_TOKEN + QUORUM_MCP_GATEWAY_URL; if those are absent at import,
    # the toolset silently drops and the deployed agent loses its MCP tools.
    print("==> Reading credentials from Secret Manager...")
    gitlab_token = _read_secret(project, "quorum-gitlab-token")
    github_token = _read_secret(project, "quorum-github-token")
    gateway_url = os.getenv(
        "QUORUM_MCP_GATEWAY_URL",
        "https://quorum-mcp-gateway-3fnjzg6adq-uc.a.run.app/mcp",
    )
    if gitlab_token:
        os.environ["QUORUM_GITLAB_TOKEN"] = gitlab_token
    os.environ["QUORUM_MCP_GATEWAY_URL"] = gateway_url

    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from quorum.adk_app import root_agent  # noqa: F401

    if root_agent is None:
        print("❌ google-adk is not installed. Run: pip install 'google-adk>=1.0.0'")
        sys.exit(1)

    # Fail loudly if the GitLab MCP toolset did not attach — otherwise we'd deploy
    # an agent missing its partner-MCP integration (4 tools expected, not 3).
    n_tools = len(getattr(root_agent, "tools", []))
    has_mcp = any(type(t).__name__ == "MCPToolset" for t in getattr(root_agent, "tools", []))
    print(f"==> root_agent tools: {n_tools} (GitLab MCPToolset attached: {has_mcp})")
    if not has_mcp:
        print("❌ GitLab MCPToolset NOT attached — aborting (check QUORUM_GITLAB_TOKEN).")
        sys.exit(1)

    bucket_name = f"quorum-ae-staging-{project}"[:63]
    staging_bucket = ensure_staging_bucket(project, region, bucket_name)

    vertexai.init(project=project, location=region, staging_bucket=staging_bucket)

    print(f"==> Deploying ADK agent via vertexai.agent_engines ({project} / {region})...")
    print("    This uploads the package and provisions the engine — takes ~3 minutes.")

    # Runtime requirements for the Agent Engine container.
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

    # Use vertexai.agent_engines.create() — this is what the Playground recognises.
    # Passing root_agent (BaseAgent) directly; AdkApp wrapping is automatic.
    #
    # QUORUM_CREATE_FIX_MRS=true: allows the Playground to demonstrate fix MR creation.
    # Over-usage protection: run_review() defaults to dry_run=True, so fix MRs are only
    # created when the user explicitly passes dry_run=False AND a CRITICAL finding is found.
    engine = agent_engines.create(
        agent_engine=root_agent,
        requirements=requirements,
        display_name="Quorum — ADK Coordination Reviewer",
        description=(
            "ADK-based Quorum agent. Three conversational tools: run_review, "
            "explain_rule, list_rules. Supports the Agent Platform Playground."
        ),
        env_vars={
            "QUORUM_CREATE_FIX_MRS": "true",
            # Vertex AI — avoids depleted AI Studio key; uses engine SA's ADC
            "QUORUM_USE_VERTEX_AI": "true",
            "QUORUM_GOOGLE_CLOUD_PROJECT": project,
            "QUORUM_GOOGLE_CLOUD_LOCATION": region,
            # Tokens injected at deploy time from Secret Manager
            "QUORUM_GITLAB_TOKEN": gitlab_token,
            "QUORUM_GITHUB_TOKEN": github_token,
        },
    )

    resource_name = engine.resource_name
    engine_id = resource_name.split("/")[-1]

    print(f"\n✅ ADK Agent Engine deployed via new API!")
    print(f"   Resource:   {resource_name}")
    print(f"   Playground: https://console.cloud.google.com/agent-platform/agents/{engine_id}/playground?project={project}")
    print()
    print("   In the Playground, open the 'Playground' tab.")
    print("   Example prompts:")
    print("     • 'review quorum-hackathon/quorum-demo MR 1 dry run'")
    print("     • 'explain RULE_01'")
    print("     • 'list all rules'")
    print()
    print("   Test from Python:")
    print(f"     import vertexai")
    print(f"     from vertexai import agent_engines")
    print(f"     vertexai.init(project='{project}', location='{region}')")
    print(f"     engine = agent_engines.get('{resource_name}')")
    print(f"     for event in engine.stream_query(message='list all rules', user_id='test'):")
    print(f"         print(event)")

    return resource_name


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy Quorum's ADK agent to Vertex AI Agent Engine (new API)"
    )
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument("--region", default="us-central1")
    args = parser.parse_args()

    deploy(args.project, args.region)


if __name__ == "__main__":
    main()
