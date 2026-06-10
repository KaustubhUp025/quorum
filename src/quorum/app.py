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
from quorum.gitlab_client import make_client

log = structlog.get_logger(__name__)


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(
        title="Quorum",
        description="Distributed-coordination MR linter — GitLab webhook receiver",
        version=__version__,
    )

    if not settings.webhook_secret:
        raise RuntimeError(
            "QUORUM_WEBHOOK_SECRET is not set. "
            "Set it to a strong random secret and configure the same value in "
            "GitLab's webhook settings. Without it, any caller who knows the "
            "Cloud Run URL can trigger reviews and create fix MRs."
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
        # SEC-01: validate webhook secret token when configured
        if not x_gitlab_token or not hmac.compare_digest(
            x_gitlab_token.encode(), settings.webhook_secret.encode()
        ):
            raise HTTPException(status_code=403, detail="Invalid X-Gitlab-Token")

        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        if x_gitlab_event not in ("Merge Request Hook", "merge_request"):
            return JSONResponse({"status": "ignored", "reason": "not a merge_request event"})

        mr = payload.get("object_attributes", {})
        mr_action = mr.get("action", "")

        if mr_action not in ("open", "reopen", "update"):
            return JSONResponse({"status": "ignored", "reason": f"mr action '{mr_action}' not reviewed"})

        # Loop prevention: never review Quorum's own fix MRs. Without this, creating a
        # fix MR fires this same webhook, which reviews the fix and may open another
        # fix MR — an infinite self-triggering loop that spawns MRs and burns API calls.
        source_branch = mr.get("source_branch", "") or ""
        if source_branch.startswith("quorum-fix/"):
            return JSONResponse({"status": "ignored", "reason": "quorum-authored fix MR (loop prevention)"})

        project = payload.get("project", {})
        # Use namespace path (e.g. "group/project") — glab needs this to build a valid
        # git remote URL. Numeric project.id would produce https://gitlab.com/12345.git
        # which glab cannot resolve. Fall back to numeric id only if path is absent.
        project_id: str = (
            project.get("path_with_namespace")
            or str(project.get("id", ""))
        )
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
    import asyncio as _asyncio
    from quorum.agent import QuorumAgent, _verify_fix_pipeline

    try:
        agent = QuorumAgent(settings)
        gitlab = make_client(settings, project_id=project_id)
        async with gitlab.connect():
            result = await agent.review(
                project_id=project_id,
                mr_iid=mr_iid,
                client=gitlab,
                post_comment=True,
            )

            # Phase D: fire-and-forget fix pipeline polling for each fix MR created.
            # Uses asyncio.create_task so the connect context stays open for polling.
            fix_findings = [f for f in result.findings if f.fix_mr_iid]
            if fix_findings and settings.create_fix_mrs:
                timeout = settings.verify_fix_timeout
                tasks = [
                    _asyncio.create_task(
                        _verify_fix_pipeline(
                            finding, gitlab, project_id, mr_iid,
                            timeout_seconds=timeout,
                        )
                    )
                    for finding in fix_findings
                ]
                # Await all polling tasks before the connect context closes.
                await _asyncio.gather(*tasks, return_exceptions=True)

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
            error=str(exc)[:200],
        )
