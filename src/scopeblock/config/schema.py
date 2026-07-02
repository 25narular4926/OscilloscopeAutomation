"""Typed config models (pydantic) for a test case.

A test case bundles: how to set up the scope, what to expect, and a requirement
id for traceability. Validate this before touching hardware.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..analysis.compare import Check, Tolerance


class ScopeSetup(BaseModel):
    """Channel / timebase / trigger + transfer settings consumed by ``MSO44B``."""

    channel: str = "CH1"
    coupling: str | None = "DC"
    vertical_scale: float | None = Field(default=None, description="volts/div")
    vertical_offset: float | None = None
    sample_rate: float | None = Field(default=None, description="samples/s")
    horizontal_scale: float | None = Field(default=None, description="seconds/div")
    record_length: int | None = None

    trigger_source: str | None = "CH1"
    trigger_level: float | None = None
    trigger_slope: str | None = "RISe"

    encoding: str = "SRIBinary"
    byte_width: int = 2

    @field_validator("channel", "trigger_source")
    @classmethod
    def _upper(cls, v: str | None) -> str | None:
        return v.upper() if isinstance(v, str) else v


class ExpectedMeasurement(BaseModel):
    """One expected feature with its tolerance, as written in TOML."""

    kind: str
    expected: float
    units: str = ""
    abs: float | None = None
    rel: float | None = None
    min: float | None = None
    max: float | None = None

    def to_check(self) -> Check:
        return Check(
            kind=self.kind,
            expected=self.expected,
            units=self.units,
            tol=Tolerance(abs=self.abs, rel=self.rel, min=self.min, max=self.max),
        )


class TestCase(BaseModel):
    """A complete, traceable test case."""

    __test__ = False  # not a pytest test class despite the name

    requirement_id: str = Field(..., description="e.g. REQ-ECM-PWM-001, for ASPICE rollup")
    name: str = ""
    description: str = ""
    setup: ScopeSetup = Field(default_factory=ScopeSetup)
    expects: list[ExpectedMeasurement] = Field(default_factory=list)

    def checks(self) -> list[Check]:
        return [e.to_check() for e in self.expects]
