"""Unit tests for .quorum.yml project configuration loading and merging."""

from __future__ import annotations

import os
import textwrap
from unittest.mock import patch

import pytest

from quorum.project_config import (
    ProjectConfig,
    apply_project_config,
    filter_diff_for_ignore_paths,
    load_project_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path, content: str) -> str:
    p = tmp_path / ".quorum.yml"
    p.write_text(textwrap.dedent(content))
    return str(p)


def _make_settings(**kwargs):
    """Create a minimal Settings-like object via the real Settings class."""
    from quorum.config import Settings

    env = {
        "QUORUM_GITLAB_TOKEN": "fake",
        "QUORUM_GEMINI_API_KEY": "fake",
        **{f"QUORUM_{k.upper()}": str(v) for k, v in kwargs.items()},
    }
    with patch.dict(os.environ, env, clear=False):
        return Settings()


# ---------------------------------------------------------------------------
# load_project_config — basic parsing
# ---------------------------------------------------------------------------


class TestLoadProjectConfig:
    def test_missing_file_returns_empty_config(self, tmp_path):
        cfg = load_project_config(str(tmp_path / "nonexistent.yml"))
        assert cfg.disabled_rules == []
        assert cfg.confidence_threshold is None
        assert cfg.ignore_paths == []
        assert cfg.create_fix_mrs is None

    def test_empty_file_returns_empty_config(self, tmp_path):
        p = _write_yaml(tmp_path, "")
        cfg = load_project_config(p)
        assert cfg.disabled_rules == []
        assert cfg.confidence_threshold is None

    def test_disabled_rules_loaded(self, tmp_path):
        p = _write_yaml(tmp_path, """
            rules:
              disabled: [RULE_07, RULE_08]
        """)
        cfg = load_project_config(p)
        assert cfg.disabled_rules == ["RULE_07", "RULE_08"]

    def test_disabled_rules_normalised_to_uppercase(self, tmp_path):
        p = _write_yaml(tmp_path, """
            rules:
              disabled: [rule_07, Rule_08]
        """)
        cfg = load_project_config(p)
        assert cfg.disabled_rules == ["RULE_07", "RULE_08"]

    def test_confidence_threshold_loaded(self, tmp_path):
        p = _write_yaml(tmp_path, "confidence_threshold: 80")
        cfg = load_project_config(p)
        assert cfg.confidence_threshold == 80

    def test_confidence_threshold_under_rules(self, tmp_path):
        p = _write_yaml(tmp_path, """
            rules:
              confidence_threshold: 75
        """)
        cfg = load_project_config(p)
        assert cfg.confidence_threshold == 75

    def test_ignore_paths_loaded(self, tmp_path):
        p = _write_yaml(tmp_path, """
            ignore_paths:
              - tests/
              - "**/*_test.py"
        """)
        cfg = load_project_config(p)
        assert "tests/" in cfg.ignore_paths
        assert "**/*_test.py" in cfg.ignore_paths

    def test_create_fix_mrs_loaded(self, tmp_path):
        p = _write_yaml(tmp_path, "create_fix_mrs: true")
        cfg = load_project_config(p)
        assert cfg.create_fix_mrs is True

    def test_platform_loaded(self, tmp_path):
        p = _write_yaml(tmp_path, "platform: github")
        cfg = load_project_config(p)
        assert cfg.platform == "github"

    def test_llm_backend_loaded(self, tmp_path):
        p = _write_yaml(tmp_path, "llm_backend: ollama/mistral")
        cfg = load_project_config(p)
        assert cfg.llm_backend == "ollama/mistral"

    def test_invalid_yaml_returns_empty_config(self, tmp_path):
        p = tmp_path / ".quorum.yml"
        p.write_text("rules: [invalid: yaml: {")
        cfg = load_project_config(str(p))
        assert cfg.disabled_rules == []


# ---------------------------------------------------------------------------
# apply_project_config — merging
# ---------------------------------------------------------------------------


