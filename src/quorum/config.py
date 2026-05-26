"""Runtime configuration loaded from environment variables."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
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
    gitlab_mcp_path: str = Field(default="/api/v4/mcp", description="MCP endpoint path")

    # GitLab CI auto-populated vars (set by the runner)
    ci_project_id: str | None = Field(default=None, alias="CI_PROJECT_ID")
    ci_merge_request_iid: str | None = Field(default=None, alias="CI_MERGE_REQUEST_IID")
    ci_project_path: str | None = Field(default=None, alias="CI_PROJECT_PATH")

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
