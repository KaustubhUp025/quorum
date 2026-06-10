"""FastAPI webhook server for Google Cloud Run deployment.

Receives GitLab MR webhooks and triggers Quorum reviews asynchronously.
This is the Cloud Run entry point that satisfies the "deploy on Google Cloud" requirement.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import re
import time
import uuid
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

# In-memory store for the stateful 3-step demo (Dry run → Post → Fix). Step 1
# stores the analysed ReviewResult under a uuid; Steps 2/3 act on it without
# re-analysing. Per-instance only — fine for the single warm demo instance
# (min-instances=1); a lost session just means re-running the dry run. Bounded
# by TTL + size so it can't grow without limit.
_demo_sessions: dict[str, dict] = {}
_SESSION_TTL_SECONDS = 1800
_SESSION_MAX = 50


def _gc_sessions() -> None:
    now = time.time()
    for k in [k for k, v in _demo_sessions.items() if now - v["created"] > _SESSION_TTL_SECONDS]:
        _demo_sessions.pop(k, None)
    if len(_demo_sessions) > _SESSION_MAX:
        oldest = sorted(_demo_sessions, key=lambda k: _demo_sessions[k]["created"])
        for k in oldest[: len(_demo_sessions) - _SESSION_MAX]:
            _demo_sessions.pop(k, None)


def _store_session(data: dict) -> str:
    _gc_sessions()
    sid = uuid.uuid4().hex
    data["created"] = time.time()
    _demo_sessions[sid] = data
    return sid


def _get_session(sid: str) -> dict | None:
    s = _demo_sessions.get(sid)
    if not s:
        return None
    if time.time() - s["created"] > _SESSION_TTL_SECONDS:
        _demo_sessions.pop(sid, None)
        return None
    return s


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

    _SSE_HEADERS = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # disable proxy buffering for live SSE
    }

    @app.get("/demo/stream")
    async def demo_stream(
        url: str = Query(...),
        # mode kept for backward-compat; the stateful flow always analyses here
        # (Step 1) and posts/fixes via the dedicated endpoints below.
        mode: str = Query("dry_run", pattern="^(dry_run|post|fix)$"),
    ) -> StreamingResponse:
        parsed = parse_mr_url(url)
        if not parsed:
            raise HTTPException(status_code=400, detail="Invalid MR/PR URL")
        connected = {"platform": parsed["platform"], "project_id": parsed["project_id"],
                     "iid": parsed["iid"], "phase": "analyze"}
        return StreamingResponse(
            _sse_stream(connected, _dry_run_worker(parsed, settings)),
            media_type="text/event-stream", headers=_SSE_HEADERS,
        )

    @app.get("/demo/post-stream")
    async def demo_post_stream(session: str = Query(...)) -> StreamingResponse:
        sess = _get_session(session)
        if not sess:
            raise HTTPException(status_code=404, detail="Session expired — re-run the dry run.")
        connected = {"phase": "post", "project_id": sess["project_id"], "iid": sess["mr_iid"]}
        return StreamingResponse(
            _sse_stream(connected, _post_worker(sess, settings)),
            media_type="text/event-stream", headers=_SSE_HEADERS,
        )

    @app.get("/demo/fix-stream")
    async def demo_fix_stream(session: str = Query(...)) -> StreamingResponse:
        sess = _get_session(session)
        if not sess:
            raise HTTPException(status_code=404, detail="Session expired — re-run the dry run.")
        connected = {"phase": "fix", "project_id": sess["project_id"], "iid": sess["mr_iid"]}
        return StreamingResponse(
            _sse_stream(connected, _fix_worker(sess, settings)),
            media_type="text/event-stream", headers=_SSE_HEADERS,
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


async def _sse_stream(connected: dict, worker):
    """Generic SSE driver for the demo: emit `connected`, run `worker(emit)` as a
    background task, drain its event queue with keepalives, and surface a clean
    error. `worker` is an async callable taking the `emit(stage, **data)` fn.

    Shared by all three demo steps (analyze / post / fix) so keepalive, error
    unwrapping and the concurrency guard live in one place.
    """
    global _active_demo_reviews

    # Concurrency guard (atomic in the single-threaded loop — no await here).
    if _active_demo_reviews >= _MAX_CONCURRENT_DEMO_REVIEWS:
        yield _sse({"event": "busy",
                    "message": "Too many live reviews in progress. Try again shortly."})
        return
    _active_demo_reviews += 1

    queue: asyncio.Queue = asyncio.Queue()

    def emit(stage: str, **data) -> None:
        queue.put_nowait({"event": stage, **data})

    async def runner() -> None:
        try:
            await worker(emit)
        except asyncio.CancelledError:
            log.info("demo_stream_cancelled")
            raise
        except BaseException as exc:  # surface a clean error to the page
            # Unwrap the wrappers that hide the real cause: ExceptionGroup/TaskGroup
            # and tenacity's RetryError ("RetryError[<Future …>]").
            import traceback as _tb
            real = _unwrap_exc(exc)
            log.error(
                "demo_stream_failed",
                error=str(real)[:300],
                error_type=type(real).__name__,
                traceback=_tb.format_exc()[-1500:],
            )
            emit("error", message=_friendly_error(real))
        finally:
            queue.put_nowait(None)  # sentinel: stream complete

    task = asyncio.create_task(runner())
    try:
        yield _sse({"event": "connected", **connected})
        while True:
            # Gemini can "think" for 60-90s between events, leaving the SSE
            # connection idle; Cloud Run / proxies / the browser drop an idle
            # connection, so emit a keepalive comment when no real event is queued.
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


def _make_client_for(platform: str, project_id: str, settings: Settings):
    from quorum.github_client import make_github_client
    from quorum.gitlab_client import make_client as _make_client
    if platform == "github":
        return make_github_client(settings)
    return _make_client(settings, project_id=project_id)


def _dry_run_worker(parsed: dict, settings: Settings):
    """Step 1 — analyse only (no posting/fix), store the result in a session, and
    emit a `session` event with can_write / has_findings / has_critical."""
    platform = parsed["platform"]
    project_id = parsed["project_id"]
    iid = parsed["iid"]

    async def worker(emit) -> None:
        from quorum.agent import QuorumAgent
        agent = QuorumAgent(settings)
        agent._emit = emit
        client = _make_client_for(platform, project_id, settings)
        async with client.connect():
            result = await agent.review(
                project_id=project_id, mr_iid=iid, client=client,
                # Analysis only. force bypasses the "already reviewed" guard so the
                # demo always produces a real result.
                post_comment=False, force=True,
            )
            mr_meta = await client.get_mr_metadata(project_id, iid)
            can_write = await agent._can_write(client, project_id)

        sid = _store_session({
            "result": result,
            "mr_meta": mr_meta,
            "source_branch": mr_meta.get("source_branch", ""),
            "target_branch": mr_meta.get("target_branch", "main"),
            "project_id": project_id,
            "mr_iid": iid,
            "platform": platform,
            "can_write": can_write,
        })
        emit(
            "session",
            session_id=sid,
            can_write=can_write,
            has_findings=len(result.findings) > 0,
            has_critical=result.critical_count > 0,
        )
        emit("done", phase="analyze", delivery="skipped",
             blocked=result.blocked, total=len(result.findings))

    return worker


def _post_worker(session: dict, settings: Settings):
    """Step 2 — post the already-analysed findings (no re-analysis)."""
    async def worker(emit) -> None:
        from quorum.agent import QuorumAgent
        agent = QuorumAgent(settings)
        agent._emit = emit
        platform, project_id, iid = session["platform"], session["project_id"], session["mr_iid"]
        client = _make_client_for(platform, project_id, settings)
        async with client.connect():
            result = await agent.post_findings(
                session["result"], session["mr_meta"], client, project_id, iid
            )
        emit("done", phase="post", delivery=result.delivery,
             blocked=result.blocked, total=len(result.findings))

    return worker


def _fix_worker(session: dict, settings: Settings):
    """Step 3 — open draft fix MRs for the already-analysed findings."""
    async def worker(emit) -> None:
        from quorum.agent import QuorumAgent
        run_settings = settings.model_copy(update={"create_fix_mrs": True})
        agent = QuorumAgent(run_settings)
        agent._emit = emit
        platform, project_id = session["platform"], session["project_id"]
        client = _make_client_for(platform, project_id, run_settings)
        async with client.connect():
            result = await agent.create_fixes(
                session["result"], session["source_branch"],
                session["target_branch"], client, project_id,
            )
        session["result"] = result  # persist fix-MR urls for any later step
        fix_count = sum(1 for f in result.findings if f.fix_mr_iid)
        emit("done", phase="fix", fix_count=fix_count,
             blocked=result.blocked, total=len(result.findings))

    return worker


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
