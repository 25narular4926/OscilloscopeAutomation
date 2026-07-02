"""Typed, validated test-case configuration. A test case is data (TOML)."""

from __future__ import annotations

from .schema import ScopeSetup, ExpectedMeasurement, TestCase
from .loader import load_test_case, load_test_case_str

__all__ = [
    "ScopeSetup",
    "ExpectedMeasurement",
    "TestCase",
    "load_test_case",
    "load_test_case_str",
]
