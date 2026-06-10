"""Tests for the Quorum Live web UI routes and the MR/PR URL parser."""

from __future__ import annotations

from fastapi.testclient import TestClient

from quorum.app import _friendly_error, _unwrap_exc, create_app, parse_mr_url
from quorum.config import Settings


def _client() -> TestClient:
    settings = Settings(
        gemini_api_key="test",
        gitlab_token="test",
        webhook_secret="mysecret",
    )
    return TestClient(create_app(settings), raise_server_exceptions=False)


class TestParseMrUrl:
    def test_gitlab_simple(self):
        out = parse_mr_url("https://gitlab.com/group/project/-/merge_requests/7")
        assert out == {"platform": "gitlab", "project_id": "group/project", "iid": 7}

    def test_gitlab_nested_namespace(self):
        out = parse_mr_url("https://gitlab.com/group/sub/project/-/merge_requests/42")
        assert out == {"platform": "gitlab", "project_id": "group/sub/project", "iid": 42}

    def test_github_pr(self):
        out = parse_mr_url("https://github.com/owner/repo/pull/218")
        assert out == {"platform": "github", "project_id": "owner/repo", "iid": 218}

    def test_trailing_path_ignored(self):
        out = parse_mr_url("https://gitlab.com/a/b/-/merge_requests/3/diffs")
        assert out == {"platform": "gitlab", "project_id": "a/b", "iid": 3}

    def test_invalid(self):
        assert parse_mr_url("https://example.com/foo") is None
        assert parse_mr_url("") is None
        assert parse_mr_url("not a url") is None


class TestErrorUnwrapping:
    def test_unwrap_retry_error(self):
        from tenacity import Future, RetryError
        cause = ValueError("429 RESOURCE_EXHAUSTED: quota")
        fut = Future(attempt_number=3)
        fut.set_exception(cause)
        real = _unwrap_exc(RetryError(fut))
        assert real is cause

    def test_unwrap_exception_group(self):
        cause = RuntimeError("boom")
        try:
            eg = ExceptionGroup("grp", [cause])  # type: ignore[name-defined]
        except NameError:  # py < 3.11 fallback
            return
        assert _unwrap_exc(eg) is cause

    def test_unwrap_plain(self):
        e = KeyError("x")
        assert _unwrap_exc(e) is e

    def test_friendly_quota(self):
        msg = _friendly_error(ValueError("429 RESOURCE_EXHAUSTED"))
        assert "rate-limited" in msg

    def test_friendly_generic(self):
        msg = _friendly_error(ValueError("something odd"))
        assert "something odd" in msg


class TestDemoRoutes:
    def test_root_redirects_to_demo(self):
        resp = _client().get("/", follow_redirects=False)
        assert resp.status_code in (307, 308)
        assert resp.headers["location"] == "/demo"

    def test_demo_page_served(self):
        resp = _client().get("/demo")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "Quorum" in resp.text

    def test_static_assets(self):
        c = _client()
        for path in ("/logo.svg", "/favicon.svg"):
            resp = c.get(path)
            assert resp.status_code == 200
            assert "svg" in resp.headers["content-type"]

    def test_parse_url_endpoint_ok(self):
        resp = _client().get("/demo/parse-url", params={"url": "https://github.com/o/r/pull/1"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["platform"] == "github"
        assert body["project_id"] == "o/r"
        assert body["iid"] == 1

    def test_parse_url_endpoint_rejects_bad_url(self):
        resp = _client().get("/demo/parse-url", params={"url": "https://example.com/x"})
        assert resp.status_code == 400
        assert resp.json()["ok"] is False

    def test_stream_rejects_bad_url(self):
        resp = _client().get("/demo/stream", params={"url": "https://example.com/x"})
        assert resp.status_code == 400

    def test_stream_rejects_bad_mode(self):
        resp = _client().get(
            "/demo/stream",
            params={"url": "https://gitlab.com/a/b/-/merge_requests/1", "mode": "delete"},
        )
        assert resp.status_code == 422  # pattern validation fails
