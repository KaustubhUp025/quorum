"""FastAPI webhook server for Google Cloud Run deployment.

Receives GitLab MR webhooks and triggers Quorum reviews asynchronously.
This is the Cloud Run entry point that satisfies the "deploy on Google Cloud" requirement.
"""

from __future__ import annotations

import hmac
import hashlib
import structlog
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Header
from fastapi.responses import JSONResponse

from quorum import __version__
from quorum.config import Settings
from quorum.gitlab_client import GitLabMCPClient

log = structlog.get_logger(__name__)


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title="Quorum",
        description="Distributed-coordination MR linter — GitLab webhook receiver",
        version=__version__,
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.post("/webhook/gitlab")
    async def gitlab_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
        x_gitlab_token: str | None = Header(default=None, alias="X-Gitlab-Token"),
        x_gitlab_event: str | None = Header(default=None, alias="X-Gitlab-Event"),
    ) -> JSONResponse:
        payload = await request.json()

        if x_gitlab_event not in ("Merge Request Hook", "merge_request"):
            return JSONResponse({"status": "ignored", "reason": "not a merge_request event"})

        mr = payload.get("object_attributes", {})
        mr_action = mr.get("action", "")

        if mr_action not in ("open", "reopen", "update"):
            return JSONResponse({"status": "ignored", "reason": f"mr action '{mr_action}' not reviewed"})

        project = payload.get("project", {})
        project_id: str = str(project.get("id", ""))
        mr_iid: int = int(mr.get("iid", 0))

        if not project_id or not mr_iid:
            raise HTTPException(status_code=400, detail="Missing project_id or mr_iid in payload")

        log.info("webhook_received", project_id=project_id, mr_iid=mr_iid, action=mr_action)

        background_tasks.add_task(
            _run_review_background,
            project_id=project_id,
            mr_iid=mr_iid,
            settings=settings,
        )

        return JSONResponse({"status": "accepted", "project_id": project_id, "mr_iid": mr_iid})

    return app


async def _run_review_background(
    project_id: str,
    mr_iid: int,
    settings: Settings,
) -> None:
    from quorum.agent import QuorumAgent

    try:
        agent = QuorumAgent(settings)
        gitlab = GitLabMCPClient(settings.gitlab_mcp_url, settings.gitlab_token)
        async with gitlab.connect():
            result = await agent.review(
                project_id=project_id,
                mr_iid=mr_iid,
                mcp=gitlab,
                post_comment=True,
            )
        log.info(
            "background_review_complete",
            project_id=project_id,
            mr_iid=mr_iid,
            critical=result.critical_count,
            blocked=result.blocked,
        )
    except Exception as exc:
        log.error(
            "background_review_failed",
            project_id=project_id,
            mr_iid=mr_iid,
            error=str(exc),
            exc_info=True,
        )
