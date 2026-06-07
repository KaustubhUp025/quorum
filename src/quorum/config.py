"""Runtime configuration loaded from environment variables.

All settings are read from the environment with the ``QUORUM_`` prefix
(e.g. ``QUORUM_GITLAB_TOKEN``) or from a ``.env`` file in the working directory.

GitLab CI auto-injected variables (``CI_PROJECT_ID`` etc.) are matched by their
exact names via ``validation_alias`` — the prefix is NOT applied to those.
"""

import ipaddress

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Cloud metadata / internal addresses that should never be a GitLab URL target.
_SSRF_BLOCKED_HOSTS = frozenset({
    "169.254.169.254",       # AWS/GCP/Azure instance metadata
    "metadata.google.internal",
    "169.254.170.2",         # ECS task metadata
})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="QUORUM_",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Gemini / Vertex AI ---
    gemini_api_key: str | None = Field(default=None, description="Gemini API key (non-GCP path)")
    google_cloud_project: str | None = Field(default=None, description="GCP project for Vertex AI")
    google_cloud_location: str = Field(default="us-central1")
    use_vertex_ai: bool = Field(default=False, description="Route Gemini calls through Vertex AI")
    gemini_model: str = Field(default="gemini-2.5-pro")

    # --- Platform ---
    platform: str = Field(
        default="gitlab",
        description="Target platform: 'gitlab' or 'github'",
    )

    # --- GitLab ---
    gitlab_url: str = Field(default="https://gitlab.com", description="GitLab instance base URL")
    gitlab_token: str = Field(
        default="",
        description="GitLab personal-access or CI job token",
    )

    # --- GitHub ---
    github_token: str | None = Field(
        default=None,
        description="GitHub personal access token (repo + read:discussion scope)",
    )
    gitlab_mcp_path: str = Field(default="/api/v4/mcp", description="MCP endpoint path (official server)")

    # MCP client selection
    mcp_mode: str | None = Field(
        default=None,
        description=(
            "MCP client tier: 'glab' (official CLI, recommended), "
            "'zereight' (community npm), 'rest' (pure Python). "
            "Auto-detected when unset: 'glab' if glab is on PATH, else 'rest'."
        ),
    )

    # Community MCP server command (used when mcp_mode='zereight')
    mcp_server_cmd: str = Field(
        default="npx --yes @zereight/mcp-gitlab",
        description="Command to launch the @zereight/mcp-gitlab MCP server subprocess",
    )

    # GitLab CI auto-populated vars (set by the runner).
    # validation_alias bypasses the QUORUM_ prefix so the runner's native vars work.
    ci_project_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CI_PROJECT_ID", "QUORUM_CI_PROJECT_ID"),
    )
    ci_merge_request_iid: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CI_MERGE_REQUEST_IID", "QUORUM_CI_MERGE_REQUEST_IID"),
    )
    ci_project_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("CI_PROJECT_PATH", "QUORUM_CI_PROJECT_PATH"),
    )

    # --- LLM backend ---
    llm_backend: str = Field(
        default="gemini/gemini-2.5-pro",
        description=(
            "LLM backend for analysis. Prefix 'gemini/' uses the native google-genai SDK "
            "(full features: thinking, grounding). Any other value uses LiteLLM — e.g. "
            "'ollama/mistral', 'openai/gpt-4o', 'anthropic/claude-3-5-sonnet-20241022'. "
            "LiteLLM backends require the model to support OpenAI-compatible tool calling."
        ),
    )

    # --- Agent behaviour ---
    min_confidence: int = Field(
        default=60,
        ge=0,
        le=100,
        description="Findings below this confidence are surfaced as informational only",
    )
    block_on_critical: bool = Field(
        default=True,
        description="Exit 1 (fail the pipeline) when CRITICAL findings exceed threshold",
    )
    max_search_results: int = Field(default=5, description="Max snippets per semantic search call")
    max_tool_rounds: int = Field(
        default=10, description="Hard cap on Gemini tool-call rounds per review"
    )
    disabled_rules: list[str] = Field(
        default_factory=list,
        description=(
            "Rule IDs to skip entirely. "
            "Set via QUORUM_DISABLED_RULES=RULE_07,RULE_08 or in .quorum.yml under rules.disabled."
        ),
    )

    # --- Issue filing ---
    auto_file_issues: bool = Field(
        default=False,
        description=(
            "When True, Quorum automatically files a GitHub/GitLab issue for each HIGH+ finding "
            "after posting the review comment. Falls back to a draft fix PR if issues are disabled, "
            "and logs a manual-action warning if both paths are blocked."
        ),
    )
    auto_file_issues_min_severity: str = Field(
        default="HIGH",
        description="Minimum severity to trigger automatic issue filing: CRITICAL, HIGH, MEDIUM.",
    )

    # --- Fix proposal MRs ---
    create_fix_mrs: bool = Field(
        default=False,
        description=(
            "When True, Quorum generates corrected code and opens a draft MR for each "
            "CRITICAL finding. Requires the GitLab token to have write access to the project."
        ),
    )
    fix_mr_max_count: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Maximum number of fix MRs to open per review (to avoid MR spam).",
    )

    # --- Phase C features ---
    apply_labels: bool = Field(
        default=True,
        description=(
            "When True, Quorum applies a 'quorum-review' label (or 'quorum-review::critical') "
            "to the MR/PR after posting findings. Labels are additive — existing labels are kept."
        ),
    )
    force_review: bool = Field(
        default=False,
        description=(
            "When True, skip the duplicate-review guard and post a new comment even if "
            "Quorum has already reviewed this MR. Useful for re-runs and debugging."
        ),
    )

    # --- CI failure correlation ---
    correlate_ci: bool = Field(
        default=False,
        description=(
            "When True, Quorum checks the MR pipeline for failures and asks Gemini whether "
            "the CI failure is related to any of the coordination findings."
        ),
    )

    # --- Deployment ---
    port: int = Field(default=8080, description="HTTP port for the Cloud Run webhook server")
    log_level: str = Field(default="INFO")
    webhook_secret: str | None = Field(
        default=None,
        description=(
            "Secret token for validating incoming GitLab webhooks (X-Gitlab-Token header). "
            "Set this in GitLab's webhook settings and here. "
            "If unset, the webhook endpoint accepts all requests (insecure)."
        ),
    )

    @field_validator("gitlab_mcp_path")
    @classmethod
    def normalise_mcp_path(cls, v: str) -> str:
        return v if v.startswith("/") else f"/{v}"

    @field_validator("gitlab_url")
    @classmethod
    def validate_gitlab_url(cls, v: str) -> str:
        from urllib.parse import urlparse
        v = v.rstrip("/")
        if not v.startswith(("https://", "http://")):
            raise ValueError("gitlab_url must start with 'https://' or 'http://'")
        # Block requests to cloud metadata endpoints and link-local addresses
        # that could be exploited for SSRF on Cloud Run / GCE.
        parsed = urlparse(v)
        host = parsed.hostname or ""
        if host in _SSRF_BLOCKED_HOSTS:
            raise ValueError(f"gitlab_url hostname '{host}' is blocked (SSRF protection)")
        try:
            addr = ipaddress.ip_address(host)
            if addr.is_link_local or addr.is_loopback or addr.is_private:
                raise ValueError(
                    f"gitlab_url hostname '{host}' resolves to a private/link-local address "
                    "(SSRF protection — use a public GitLab instance URL)"
                )
        except ValueError as exc:
            if "SSRF protection" in str(exc):
                raise
            # Not an IP address — domain name, allow through
        return v

    @property
    def gitlab_mcp_url(self) -> str:
        return f"{self.gitlab_url.rstrip('/')}{self.gitlab_mcp_path}"


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
