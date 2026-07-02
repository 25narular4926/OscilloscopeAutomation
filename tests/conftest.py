"""Shared pytest configuration.

Hardware tests are opt-in: a test marked ``@pytest.mark.hardware`` is skipped
automatically unless ``SCOPE_RESOURCE`` is set (and ``AFG_RESOURCE`` for the
loopback). CI never depends on a bench.
"""

from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SCOPE_RESOURCE"):
        return  # bench present — run hardware tests
    skip_hw = pytest.mark.skip(reason="no SCOPE_RESOURCE set; hardware test skipped")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip_hw)
