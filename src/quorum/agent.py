"""Quorum agent — orchestrates Gemini + GitLab MCP to review a merge request.

Agent loop:
  1. Fetch MR diff via GitLab MCP.
  2. Run surface detector (fast pre-filter, no API calls).
  3. If no surfaces detected → exit early.
  4. Build the investigation prompt and expose MCP tools as Gemini function declarations.
  5. Run the Gemini multi-turn tool-calling loop:
       Gemini calls → Python executes MCP tool → result sent back → repeat.
  6. Parse findings from the final Gemini response.
  7. Format and post comment to the MR.
  8. Return ReviewResult (blocking status for CI exit code).
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from google import genai
from google.genai import types
from tenacity import retry, stop_after_attempt, wait_exponential

from quorum.config import Settings
from quorum.detector import detect_surfaces
from quorum.formatter import format_comment
from quorum.gitlab_client import GitLabMCPClient
from quorum.models import Finding, ReviewResult, Severity
from quorum.prompts import SYSTEM_PROMPT, build_review_prompt

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Gemini tool declarations (mirrors the GitLab MCP tools we expose)
# ---------------------------------------------------------------------------

_GEMINI_TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="semantic_code_search",
                description=(
                    "Search the GitLab project for code snippets related to a natural-language query. "
                    "Use this to find compensation handlers, lock utilities, retry helpers, "
                    "idempotency checks, and other coordination-related code across the project."
                ),
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "query": types.Schema(
                            type=types.Type.STRING,
                            description="Natural-language search query, e.g. 'compensation handler for OrderCreated'",
                        ),
                    },
                    required=["query"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_merge_request",
                description="Get metadata for the merge request being reviewed (title, description, author).",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={},
                ),
            ),
        ]
    )
]


# ---------------------------------------------------------------------------
# Gemini client factory
# ---------------------------------------------------------------------------


def _make_gemini_client(settings: Settings) -> genai.Client:
    if settings.use_vertex_ai:
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
    return genai.Client(api_key=settings.gemini_api_key)


# ---------------------------------------------------------------------------
# Findings parser
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> dict[str, Any]:
    """Extract and parse the JSON block from Gemini's final response."""
    match = re.search(r"```json\s*([\s\S]+?)\s*```", text)
    if match:
        return json.loads(match.group(1))
    # Fallback: try parsing the whole response
    return json.loads(text)


def _parse_findings(raw: dict[str, Any]) -> list[Finding]:
    findings = []
    for item in raw.get("findings", []):
        try:
            findings.append(
                Finding(
                    rule_id=item.get("rule_id", "UNKNOWN"),
                    rule_name=item.get("rule_name", ""),
                    severity=Severity(item.get("severity", "LOW")),
                    confidence=int(item.get("confidence", 50)),
                    title=item.get("title", ""),
                    explanation=item.get("explanation", ""),
                    diff_snippet=item.get("diff_snippet"),
                    search_evidence=item.get("search_evidence"),
                    reference=item.get("reference"),
                    suggested_fix=item.get("suggested_fix"),
                    file_path=item.get("file_path"),
                    line_number=item.get("line_number"),
                )
            )
        except Exception as exc:
            log.warning("finding_parse_error", item=item, error=str(exc))
    return findings


# ---------------------------------------------------------------------------
# Core agent loop
# ---------------------------------------------------------------------------


