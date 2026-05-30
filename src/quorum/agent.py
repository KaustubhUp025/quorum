"""Quorum agent — orchestrates Gemini + GitLab MCP to review a merge request.

Agent loop:
  1. Fetch MR diff via GitLab client.
  2. Run surface detector (fast pre-filter, no API calls).
  3. If no surfaces detected → exit early.
  4. Build the investigation prompt and expose MCP tools as Gemini function declarations.
  5. Run the Gemini multi-turn tool-calling loop:
       Gemini calls → Python executes MCP tool → result sent back → repeat.
     Gemini 2.5 Pro uses dynamic thinking (thinking_budget=-1) and has access to
     Google Search for grounding citations against real CVEs / incident reports.
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
from quorum.gitlab_client import GitLabYodaMCPClient, GitLabRESTClient
from quorum.models import Finding, ReviewResult, Severity
from quorum.prompts import SYSTEM_PROMPT, build_review_prompt

log = structlog.get_logger(__name__)

# Type alias for any GitLab client implementation
GitLabClientT = GitLabYodaMCPClient | GitLabRESTClient

# ---------------------------------------------------------------------------
# Gemini tool declarations
# ---------------------------------------------------------------------------

_MCP_FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name="semantic_code_search",
        description=(
            "Search the GitLab project for code snippets related to a natural-language query. "
            "Use this to find compensation handlers, lock utilities, retry helpers, "
            "idempotency checks, and other coordination-related code across the entire project. "
            "Returns file paths and matching code snippets."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "query": types.Schema(
                    type=types.Type.STRING,
                    description=(
                        "Natural-language search query. "
                        "E.g. 'compensation handler for OrderCreated', "
                        "'cancel shipment saga step', 'fencing token uuid lock'"
                    ),
                ),
            },
            required=["query"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_merge_request",
        description=(
            "Get metadata for the merge request being reviewed "
            "(title, description, author, source/target branch). "
            "Call this first to understand the purpose of the change."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={},
        ),
    ),
    types.FunctionDeclaration(
        name="get_file_contents",
        description=(
            "Fetch the full current content of a file in the repository. "
            "Use when the diff snippet alone is insufficient — e.g. to verify whether "
            "a class has a compensate() method defined elsewhere in the same file, "
            "or to inspect an import that appears in the diff."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "file_path": types.Schema(
                    type=types.Type.STRING,
                    description="Repository-relative file path, e.g. 'src/orderservice/saga.py'",
                ),
                "ref": types.Schema(
                    type=types.Type.STRING,
                    description="Git ref to read from (branch name or commit SHA). Defaults to HEAD.",
                ),
            },
            required=["file_path"],
        ),
    ),
]

# Tools for the multi-turn investigation loop (function declarations only).
# Google Search cannot be combined with function declarations in the same request;
# it is used separately in _enrich_with_citations() after findings are parsed.
_GEMINI_TOOLS = [
    types.Tool(function_declarations=_MCP_FUNCTION_DECLARATIONS),
]

# Config for the grounding/citation enrichment call (Google Search only, no function decls).
_GROUNDING_TOOLS = [
    types.Tool(google_search=types.GoogleSearch()),
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
                # Dynamic thinking: Gemini 2.5 Pro decides reasoning depth per turn.
                # Harder multi-file problems (saga, fencing token) get more thinking;
                # simpler surface checks get less. -1 = model-controlled.
                thinking_config=types.ThinkingConfig(thinking_budget=-1),
            ),
        )

    async def _run_tool(
        self,
        name: str,
        args: dict[str, Any],
        client: GitLabClientT,
        project_id: str,
        mr_iid: int,
    ) -> str:
        log.info("tool_call", name=name, args=args)
        if name == "semantic_code_search":
            return await client.semantic_code_search(
                project_id=project_id,
                query=args["query"],
                max_results=self._settings.max_search_results,
            )
        if name == "get_merge_request":
            return await client.get_merge_request(project_id, mr_iid)
        if name == "get_file_contents":
            return await client.get_file_contents(
                project_id=project_id,
                file_path=args["file_path"],
                ref=args.get("ref", "HEAD"),
            )
        return f"[UNKNOWN TOOL] {name}"

    async def _agent_loop(
        self,
        initial_message: str,
        client: GitLabClientT,
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

            # Collect function calls from this turn (ignore thought/search parts)
            function_calls = [
                p.function_call
                for p in (candidate.content.parts or [])
                if p.function_call is not None
            ]

            if not function_calls:
                text_parts = [
                    p.text for p in (candidate.content.parts or [])
                    if p.text and not getattr(p, "thought", False)
                ]
                if text_parts:
                    return "\n".join(text_parts)
                # Silent planning / thinking turn — keep history and continue
                log.debug(
                    "empty_thinking_turn",
                    round=round_num,
                    finish_reason=str(candidate.finish_reason),
                )
                continue

            log.info(
                "tool_round",
                round=round_num + 1,
                calls=[fc.name for fc in function_calls],
            )

            # Execute all function calls in this turn
            tool_parts: list[types.Part] = []
            for fc in function_calls:
                result_text = await self._run_tool(
                    name=fc.name,
                    args=dict(fc.args),
                    client=client,
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
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part(text="Please summarise your findings as JSON now.")],
            )
        )
        response = await self._generate(contents)
        return "\n".join(
            p.text for p in response.candidates[0].content.parts
            if p.text and not getattr(p, "thought", False)
        )

    # ------------------------------------------------------------------
    # Citation enrichment (separate Google Search pass)
    # ------------------------------------------------------------------

    async def _enrich_with_citations(self, findings: list[Finding]) -> list[Finding]:
        """
        Make one Gemini call with Google Search grounding to add real citations
        (CVE numbers, RFC refs, incident report URLs) to confirmed findings.

        Google Search cannot be combined with function calling in the same request,
        so this runs as a separate pass after the investigation loop completes.
        Returns the same list with reference fields populated where possible.
        """
        if not findings:
            return findings

        # Only enrich FINDING-level items (skip PASS and LOW confidence)
        to_enrich = [
            f for f in findings
            if f.severity not in (Severity.PASS,) and not f.reference
        ]
        if not to_enrich:
            return findings

        prompt_parts = ["For each finding below, find ONE real published reference "
                        "(CVE, RFC, AWS/Google incident report, or academic paper). "
                        "Reply with a JSON array in the same order, each item having "
                        "'rule_id' and 'reference' (short citation + URL).\n\n"]
        for f in to_enrich:
            prompt_parts.append(f"- {f.rule_id}: {f.title} — {f.explanation[:120]}")

        try:
            response = await self._gemini.aio.models.generate_content(
                model=self._settings.gemini_model,
                contents="\n".join(prompt_parts),
                config=types.GenerateContentConfig(
                    tools=_GROUNDING_TOOLS,
                    temperature=0.1,
                ),
            )
            text = "\n".join(
                p.text for p in response.candidates[0].content.parts if p.text
            )
            # Parse JSON array from response
            match = re.search(r"\[[\s\S]+\]", text)
            if match:
                citations = json.loads(match.group(0))
                citation_map = {c["rule_id"]: c.get("reference", "") for c in citations}
                for f in to_enrich:
                    if f.rule_id in citation_map and citation_map[f.rule_id]:
                        f.reference = citation_map[f.rule_id]
        except Exception as exc:
            log.warning("citation_enrichment_failed", error=str(exc))

        return findings

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def review(
        self,
        project_id: str,
        mr_iid: int,
        client: GitLabClientT,
        post_comment: bool = True,
    ) -> ReviewResult:
        log.info("review_started", project_id=project_id, mr_iid=mr_iid)

        # Step 1 — fetch diff
        diff_raw = await client.get_merge_request_diffs(project_id, mr_iid)

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
                await client.create_workitem_note(project_id, mr_iid, comment)
            return result

        log.info("surfaces_detected", count=len(triggered_rules), rules=[r.id for r in triggered_rules])

        # Step 3 — build prompt and run agent loop
        prompt = build_review_prompt(
            diff=diff_raw,
            triggered_rules=triggered_rules,
            project_id=project_id,
            mr_iid=mr_iid,
        )
        final_text = await self._agent_loop(prompt, client, project_id, mr_iid)

        # Step 4 — parse findings
        findings: list[Finding] = []
        try:
            raw = _extract_json(final_text)
            all_findings = _parse_findings(raw)
            findings = [
                f for f in all_findings
                if f.confidence >= self._settings.min_confidence
                or f.severity == Severity.PASS
            ]
        except Exception as exc:
            log.error(
                "findings_parse_failed",
                error=str(exc),
                response_preview=final_text[:500],
            )

        # Enrich confirmed findings with real citations via Google Search
        if findings:
            findings = await self._enrich_with_citations(findings)

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
            await client.create_workitem_note(project_id, mr_iid, comment_body)

        return result
