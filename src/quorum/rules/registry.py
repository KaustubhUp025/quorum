"""Rule registry — auto-discovers all rule_*.py modules in this package."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from quorum.rules.base import Rule

_rules: dict[str, Rule] | None = None


def _load_rules() -> dict[str, Rule]:
    rules: dict[str, Rule] = {}
    package_dir = Path(__file__).parent
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if not module_info.name.startswith("rule_"):
            continue
        module = importlib.import_module(f"quorum.rules.{module_info.name}")
        if hasattr(module, "RULE") and isinstance(module.RULE, Rule):
            rules[module.RULE.id] = module.RULE
    return dict(sorted(rules.items()))


def get_registry() -> dict[str, Rule]:
    global _rules
    if _rules is None:
        _rules = _load_rules()
    return _rules


REGISTRY: dict[str, Rule] = {}


def _init() -> None:
    REGISTRY.update(get_registry())


_init()
