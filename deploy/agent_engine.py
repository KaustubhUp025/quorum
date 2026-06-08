#!/usr/bin/env python3
"""Deploy Quorum's DeepReasoningAgent to Vertex AI Agent Engine.

Usage:
    python deploy/agent_engine.py --project gen-lang-client-0294573094 [--build]

Prerequisites:
    pip install build
    gcloud auth login
    gcloud auth application-default login

The deployed engine is accessible at:
    https://console.cloud.google.com/vertex-ai/reasoning-engines?project=<PROJECT_ID>
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def build_sdist(repo_root: Path) -> Path:
    """Build a source distribution to bundle into the Agent Engine image."""
    print("==> Building source distribution...")
    subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--outdir", str(repo_root / "dist")],
        cwd=repo_root,
        check=True,
    )
    sdists = sorted((repo_root / "dist").glob("quorum-*.tar.gz"))
    if not sdists:
        raise RuntimeError("No sdist found after build. Is 'build' installed? pip install build")
    latest = sdists[-1]
    print(f"    Built: {latest.name}")
    return latest


def ensure_staging_bucket(project: str, region: str, bucket_name: str) -> str:
    """Create the GCS staging bucket if it doesn't already exist."""
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


def deploy(project: str, region: str, sdist_path: Path | None = None) -> str:
    import vertexai
    from vertexai.preview import reasoning_engines

    sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
    from quorum.reasoning_engine_app import QuorumReasoningEngine

    # GCS bucket used to stage the sdist + requirements before Agent Engine picks them up.
    # Name is derived from the project ID (globally unique, ≤63 chars).
    bucket_name = f"quorum-ae-staging-{project}"[:63]
    staging_bucket = ensure_staging_bucket(project, region, bucket_name)

    vertexai.init(project=project, location=region, staging_bucket=staging_bucket)

    print(f"==> Deploying to Agent Engine ({project} / {region})...")
    print("    This uploads the package and provisions the engine — takes ~3 minutes.")

    extra_packages: list[str] = [str(sdist_path)] if sdist_path else []

    # Runtime requirements: everything quorum needs at inference time.
    # google-cloud-aiplatform is pre-installed in the Agent Engine runtime.
    # fastapi / uvicorn are not needed (Agent Engine is not a web server).
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
        "pyyaml>=6.0.3",
    ]

    engine = reasoning_engines.ReasoningEngine.create(
        QuorumReasoningEngine(),
        requirements=requirements,
        display_name="Quorum — Distributed Coordination Reviewer",
        description=(
            "Three-stage agentic pipeline: SurfaceDetectorAgent → DeepReasoningAgent "
            "(Gemini 2.5 Pro) → ReportFormatterAgent. Detects distributed systems "
            "coordination bugs in GitLab MRs and GitHub PRs."
        ),
        extra_packages=extra_packages,
        # Pin to Python 3.10 — matches the local dev environment and pyproject.toml
        # requires-python = ">=3.10".
        sys_version="3.10",
    )

    resource_name = engine.resource_name
    # resource_name looks like:
    #   projects/PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID
    engine_id = resource_name.split("/")[-1]

    print(f"\n✅ Agent Engine deployed!")
    print(f"   Resource:   {resource_name}")
    print(f"   Playground: https://console.cloud.google.com/vertex-ai/reasoning-engines/{engine_id}?project={project}")
    print()
    print("   Test from Python:")
    print(f"     import vertexai")
    print(f"     from vertexai.preview import reasoning_engines")
    print(f"     vertexai.init(project='{project}', location='{region}')")
    print(f"     engine = reasoning_engines.ReasoningEngine('{resource_name}')")
    print(f"     result = engine.query(")
    print(f"         project_id='quorum-hackathon/quorum-demo',")
    print(f"         mr_iid=1,")
    print(f"         dry_run=True,")
    print(f"     )")
    print(f"     print(result['summary'])")

    return resource_name


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy Quorum to Vertex AI Agent Engine")
    parser.add_argument("--project", required=True, help="GCP project ID")
    parser.add_argument("--region", default="us-central1")
    parser.add_argument(
        "--build",
        action="store_true",
        help="Build a source distribution first (requires: pip install build)",
    )
    parser.add_argument(
        "--sdist",
        help="Path to an existing .tar.gz sdist (skips --build)",
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
