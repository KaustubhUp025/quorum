"""Quorum agent — orchestrates Gemini + GitLab MCP to review a merge request.

Agent loop:
  1. Fetch MR diff and metadata via GitLab client.
  2. Run surface detector (fast pre-filter, no API calls).
  3. If no surfaces detected → exit early.
  4. Build the investigation prompt and expose MCP tools as Gemini function declarations.
  5. Run the LLM tool-calling loop (Gemini native or LiteLLM for alternate backends):
       LLM calls → Python executes MCP tool → result sent back → repeat.
     Gemini 2.5 Pro uses dynamic thinking (thinking_budget=-1) and Google Search grounding.
     LiteLLM backends (ollama, openai, anthropic) use OpenAI-compatible tool calling.
  6. Parse findings from the final LLM response.
  7. (Optional) Generate fix code and open a draft fix MR for each CRITICAL finding.
  8. (Optional) Correlate findings with a failing CI pipeline.
  9. Format and post comment to the MR.
 10. Return ReviewResult (blocking status for CI exit code).
"""

from __future__ import annotations

import json
import re
import time
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
    # LiteLLM agent loop (OpenAI-compatible — any non-Gemini backend)
    # ------------------------------------------------------------------

    @staticmethod
    def _openai_tools() -> list[dict]:
        """Return the investigation tools in OpenAI function-calling JSON format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "semantic_code_search",
                    "description": (
                        "Search the repository for code snippets related to a natural-language query. "
                        "Use this to find compensation handlers, lock utilities, retry helpers, "
                        "idempotency checks, and other coordination-related code across the project."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": (
                                    "Natural-language search query. "
                                    "E.g. 'compensation handler for OrderCreated', "
                                    "'fencing token uuid lock', 'retry backoff jitter'"
                                ),
                            }
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_merge_request",
                    "description": (
                        "Get metadata for the MR/PR being reviewed "
                        "(title, description, author, source/target branch)."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_file_contents",
                    "description": (
                        "Fetch the full current content of a file in the repository. "
                        "Use when the diff snippet alone is insufficient."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Repository-relative path, e.g. 'src/saga.py'",
                            },
                            "ref": {
                                "type": "string",
                                "description": "Git ref (branch or SHA). Defaults to HEAD.",
                            },
                        },
                        "required": ["file_path"],
                    },
                },
            },
        ]

    async def _agent_loop_litellm(
        self,
        initial_message: str,
        client: GitLabClientT,
        project_id: str,
        mr_iid: int,
    ) -> str:
        """
        Run the multi-turn tool-calling loop using LiteLLM (OpenAI-compatible API).

        Used when QUORUM_LLM_BACKEND is set to a non-Gemini backend such as
        'ollama/mistral', 'openai/gpt-4o', or 'anthropic/claude-3-5-sonnet-20241022'.
        The backend must support OpenAI-compatible function/tool calling.
        """
        import litellm  # soft import

        litellm.drop_params = True  # ignore unknown params silently

        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": initial_message},
        ]
        tools = self._openai_tools()
        model = self._settings.llm_backend

        for round_num in range(self._settings.max_tool_rounds):
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.1,
            )
            msg = response.choices[0].message
            tool_calls = msg.tool_calls or []

            # Append the assistant turn to history
            messages.append(msg.model_dump(exclude_unset=True))

            if not tool_calls:
                # Final text response
                return msg.content or ""

            log.info(
                "tool_round",
                round=round_num + 1,
                calls=[tc.function.name for tc in tool_calls],
            )

            # Execute each tool call and append results
            for tc in tool_calls:
                fn_name = tc.function.name
                try:
                    fn_args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    fn_args = {}

                log.info("tool_call", name=fn_name, args=fn_args)
                result_text = await self._run_tool(
                    name=fn_name,
                    args=fn_args,
                    client=client,
                    project_id=project_id,
                    mr_iid=mr_iid,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })

        log.warning("max_tool_rounds_reached", rounds=self._settings.max_tool_rounds)
        messages.append({"role": "user", "content": "Please summarise your findings as JSON now."})
        response = await litellm.acompletion(
            model=model, messages=messages, temperature=0.1
        )
        return response.choices[0].message.content or ""

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
    # Fix proposal MR generation
    # ------------------------------------------------------------------

    async def _generate_fix(self, finding: Finding, file_content: str) -> str | None:
        """Ask Gemini to produce the corrected version of a file for a CRITICAL finding."""
        prompt = (
            f"A coordination bug was found by Quorum ({finding.rule_id} — {finding.title}).\n\n"
            f"Bug explanation: {finding.explanation}\n\n"
            f"Suggested fix: {finding.suggested_fix or 'Apply the correct pattern for this rule.'}\n\n"
            f"Current file:\n{file_content}\n\n"
            "Output ONLY the complete corrected file content with the fix applied. "
            "Do NOT include any explanation, markdown code fences, or commentary — "
            "just the raw corrected source file, ready to commit."
        )
        try:
            response = await self._gemini.aio.models.generate_content(
                model=self._settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    # No thinking needed for pure code generation — saves latency + cost
                    thinking_config=types.ThinkingConfig(thinking_budget=1024),
                ),
            )
            text = "\n".join(
                p.text for p in response.candidates[0].content.parts
                if p.text and not getattr(p, "thought", False)
            )
            # Strip markdown wrappers if Gemini added them anyway
            text = re.sub(r"^```[^\n]*\n?", "", text.strip())
            text = re.sub(r"\n?```\s*$", "", text.strip())
            return text.strip() or None
        except Exception as exc:
            log.warning("fix_generation_failed", rule=finding.rule_id, error=str(exc))
            return None

    def _build_fix_mr_description(self, finding: Finding, fixed_content: str) -> str:
        """Build the description for the fix draft MR."""
        snippet = fixed_content[:2000] + ("…" if len(fixed_content) > 2000 else "")
        desc = (
            f"## 🤖 Auto-generated fix by [Quorum](https://github.com/KaustubhUp025/quorum)\n\n"
            f"**Rule:** {finding.rule_id} — {finding.rule_name}\n"
            f"**Severity:** {finding.severity.value}\n"
            f"**File:** `{finding.file_path}`\n\n"
            f"### What was wrong\n{finding.explanation}\n\n"
            f"### Fix applied\n{finding.suggested_fix or 'See the diff above.'}\n\n"
            f"### Corrected file preview\n```\n{snippet}\n```\n\n"
            f"---\n*This MR was opened automatically by Quorum after detecting a CRITICAL "
            f"coordination bug. Review the diff carefully before merging.*"
        )
        if finding.reference:
            desc += f"\n\n**Reference:** {finding.reference}"
        return desc

    async def _create_fix_mrs(
        self,
        findings: list[Finding],
        source_branch: str,
        target_branch: str,
        client: GitLabClientT,
        project_id: str,
    ) -> list[Finding]:
        """Create draft fix MRs for CRITICAL findings (up to fix_mr_max_count)."""
        created = 0
        for finding in findings:
            if created >= self._settings.fix_mr_max_count:
                break
            if finding.severity != Severity.CRITICAL or not finding.file_path:
                continue

            log.info("fix_mr_starting", rule=finding.rule_id, file=finding.file_path)
            try:
                # 1 — get the current file from the source branch
                file_content = await client.get_file_contents(
                    project_id, finding.file_path, ref=source_branch
                )
                if file_content.startswith("["):
                    log.warning("fix_mr_file_not_found", file=finding.file_path)
                    continue

                # 2 — ask Gemini to generate the corrected file
                fixed_content = await self._generate_fix(finding, file_content)
                if not fixed_content:
                    continue

                # 3 — create a branch from the source branch
                branch_name = (
                    f"quorum-fix/{finding.rule_id.lower().replace('_', '-')}"
                    f"-{int(time.time())}"
                )
                await client.create_branch(project_id, branch_name, ref=source_branch)

                # 4 — commit the corrected file
                commit_msg = f"[Quorum] Fix {finding.rule_id}: {finding.title}"
                await client.commit_file(
                    project_id, branch_name, finding.file_path, fixed_content, commit_msg
                )

                # 5 — open a DRAFT MR (prefix title with "Draft:" for GitLab draft status)
                mr_title = f"Draft: [Quorum] Fix {finding.rule_id}: {finding.title}"
                mr_desc = self._build_fix_mr_description(finding, fixed_content)
                mr_result = await client.create_merge_request(
                    project_id, branch_name, target_branch, mr_title, mr_desc
                )
                # Result is JSON (REST) or glab text URL — handle both
                try:
                    mr_data = json.loads(mr_result)
                    finding.fix_mr_url = mr_data.get("web_url")
                    finding.fix_mr_iid = mr_data.get("iid")
                except json.JSONDecodeError:
                    url_match = re.search(
                        r'https?://\S+/-/merge_requests/(\d+)', mr_result
                    )
                    if url_match:
                        finding.fix_mr_url = url_match.group(0).rstrip(".")
                        finding.fix_mr_iid = int(url_match.group(1))

                log.info(
                    "fix_mr_created",
                    rule=finding.rule_id,
                    mr_iid=finding.fix_mr_iid,
                    url=finding.fix_mr_url,
                )
                created += 1

            except Exception as exc:
                log.warning("fix_mr_failed", rule=finding.rule_id, error=str(exc))

        return findings

    # ------------------------------------------------------------------
    # CI failure correlation
    # ------------------------------------------------------------------

    async def _correlate_with_ci_failure(
        self,
        project_id: str,
        mr_iid: int,
        findings: list[Finding],
        client: GitLabClientT,
    ) -> str | None:
        """Check for a failing CI pipeline and ask Gemini if it relates to the findings."""
        try:
            pipelines = await client.get_mr_pipelines(project_id, mr_iid)
            if not pipelines:
                return None

            # Most recent pipeline first
            latest = pipelines[0]
            status = latest.get("status", "")
            if status not in ("failed", "running"):
                return None

            pipeline_id = latest.get("id")
            log.info("ci_pipeline_found", pipeline_id=pipeline_id, status=status)

            # Fetch failed job logs
            jobs = await client.get_pipeline_jobs(project_id, pipeline_id)
            if not jobs:
                return None

            first_job = jobs[0]
            job_id = first_job.get("id")
            job_name = first_job.get("name", "unknown")
            log.info("ci_failed_job", job_id=job_id, job_name=job_name)

            log_text = await client.get_pipeline_job_output(project_id, job_id)
            # Trim to last 3000 chars to avoid flooding the context
            if len(log_text) > 3000:
                log_text = f"[...truncated...]\n{log_text[-3000:]}"

            # Build correlation prompt
            findings_summary = "\n".join(
                f"- {f.rule_id} ({f.severity.value}): {f.title}"
                for f in findings
                if f.severity != Severity.PASS
            )
            prompt = (
                "A merge request with the following coordination bugs was reviewed by Quorum:\n\n"
                f"{findings_summary}\n\n"
                f"The CI pipeline (job: {job_name}) is {status} with this log output:\n"
                f"```\n{log_text}\n```\n\n"
                "In 2-3 sentences: does the CI failure appear to be caused by or related to any "
                "of these coordination bugs? If yes, name the specific rule(s). "
                "If no clear connection, say so briefly."
            )

            response = await self._gemini.aio.models.generate_content(
                model=self._settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    thinking_config=types.ThinkingConfig(thinking_budget=1024),
                ),
            )
            correlation = "\n".join(
                p.text for p in response.candidates[0].content.parts
                if p.text and not getattr(p, "thought", False)
            ).strip()

            log.info("ci_correlation_done", job=job_name, status=status)
            return f"**Pipeline job `{job_name}` is {status}.** {correlation}"

        except Exception as exc:
            log.warning("ci_correlation_failed", error=str(exc))
            return None

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

        # Step 1 — fetch diff and MR metadata in parallel
        diff_raw = await client.get_merge_request_diffs(project_id, mr_iid)
        mr_meta = await client.get_mr_metadata(project_id, mr_iid)
        source_branch = mr_meta.get("source_branch", "")
        target_branch = mr_meta.get("target_branch", "main")

        # Step 2 — surface detection + disabled-rule filtering
        triggered_rules = detect_surfaces(diff_raw)
        disabled = {r.upper() for r in (self._settings.disabled_rules or [])}
        if disabled:
            before = len(triggered_rules)
            triggered_rules = [r for r in triggered_rules if r.id not in disabled]
            if before != len(triggered_rules):
                log.info("rules_disabled", skipped=before - len(triggered_rules), ids=sorted(disabled))

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

        # Select backend: native google-genai for Gemini, LiteLLM for everything else
        use_gemini = self._settings.llm_backend.startswith("gemini/")
        if use_gemini:
            final_text = await self._agent_loop(prompt, client, project_id, mr_iid)
        else:
            log.info("llm_backend_litellm", model=self._settings.llm_backend)
            final_text = await self._agent_loop_litellm(prompt, client, project_id, mr_iid)

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

        # Step 5 — enrich confirmed findings with real citations via Google Search
        if findings:
            findings = await self._enrich_with_citations(findings)

        # Step 6 — create draft fix MRs for CRITICAL findings (opt-in)
        if findings and self._settings.create_fix_mrs and source_branch:
            findings = await self._create_fix_mrs(
                findings, source_branch, target_branch, client, project_id
            )

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

        # Step 7 — correlate with CI failures (opt-in)
        if self._settings.correlate_ci and findings:
            result.ci_correlation = await self._correlate_with_ci_failure(
                project_id, mr_iid, findings, client
            )

        log.info(
            "review_complete",
            critical=result.critical_count,
            high=result.high_count,
            medium=result.medium_count,
            blocked=blocked,
        )

        # Step 8 — post comment
        if post_comment:
            comment_body = format_comment(result)
            await client.create_workitem_note(project_id, mr_iid, comment_body)

        return result
