"""Vertex AI Agent Engine wrapper for Quorum's DeepReasoningAgent.

Implements the Queryable interface required by vertexai.preview.reasoning_engines.
Deployed via deploy/agent_engine.py.

In Agent Engine mode:
- No glab binary available → forces REST mode (QUORUM_MCP_MODE=rest)
- No subprocess MCP servers → pure HTTPS calls to GitLab/GitHub APIs
- Synchronous query() wraps the async review() via asyncio.run()
"""

from __future__ import annotations

import asyncio
import os


class QuorumReasoningEngine:
    """Vertex AI Agent Engine app wrapping Quorum's three-stage pipeline.

    Judges can interact with this directly in the Google Cloud Console
    Agent Engine playground — no webhook or CLI setup required.
    """

    def set_up(self) -> None:
        """Called once after the engine is deployed. Validate config."""
        from quorum.config import Settings
        # Validate that required secrets are reachable
        s = Settings()
        assert s.gemini_api_key or s.use_vertex_ai, (
            "QUORUM_GEMINI_API_KEY or QUORUM_USE_VERTEX_AI must be set"
        )

    def query(
        self,
        project_id: str,
        mr_iid: int,
        platform: str = "gitlab",
        dry_run: bool = False,
    ) -> dict:
        """Run a Quorum review on the specified MR/PR.

        Args:
            project_id: Namespace path — e.g. ``quorum-hackathon/quorum-demo``
                        for GitLab, or ``aio-libs/aiokafka`` for GitHub.
            mr_iid:     MR/PR number (integer).
            platform:   ``"gitlab"`` (default) or ``"github"``.
            dry_run:    If ``True``, skip posting comments to the MR/PR.
                        Useful for exploring findings without side effects.

        Returns:
            dict with keys:
                - ``summary``: one-paragraph markdown summary
                - ``blocked``: bool — True if CRITICAL findings require action
                - ``critical_count``, ``high_count``: severity tallies
                - ``findings``: list of finding dicts (rule_id, severity,
                  confidence, title, explanation, file_path, line_number,
                  suggested_fix, reference)
        """
        return asyncio.run(self._async_query(project_id, mr_iid, platform, dry_run))

    async def _async_query(
        self,
        project_id: str,
        mr_iid: int,
        platform: str,
        dry_run: bool,
    ) -> dict:
        from quorum.agent import DeepReasoningAgent
        from quorum.config import Settings
        from quorum.gitlab_client import make_client
        from quorum.github_client import make_github_client

        # Force REST mode — no glab binary in the Agent Engine container
        settings = Settings(
            platform=platform,
            mcp_mode="rest",
        )
        agent = DeepReasoningAgent(settings)

        if platform == "github":
            client = make_github_client(settings)
        else:
            client = make_client(settings, rest_only=True, project_id=project_id)

        async with client.connect():
            result = await agent.review(
                project_id=project_id,
                mr_iid=mr_iid,
                client=client,
                post_comment=not dry_run,
            )

        actionable = [f for f in result.findings if f.severity.value != "PASS"]
        sev_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        actionable.sort(key=lambda f: sev_order.get(f.severity.value, 9))

        if not actionable:
            summary = (
                f"✅ No coordination issues found in {platform.upper()} "
                f"`{project_id}` #{mr_iid}. All {result.rules_checked} rules passed."
            )
        else:
            lines = [
                f"**Quorum found {len(actionable)} coordination issue(s)** in "
                f"`{project_id}` #{mr_iid}:\n"
            ]
            for f in actionable:
                loc = f"`{f.file_path}:{f.line_number}`" if f.file_path else ""
                lines.append(
                    f"- {f.severity.emoji} **{f.rule_id}** {f.title} "
                    f"({f.confidence}%){' — ' + loc if loc else ''}"
                )
            if result.blocked:
                lines.append("\n⛔ **Pipeline should be blocked** until CRITICAL findings are resolved.")
            summary = "\n".join(lines)

        return {
            "summary": summary,
            "blocked": result.blocked,
            "critical_count": result.critical_count,
            "high_count": result.high_count,
            "medium_count": result.medium_count,
            "surfaces_detected": result.surfaces_detected,
            "rules_checked": result.rules_checked,
            "findings": [
                {
                    "rule_id": f.rule_id,
                    "rule_name": f.rule_name,
                    "severity": f.severity.value,
                    "confidence": f.confidence,
                    "title": f.title,
                    "explanation": f.explanation,
                    "file_path": f.file_path,
                    "line_number": f.line_number,
                    "suggested_fix": f.suggested_fix,
                    "reference": f.reference,
                    "fix_mr_url": f.fix_mr_url,
                }
                for f in result.findings
                if f.severity.value != "PASS"
            ],
        }