class QuorumAgent:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._gemini = _make_gemini_client(settings)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
    )
    async def _generate(
        self,
        contents: list[types.Content],
    ) -> types.GenerateContentResponse:
        return await self._gemini.aio.models.generate_content(
            model=self._settings.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=_GEMINI_TOOLS,
                temperature=0.1,
            ),
        )

    async def _run_tool(
        self,
        name: str,
        args: dict[str, Any],
        mcp: GitLabMCPClient,
        project_id: str,
        mr_iid: int,
    ) -> str:
        log.info("tool_call", name=name, args=args)
        if name == "semantic_code_search":
            return await mcp.semantic_code_search(
                project_id=project_id,
                query=args["query"],
                max_results=self._settings.max_search_results,
            )
        if name == "get_merge_request":
            return await mcp.get_merge_request(project_id, mr_iid)
        return f"[UNKNOWN TOOL] {name}"

    async def _agent_loop(
        self,
        initial_message: str,
        mcp: GitLabMCPClient,
        project_id: str,
        mr_iid: int,
    ) -> str:
        """Run the Gemini tool-calling loop. Returns the final text response."""
        contents: list[types.Content] = [
            types.Content(role="user", parts=[types.Part(text=initial_message)])
        ]

        for round_num in range(self._settings.max_tool_rounds):
            response = await self._generate(contents)
            candidate = response.candidates[0]
            contents.append(candidate.content)

            # Collect any function calls from this turn
            function_calls = [
                p.function_call
                for p in (candidate.content.parts or [])
                if p.function_call is not None
            ]

            if not function_calls:
                # Gemini is done calling tools — extract the text response
                text_parts = [
                    p.text for p in (candidate.content.parts or []) if p.text
                ]
                if text_parts:
                    return "\n".join(text_parts)
                # Empty turn: gemini-2.5-pro emits a silent planning turn before
                # calling tools. Keep the turn in history and continue the loop.
                log.debug("empty_thinking_turn", round=round_num,
                          finish_reason=str(candidate.finish_reason))
                continue

            log.info("tool_round", round=round_num + 1, calls=[fc.name for fc in function_calls])

            # Execute all function calls (may be multiple in one turn)
            tool_parts: list[types.Part] = []
            for fc in function_calls:
                result_text = await self._run_tool(
                    name=fc.name,
                    args=dict(fc.args),
                    mcp=mcp,
                    project_id=project_id,
                    mr_iid=mr_iid,
                )
                tool_parts.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": result_text},
                        )
                    )
                )

            contents.append(types.Content(role="user", parts=tool_parts))

        log.warning("max_tool_rounds_reached", rounds=self._settings.max_tool_rounds)
        # Ask Gemini to summarise with what it has so far
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part(text="Please summarise your findings as JSON now.")],
            )
        )
        response = await self._generate(contents)
        return "\n".join(
            p.text for p in response.candidates[0].content.parts if p.text
        )

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def review(
        self,
        project_id: str,
        mr_iid: int,
        mcp: GitLabMCPClient,
        post_comment: bool = True,
    ) -> ReviewResult:
        log.info("review_started", project_id=project_id, mr_iid=mr_iid)

        # Step 1 — fetch diff
        diff_raw = await mcp.get_merge_request_diffs(project_id, mr_iid)

        # Step 2 — surface detection
        triggered_rules = detect_surfaces(diff_raw)

        if not triggered_rules:
            log.info("review_skipped_no_surfaces")
            result = ReviewResult(
                mr_iid=mr_iid,
                project_id=project_id,
                surfaces_detected=0,
                rules_checked=0,
                blocked=False,
            )
            if post_comment:
                comment = (
                    "## Quorum · Distributed Coordination Review\n\n"
                    "✅ No coordination surfaces detected in this diff. "
                    "No distributed locks, sagas, retries, idempotency, or messaging patterns found."
                )
                await mcp.create_workitem_note(project_id, mr_iid, comment)
            return result

        # Step 3 — build prompt and run agent
        prompt = build_review_prompt(
            diff=diff_raw,
            triggered_rules=triggered_rules,
            project_id=project_id,
            mr_iid=mr_iid,
        )

        final_text = await self._agent_loop(prompt, mcp, project_id, mr_iid)

        # Step 4 — parse findings
        findings: list[Finding] = []
        try:
            raw = _extract_json(final_text)
            all_findings = _parse_findings(raw)
            # Apply confidence threshold
            findings = [
                f for f in all_findings
                if f.confidence >= self._settings.min_confidence or f.severity == Severity.PASS
            ]
        except Exception as exc:
            log.error("findings_parse_failed", error=str(exc), response_preview=final_text[:500])

        has_critical = any(f.severity == Severity.CRITICAL for f in findings)
        blocked = self._settings.block_on_critical and has_critical

        result = ReviewResult(
            mr_iid=mr_iid,
            project_id=project_id,
            findings=findings,
            surfaces_detected=len(triggered_rules),
            rules_checked=len(triggered_rules),
            blocked=blocked,
        )

        log.info(
            "review_complete",
            critical=result.critical_count,
            high=result.high_count,
            medium=result.medium_count,
            blocked=blocked,
        )

        # Step 5 — post comment
        if post_comment:
            comment_body = format_comment(result)
            await mcp.create_workitem_note(project_id, mr_iid, comment_body)

        return result
