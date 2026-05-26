"""Base class for all Quorum coordination rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Rule:
    """Describes one coordination anti-pattern that Quorum can detect.

    Rules are pure data — detection logic and reasoning live in the agent.
    Each rule contributes:
    - Surface keywords for the fast pre-filter (cheap regex before calling Gemini)
    - Semantic search query templates that guide the MCP investigation step
    - A focused Gemini reasoning prompt that checks *only* this rule
    """

    id: str
    name: str
    description: str
    reference: str
    reference_url: str
    surface_keywords: list[str]
    search_query_templates: list[str]
    reasoning_guidance: str
    surface_patterns: list[str] = field(default_factory=list)

    def matches_surface(self, diff: str) -> bool:
        """Fast pre-filter: returns True if the diff likely touches this rule's surface."""
        diff_lower = diff.lower()
        keyword_hit = any(kw.lower() in diff_lower for kw in self.surface_keywords)
        if not keyword_hit:
            return False
        if self.surface_patterns:
            return any(re.search(p, diff, re.IGNORECASE | re.MULTILINE) for p in self.surface_patterns)
        return True

    def build_search_queries(self, diff: str) -> list[str]:
        """Produce concrete search queries by filling templates from diff context."""
        return list(self.search_query_templates)

    def __str__(self) -> str:
        return f"[{self.id}] {self.name}"
