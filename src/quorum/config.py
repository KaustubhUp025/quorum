"""Runtime configuration loaded from environment variables.

All settings are read from the environment with the ``QUORUM_`` prefix
(e.g. ``QUORUM_GITLAB_TOKEN``) or from a ``.env`` file in the working directory.

GitLab CI auto-injected variables (``CI_PROJECT_ID`` etc.) are matched by their
exact names via ``validation_alias`` — the prefix is NOT applied to those.
"""

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # --- GitLab ---
    gitlab_url: str = Field(default="https://gitlab.com", description="GitLab instance base URL")
    gitlab_token: str = Field(description="GitLab personal-access or CI job token")
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

    # --- Deployment ---
    port: int = Field(default=8080, description="HTTP port for the Cloud Run webhook server")
    log_level: str = Field(default="INFO")

    @field_validator("gitlab_mcp_path")
    @classmethod
    def normalise_mcp_path(cls, v: str) -> str:
        return v if v.startswith("/") else f"/{v}"

    @property
    def gitlab_mcp_url(self) -> str:
        return f"{self.gitlab_url.rstrip('/')}{self.gitlab_mcp_path}"


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
