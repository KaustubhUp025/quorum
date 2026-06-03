"""Per-project configuration from .quorum.yml.

Priority: .quorum.yml > environment variables > built-in defaults.

Example .quorum.yml:

    rules:
      disabled: [RULE_07]         # skip noisy rules for this project
    confidence_threshold: 75      # override QUORUM_MIN_CONFIDENCE
    ignore_paths:
      - tests/
      - "**/*_test.py"
    create_fix_mrs: true
    platform: github              # override QUORUM_PLATFORM
    llm_backend: ollama/mistral   # override QUORUM_LLM_BACKEND
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from quorum.config import Settings

log = structlog.get_logger(__name__)


@dataclass
class ProjectConfig:
    """Values loaded from .quorum.yml; None means "not set — keep Settings value"."""

    disabled_rules: list[str] = field(default_factory=list)
    confidence_threshold: int | None = None
    ignore_paths: list[str] = field(default_factory=list)
    create_fix_mrs: bool | None = None
    platform: str | None = None
    llm_backend: str | None = None


def load_project_config(path: str = ".quorum.yml") -> ProjectConfig:
    """Load .quorum.yml; return empty ProjectConfig if the file is missing or unreadable."""
    if not os.path.exists(path):
        return ProjectConfig()
    try:
        import yaml  # soft import — pyyaml only needed when file exists
        with open(path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        log.warning("project_config_load_failed", path=path, error=str(exc))
        return ProjectConfig()

    rules_section = data.get("rules") or {}
    disabled_raw = rules_section.get("disabled") or data.get("disabled_rules") or []

    raw_backend = data.get("llm_backend")
    # Accept any string that starts with a known provider prefix or is a Gemini model name.
    # Reject values that look like file:// or other non-LLM URI schemes that LiteLLM
    # might try to open as local resources.
    _safe_backend = None
    if isinstance(raw_backend, str):
        _ALLOWED_PREFIXES = (
            "gemini/", "openai/", "anthropic/", "ollama/", "groq/",
            "together_ai/", "azure/", "bedrock/", "cohere/", "vertex_ai/",
        )
        if any(raw_backend.startswith(p) for p in _ALLOWED_PREFIXES):
            _safe_backend = raw_backend
        else:
            log.warning(
                "project_config_llm_backend_rejected",
                value=raw_backend,
                reason="does not start with a known provider prefix",
            )

    raw_threshold = (
        data.get("confidence_threshold")
        or rules_section.get("confidence_threshold")
    )
    confidence_threshold: int | None = None
    if raw_threshold is not None:
        try:
            confidence_threshold = int(raw_threshold)
        except (TypeError, ValueError):
            log.warning(
                "project_config_confidence_threshold_invalid",
                value=raw_threshold,
                reason="must be an integer",
            )

    config = ProjectConfig(
        disabled_rules=[str(r).upper() for r in disabled_raw],
        confidence_threshold=confidence_threshold,
        ignore_paths=list(data.get("ignore_paths") or []),
        create_fix_mrs=data.get("create_fix_mrs"),
        platform=data.get("platform"),
        llm_backend=_safe_backend,
    )

    log.info(
        "project_config_loaded",
        path=path,
        disabled_rules=config.disabled_rules,
        ignore_paths=config.ignore_paths,
    )
    return config


def apply_project_config(settings: "Settings", config: ProjectConfig) -> "Settings":
    """Return a new Settings with .quorum.yml values applied on top."""
    overrides: dict = {}
    if config.confidence_threshold is not None:
        overrides["min_confidence"] = config.confidence_threshold
    if config.create_fix_mrs is not None:
        overrides["create_fix_mrs"] = config.create_fix_mrs
    if config.platform is not None:
        overrides["platform"] = config.platform
    if config.llm_backend is not None:
        overrides["llm_backend"] = config.llm_backend
    if config.disabled_rules:
        existing = list(getattr(settings, "disabled_rules", []))
        merged = list(dict.fromkeys(existing + config.disabled_rules))
        overrides["disabled_rules"] = merged
    return settings.model_copy(update=overrides) if overrides else settings


def filter_diff_for_ignore_paths(diff: str, ignore_paths: list[str]) -> str:
    """Remove diff hunks for files whose paths match any ignore_paths glob pattern.

    Hunks are delimited by '--- a/<path>' lines. We keep hunks whose file path
    does NOT match any pattern, and drop hunks that do.
    """
    if not ignore_paths:
        return diff

    hunks: list[list[str]] = []
    current: list[str] = []
    for line in diff.splitlines(keepends=True):
        if line.startswith("--- "):
            if current:
                hunks.append(current)
            current = [line]
        else:
            current.append(line)
    if current:
        hunks.append(current)

    kept: list[str] = []
    for hunk in hunks:
        # Extract path from '--- a/<path>' or '--- /dev/null'
        header = hunk[0].strip()
        path = header.removeprefix("--- a/").removeprefix("--- ")
        if path == "/dev/null" or not any(
            fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, pat.lstrip("/"))
            for pat in ignore_paths
        ):
            kept.extend(hunk)

    return "".join(kept)
