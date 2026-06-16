# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅ Current |

Older versions are not patched. Please upgrade to the latest release.

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report security issues by emailing **kaustubhme0@gmail.com** with the subject line:

```
[QUORUM SECURITY] <short description>
```

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fix (optional but appreciated)

You will receive an acknowledgement within **48 hours** and a status update within **7 days**.

## Scope

The following are in scope:

- **Prompt injection** via GitLab MR content (diff text, MR description, commit messages)
- **Credential leakage** — `QUORUM_GITLAB_TOKEN` or `QUORUM_GEMINI_API_KEY` exposed in logs or comment output
- **Webhook authentication bypass** — forged GitLab webhook events reaching the Cloud Run server
- **Arbitrary code execution** via the rule pattern evaluation path

The following are **out of scope**:

- Vulnerabilities in GitLab itself or the GitLab MCP server
- Vulnerabilities in the Gemini API
- Rate-limit abuse (contact Google or GitLab directly)
- Issues in dependencies — please report those upstream

## Known security considerations

### Prompt injection

Quorum sends MR diffs, file contents, CI logs, and `semantic_code_search` results to Gemini. A malicious MR author can include text designed to manipulate the model's output.

**Mitigations in place:**
- All externally-sourced content (diff, file contents, CI logs, MR metadata, search results) is isolated inside `<untrusted_diff>`, `<untrusted_tool_result>`, or `<untrusted_ci_log>` XML boundary tags.
- A `_escape_boundary_tags()` function HTML-encodes any of those tag names before embedding external content — including opening tags, closing tags, whitespace variants (`</ untrusted_diff >`), and case variants. This prevents boundary-escape attacks.
- The system prompt includes 5 ABSOLUTE RULES that instruct the model to never follow instructions from untrusted content, never reveal secrets, never repeat the system prompt, and never change output format based on untrusted input.
- Every standalone Gemini call (citations, fix generation, CI correlation) passes `system_instruction=SYSTEM_PROMPT` so the injection-resistance policy is active on all calls.
- LLM-supplied `file_path` values are validated before any write API call: `..` traversal and absolute paths are rejected.
- LLM-generated prose (`explanation`, `suggested_fix`) has markdown heading markers escaped before rendering, preventing fake `## Quorum` heading injection in MR comments.

### Webhook secret verification

The `quorum serve` FastAPI server accepts GitLab webhooks. **`QUORUM_WEBHOOK_SECRET` is mandatory** — the server raises a `RuntimeError` at startup if it is not set, refusing to serve any requests.

**Configuration:** Set `QUORUM_WEBHOOK_SECRET` to a strong random string. Configure the same value in GitLab's webhook settings. The server validates the `X-Gitlab-Token` header using `hmac.compare_digest` (timing-safe) and returns `403` on mismatch.

### Credential safety

- Secrets are loaded from Google Secret Manager at runtime — never baked into the container image.
- All `error=str(exc)` log fields in the review pipeline are passed through `_scrub_secrets()` before writing, which redacts GitLab PATs, GitHub tokens, and Google API keys.
- Background task error logs do not include stack traces (`exc_info` disabled) to prevent `Settings` locals from appearing in Cloud Run logs.
- The `run_review` ADK tool return dict contains only review findings — no credential values are serialised into Playground responses.

### Model safety settings

Every Gemini call configures explicit Vertex AI / Gemini harm-category thresholds rather than relying on provider defaults. The thresholds are defined once in `quorum.agent._SAFETY_SETTINGS` and applied to all five `GenerateContentConfig` instances in the native review loop (investigation turn, forced summary turn, citation enrichment, and both fix-generation calls) as well as the ADK `root_agent`'s `generate_content_config` (`src/quorum/adk_app.py`).

**Configuration:**
- Categories covered: `HARM_CATEGORY_HARASSMENT`, `HARM_CATEGORY_HATE_SPEECH`, `HARM_CATEGORY_SEXUALLY_EXPLICIT`, `HARM_CATEGORY_DANGEROUS_CONTENT`.
- Threshold: `BLOCK_ONLY_HIGH` for all four.

**Rationale:** Quorum reviews source code, which legitimately includes security/exploit snippets, infrastructure config, and adversarial test fixtures. The default `DANGEROUS_CONTENT` filter treats such content as unsafe and returns empty candidates — which the review loop sees as a `safety_block_or_quota` warning and a zero-finding result. `BLOCK_ONLY_HIGH` still blocks genuinely harmful generation while preventing legitimate code review from being false-positived. The low generation temperature (0.1) further constrains output. If security-heavy diffs are still blocked, the documented fallback is `OFF`/`BLOCK_NONE` for `DANGEROUS_CONTENT` only.

## Disclosure policy

We follow [Coordinated Vulnerability Disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure). Once a fix is merged and released, we will publish a GitHub Security Advisory.
