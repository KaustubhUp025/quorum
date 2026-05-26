"""Shared pytest fixtures."""

from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(category: str, name: str) -> str:
    """Load a code fixture file as a string."""
    path = FIXTURES_DIR / category / name
    return path.read_text()


@pytest.fixture
def bad_fixture():
    def _load(name: str) -> str:
        return load_fixture("bad", name)
    return _load


@pytest.fixture
def good_fixture():
    def _load(name: str) -> str:
        return load_fixture("good", name)
    return _load
