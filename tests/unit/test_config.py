"""Config validation + loading TOML test cases."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from scopeblock.config import load_test_case_str
from scopeblock.config.schema import ScopeSetup, TestCase


TOML = """
requirement_id = "REQ-ECM-PWM-001"
name = "PWM pin 40% @ 1kHz"
description = "ECM PWM output verification"

[setup]
channel = "ch1"
vertical_scale = 1.0
record_length = 100000
trigger_source = "ch1"
trigger_level = 2.5

[[expects]]
kind = "frequency"
expected = 1000.0
units = "Hz"
rel = 0.01

[[expects]]
kind = "duty"
expected = 40.0
units = "%"
abs = 1.0
"""


def test_load_and_validate():
    case = load_test_case_str(TOML)
    assert isinstance(case, TestCase)
    assert case.requirement_id == "REQ-ECM-PWM-001"
    assert case.setup.channel == "CH1"  # normalized to upper
    assert len(case.expects) == 2


def test_checks_conversion():
    case = load_test_case_str(TOML)
    checks = case.checks()
    assert checks[0].kind == "frequency"
    assert checks[0].tol.rel == 0.01
    assert checks[1].tol.abs == 1.0


def test_missing_requirement_id_fails():
    with pytest.raises(ValidationError):
        TestCase.model_validate({"name": "no req id"})


def test_scope_setup_defaults():
    s = ScopeSetup()
    assert s.channel == "CH1"
    assert s.byte_width == 2
    assert s.encoding == "SRIBinary"
