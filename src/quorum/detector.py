"""Fast coordination-surface pre-filter.

Runs cheap regex/keyword checks before invoking Gemini.
If no surface is detected, the review exits early without any API calls.
"""

from __future__ import annotations

import structlog

from quorum.rules.registry import REGISTRY
from quorum.rules.base import Rule

log = structlog.get_logger(__name__)


def detect_surfaces(diff: str) -> list[Rule]:
    """Return rules whose surface keywords/patterns appear in *diff*.

    This is purely a pre-filter — it does not do semantic analysis.
    A rule appearing here means Gemini *should* investigate; absence means skip.
    """
    triggered: list[Rule] = []
    for rule in REGISTRY.values():
        if rule.matches_surface(diff):
            triggered.append(rule)
            log.debug("surface_detected", rule_id=rule.id, rule_name=rule.name)

    if not triggered:
        log.info("no_coordination_surfaces_found", rules_checked=len(REGISTRY))
    else:
        log.info(
            "surfaces_detected",
            count=len(triggered),
            rules=[r.id for r in triggered],
        )
    return triggered
