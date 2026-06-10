"""FastAPI webhook server for Google Cloud Run deployment.

Receives GitLab MR webhooks and triggers Quorum reviews asynchronously.
This is the Cloud Run entry point that satisfies the "deploy on Google Cloud" requirement.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import structlog
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

from quorum import __version__
from quorum.config import Settings
from quorum.gitlab_client import make_client

log = structlog.get_logger(__name__)

# Directory holding the Quorum Live web UI (demo.html, logo.svg, favicon.svg).
_STATIC_DIR = Path(__file__).parent / "static"

# Public-demo guard: cap concurrent live reviews so a paste-the-URL playground
# can't fan out into unbounded Gemini / GitLab traffic. The event loop is
# single-threaded so the check-then-increment below is atomic (no await between).
_MAX_CONCURRENT_DEMO_REVIEWS = 2
_active_demo_reviews = 0

# Heartbeat interval for the SSE demo stream — keeps the connection alive through
# long idle gaps (e.g. Gemini thinking before its first tool call).
_SSE_KEEPALIVE_SECONDS = 10


def parse_mr_url(url: str) -> dict | None:
    """Parse a GitLab MR or GitHub PR URL into platform / project_id / iid.

    GitLab: https://gitlab.com/group/sub/project/-/merge_requests/123
    GitHub: https://github.com/owner/repo/pull/123
    Returns None if the URL doesn't match either shape.
    """
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")

    # GitHub PR: owner/repo/pull/N
    gh = re.match(r"^([^/]+/[^/]+)/pull/(\d+)", path)
    if "github" in host and gh:
        return {"platform": "github", "project_id": gh.group(1), "iid": int(gh.group(2))}

    # GitLab MR: <project-path>/-/merge_requests/N  (project path may be nested)
    gl = re.match(r"^(.+?)/-/merge_requests/(\d+)", path)
    if gl:
        return {"platform": "gitlab", "project_id": gl.group(1), "iid": int(gl.group(2))}

    return None


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

    # Quorum Live web UI is a static page that talks to the SSE endpoint below.
    # Allow cross-origin GETs so the page works when served from any host.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/demo")

    @app.get("/demo")
    async def demo_page() -> FileResponse:
        page = _STATIC_DIR / "demo.html"
        if not page.exists():
            raise HTTPException(status_code=404, detail="demo.html not found")
        return FileResponse(page, media_type="text/html")

    @app.get("/logo.svg")
    async def logo() -> FileResponse:
        f = _STATIC_DIR / "logo.svg"
        if not f.exists():
            raise HTTPException(status_code=404, detail="logo.svg not found")
        return FileResponse(f, media_type="image/svg+xml")

    @app.get("/favicon.svg")
    async def favicon() -> FileResponse:
        f = _STATIC_DIR / "favicon.svg"
        if not f.exists():
            raise HTTPException(status_code=404, detail="favicon.svg not found")
        return FileResponse(f, media_type="image/svg+xml")

    @app.get("/demo/parse-url")
    async def demo_parse_url(url: str = Query(...)) -> JSONResponse:
        parsed = parse_mr_url(url)
        if not parsed:
            return JSONResponse(
                {"ok": False, "error": "Not a recognised GitLab MR or GitHub PR URL."},
                status_code=400,
            )
        return JSONResponse({"ok": True, **parsed})

    @app.get("/demo/stream")
    async def demo_stream(
        url: str = Query(...),
        mode: str = Query("dry_run", pattern="^(dry_run|post|fix)$"),
    ) -> StreamingResponse:
        parsed = parse_mr_url(url)
        if not parsed:
            raise HTTPException(status_code=400, detail="Invalid MR/PR URL")

        return StreamingResponse(
            _demo_event_stream(parsed, mode, settings),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # disable proxy buffering for live SSE
            },
        )

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


def _unwrap_exc(exc: BaseException) -> BaseException:
    """Dig through ExceptionGroup/TaskGroup and tenacity RetryError wrappers to
    the first concrete cause, so error messages are actionable rather than e.g.
    "RetryError[<Future at 0x… raised ClientError>]"."""
    seen: set[int] = set()
    real = exc
    while id(real) not in seen:
        seen.add(id(real))
        subs = getattr(real, "exceptions", None)  # ExceptionGroup
        if subs:
            real = subs[0]
            continue
        last = getattr(real, "last_attempt", None)  # tenacity.RetryError
        if last is not None:
            inner = last.exception() if hasattr(last, "exception") else None
            if inner is not None:
                real = inner
                continue
        break
    return real


def _friendly_error(exc: BaseException) -> str:
    """Map a raw API error to a short, user-facing message."""
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()
    if "resource_exhausted" in low or "429" in low or "quota" in low or "rate limit" in low:
        return (
            "The model is rate-limited right now (quota exhausted). "
            "Please try again in a minute."
        )
    if "deadline" in low or "timeout" in low:
        return "The review timed out. Please try again."
    return f"{name}: {msg[:280]}"


def _sse(event: dict) -> str:
    """Serialise one event as a Server-Sent Events frame."""
    return f"data: {json.dumps(event)}\n\n"


async def _demo_event_stream(parsed: dict, mode: str, settings: Settings):
    """Run a live review and yield pipeline events as SSE frames.

    The QuorumAgent's `_emit` hook pushes events onto an in-process queue while
    the review runs as a background task; we drain the queue to the client.
    """
    global _active_demo_reviews

    platform = parsed["platform"]
    project_id = parsed["project_id"]
    iid = parsed["iid"]

    # Concurrency guard (atomic in the single-threaded loop — no await here).
    if _active_demo_reviews >= _MAX_CONCURRENT_DEMO_REVIEWS:
        yield _sse({"event": "busy",
                    "message": "Too many live reviews in progress. Try again shortly."})
        return
    _active_demo_reviews += 1

    queue: asyncio.Queue = asyncio.Queue()

    def emit(stage: str, **data) -> None:
        queue.put_nowait({"event": stage, **data})

    async def run() -> None:
        from quorum.agent import QuorumAgent
        from quorum.github_client import make_github_client
        from quorum.gitlab_client import make_client as _make_client

        try:
            # 'fix' mode opts into draft fix-MR creation; clone settings so the
            # per-request override never leaks into the shared webhook config.
            run_settings = settings
            if mode == "fix":
                run_settings = settings.model_copy(update={"create_fix_mrs": True})

            agent = QuorumAgent(run_settings)
            agent._emit = emit

            if platform == "github":
                client = make_github_client(run_settings)
            else:
                client = _make_client(run_settings, project_id=project_id)

            async with client.connect():
                result = await agent.review(
                    project_id=project_id,
                    mr_iid=iid,
                    client=client,
                    post_comment=(mode != "dry_run"),
                    # The demo must always run a real review. Without force, the
                    # "already reviewed" duplicate guard short-circuits post/fix
                    # runs on a previously-reviewed MR and returns 0 findings.
                    force=True,
                )
            emit(
                "done",
                delivery=result.delivery,
                blocked=result.blocked,
                total=len(result.findings),
            )
        except asyncio.CancelledError:
            # The client disconnected (browser closed / navigated away) and the
            # streaming generator cancelled us. Not an error — exit quietly.
            log.info("demo_stream_cancelled", project_id=project_id, iid=iid)
            raise
        except BaseException as exc:  # surface a clean error to the page
            # Unwrap the wrappers that hide the real cause so the page shows
            # something actionable: ExceptionGroup/TaskGroup ("unhandled errors
            # in a TaskGroup") and tenacity's RetryError ("RetryError[<Future …>]").
            import traceback as _tb
            real = _unwrap_exc(exc)
            log.error(
                "demo_stream_failed",
                project_id=project_id,
                iid=iid,
                error=str(real)[:300],
                error_type=type(real).__name__,
                traceback=_tb.format_exc()[-1500:],
            )
            emit("error", message=_friendly_error(real))
        finally:
            queue.put_nowait(None)  # sentinel: stream complete

    task = asyncio.create_task(run())
    try:
        # Announce immediately so the page knows the stream is live.
        yield _sse({"event": "connected", "platform": platform,
                    "project_id": project_id, "iid": iid, "mode": mode})
        while True:
            # Gemini can "think" for 60-90s between events (e.g. before its first
            # tool call), leaving the SSE connection idle. Cloud Run / proxies /
            # the browser drop an idle connection, so emit an SSE keepalive comment
            # every few seconds when no real event is queued. Comment lines
            # (": ...") are ignored by EventSource but keep the socket warm.
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_SSE_KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item is None:
                break
            yield _sse(item)
    finally:
        _active_demo_reviews -= 1
        if not task.done():
            task.cancel()


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
