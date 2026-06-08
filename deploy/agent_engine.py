#!/usr/bin/env python3
"""Deploy Quorum's DeepReasoningAgent to Vertex AI Agent Engine.

Usage:
    python deploy/agent_engine.py --project YOUR_GCP_PROJECT [--region us-central1] [--build]

Prerequisites:
    pip install google-cloud-aiplatform
    gcloud auth login (or GOOGLE_APPLICATION_CREDENTIALS set)

What this does:
    1. Builds a source distribution of the quorum package
    2. Deploys QuorumReasoningEngine to Vertex AI Agent Engine
    3. Prints the resource name and playground URL for judges

The deployed engine is accessible at:
    https://console.cloud.google.com/vertex-ai/reasoning-engines
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import os
from pathlib import Path


def build_sdist(repo_root: Path) -> Path:
    """Build a source distribution for bundling into Agent Engine."""
    print("==> Building source distribution...")
    subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(repo_root / "dist")],
        cwd=repo_root,
        check=True,
    )
    sdists = sorted((repo_root / "dist").glob("quorum-*.tar.gz"))
    if not sdists:
        raise RuntimeError("No sdist found after build. Run: pip install build")
    latest = sdists[-1]
    print(f"    Built: {latest.name}")
    return latest


def deploy(project: str, region: str, sdist_path: Path | None = None) -> None:
    import vertexai
    from vertexai.preview import reasoning_engines

    # We import here (after the package is built and installed) so the wrapper
    # class is available in the current Python environment.
    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from quorum.reasoning_engine_app import QuorumReasoningEngine

    vertexai.init(project=project, location=region)

    print(f"==> Deploying QuorumReasoningEngine to Agent Engine ({project}/{region})...")

    extra_packages: list[str] = []
    if sdist_path:
        extra_packages = [str(sdist_path)]

    # Runtime requirements — everything quorum needs at inference time.
    # google-cloud-aiplatform is already in the runtime environment.
    requirements = [
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
        "fastapi>=0.136.3",
        "pyyaml>=6.0.3",
    ]

    engine = reasoning_engines.ReasoningEngine.create(
        QuorumReasoningEngine(),
        requirements=requirements,
        display_name="Quorum — Distributed Coordination Reviewer",
        description=(
            "Three-stage agentic pipeline for detecting distributed systems "
            "coordination bugs in GitLab MRs and GitHub PRs. "
            "Powered by Gemini 2.5 Pro with GitLab MCP tool calling."
        ),
        extra_packages=extra_packages,
    )

    print(f"\n✅ Agent Engine deployed!")
    print(f"   Resource: {engine.resource_name}")
    print(f"   Playground: https://console.cloud.google.com/vertex-ai/reasoning-engines/{engine.name}?project={project}")
    print(f"\n   Test it now:")
    print(f"   python -c \"")
    print(f"   import vertexai")
    print(f"   from vertexai.preview import reasoning_engines")
    print(f"   vertexai.init(project='{project}', location='{region}')")
    print(f"   engine = reasoning_engines.ReasoningEngine('{engine.resource_name}')")
    print(f"   print(engine.query(project_id='quorum-hackathon/quorum-demo', mr_iid=1, dry_run=True))")
    print(f"   \"")

    return engine.resource_name


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy Quorum to Vertex AI Agent Engine")
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument("--region", default="us-central1", help="GCP region (default: us-central1)")
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build source distribution before deploying (requires 'pip install build')",
    )
    parser.add_argument(
        "--sdist",
        help="Path to existing .tar.gz sdist (skips --build)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent

    sdist_path: Path | None = None
    if args.sdist:
        sdist_path = Path(args.sdist)
    elif args.build:
        sdist_path = build_sdist(repo_root)

    deploy(args.project, args.region, sdist_path)


if __name__ == "__main__":
    main()
