"""Coordination anti-pattern rules.

Each rule is a standalone module that exports a ``Rule`` instance.
Adding a new rule is a single-file contribution — see ``CONTRIBUTING.md``.
"""

from quorum.rules.registry import REGISTRY, Rule

__all__ = ["REGISTRY", "Rule"]
