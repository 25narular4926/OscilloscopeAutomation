"""Load + validate TOML test cases into :class:`TestCase` models."""

from __future__ import annotations

from pathlib import Path

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - fallback for <3.11
    import tomli as tomllib  # type: ignore[no-redef]

from .schema import TestCase


def load_test_case_str(text: str) -> TestCase:
    """Parse and validate a TOML test case from a string."""
    data = tomllib.loads(text)
    return TestCase.model_validate(data)


def load_test_case(path: str | Path) -> TestCase:
    """Load, parse, and validate a TOML test case from a file."""
    raw = Path(path).read_text(encoding="utf-8")
    return load_test_case_str(raw)
