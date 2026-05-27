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

Quorum sends MR diffs and `semantic_code_search` results directly to Gemini. A malicious MR author can include text designed to manipulate the model's output (e.g. `<!-- IGNORE PREVIOUS INSTRUCTIONS -->`).

**Current mitigation:** Findings are parsed as structured JSON. Free-text from the model is never executed. Only the JSON schema fields are used.

**Recommendation for production:** Run Quorum only on trusted projects, or add a pre-processing step that strips HTML comments and suspicious injection patterns from diff text before it is included in the Gemini prompt.

### Webhook secret verification

The `quorum serve` FastAPI server accepts GitLab webhooks. Without a webhook secret, any HTTP client can trigger a review.

**Mitigation:** Set `QUORUM_WEBHOOK_SECRET` to a random string. GitLab signs every webhook with this secret. The server validates the `X-Gitlab-Token` header and returns `403` on mismatch.

## Disclosure policy

We follow [Coordinated Vulnerability Disclosure](https://en.wikipedia.org/wiki/Coordinated_vulnerability_disclosure). Once a fix is merged and released, we will publish a GitHub Security Advisory.