class TestApplyProjectConfig:
    def test_no_overrides_returns_same_settings(self):
        s = _make_settings()
        merged = apply_project_config(s, ProjectConfig())
        assert merged.min_confidence == s.min_confidence

    def test_confidence_threshold_override(self):
        s = _make_settings()
        merged = apply_project_config(s, ProjectConfig(confidence_threshold=90))
        assert merged.min_confidence == 90

    def test_create_fix_mrs_override(self):
        s = _make_settings()
        assert s.create_fix_mrs is False
        merged = apply_project_config(s, ProjectConfig(create_fix_mrs=True))
        assert merged.create_fix_mrs is True

    def test_platform_override(self):
        s = _make_settings()
        merged = apply_project_config(s, ProjectConfig(platform="github"))
        assert merged.platform == "github"

    def test_llm_backend_override(self):
        s = _make_settings()
        merged = apply_project_config(s, ProjectConfig(llm_backend="ollama/mistral"))
        assert merged.llm_backend == "ollama/mistral"

    def test_disabled_rules_merged_with_existing(self):
        s = _make_settings()
        # Start with no disabled rules
        assert s.disabled_rules == []
        merged = apply_project_config(s, ProjectConfig(disabled_rules=["RULE_07"]))
        assert "RULE_07" in merged.disabled_rules

    def test_disabled_rules_deduplicated(self):
        from quorum.config import Settings
        import os
        env = {
            "QUORUM_GITLAB_TOKEN": "fake",
            "QUORUM_GEMINI_API_KEY": "fake",
            "QUORUM_DISABLED_RULES": '["RULE_07"]',
        }
        with patch.dict(os.environ, env, clear=False):
            s = Settings()
        merged = apply_project_config(s, ProjectConfig(disabled_rules=["RULE_07", "RULE_08"]))
        assert merged.disabled_rules.count("RULE_07") == 1
        assert "RULE_08" in merged.disabled_rules

    def test_original_settings_not_mutated(self):
        s = _make_settings()
        original_confidence = s.min_confidence
        apply_project_config(s, ProjectConfig(confidence_threshold=99))
        assert s.min_confidence == original_confidence


# ---------------------------------------------------------------------------
# filter_diff_for_ignore_paths
# ---------------------------------------------------------------------------


class TestFilterDiffForIgnorePaths:
    _DIFF = textwrap.dedent("""\
        --- a/src/app.py
        +++ b/src/app.py
        @@ -1,3 +1,4 @@
        +import redis
         import os
        --- a/tests/test_app.py
        +++ b/tests/test_app.py
        @@ -1,2 +1,3 @@
        +def test_new():
         pass
        --- a/src/lock.py
        +++ b/src/lock.py
        @@ -1 +1,2 @@
        +redis.set("key", "value", nx=True)
    """)

    def test_no_ignore_paths_returns_full_diff(self):
        result = filter_diff_for_ignore_paths(self._DIFF, [])
        assert result == self._DIFF

    def test_ignore_tests_directory(self):
        result = filter_diff_for_ignore_paths(self._DIFF, ["tests/*"])
        assert "tests/test_app.py" not in result
        assert "src/app.py" in result
        assert "src/lock.py" in result

    def test_ignore_glob_pattern(self):
        result = filter_diff_for_ignore_paths(self._DIFF, ["**/*_test.py", "tests/*"])
        assert "tests/test_app.py" not in result

    def test_ignore_specific_file(self):
        result = filter_diff_for_ignore_paths(self._DIFF, ["src/app.py"])
        assert "src/app.py" not in result
        assert "src/lock.py" in result

    def test_ignore_all_src_files(self):
        result = filter_diff_for_ignore_paths(self._DIFF, ["src/*"])
        assert "src/app.py" not in result
        assert "src/lock.py" not in result
        assert "tests/test_app.py" in result

    def test_empty_diff_unchanged(self):
        assert filter_diff_for_ignore_paths("", ["tests/*"]) == ""
