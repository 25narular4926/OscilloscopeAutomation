"""The comparison engine — expected vs measured + tolerances -> pass/fail.

A failure is a *result*, not an exception: the engine always returns a
:class:`Result` carrying the measured value, the expected value, the band, and
the verdict, so a test report can show exactly why something failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..waveform import Waveform
from .measure import measure


@dataclass(frozen=True)
class Tolerance:
    """A tolerance band. Combine absolute and relative; the wider of the two wins.

    Or pin an explicit ``[min, max]`` band, which overrides abs/rel entirely.
    """

    abs: float | None = None      # ± absolute, in the measurement's units
    rel: float | None = None      # ± fraction of |expected| (0.05 == 5%)
    min: float | None = None      # explicit lower bound
    max: float | None = None      # explicit upper bound

    def band(self, expected: float) -> tuple[float, float]:
        if self.min is not None or self.max is not None:
            lo = self.min if self.min is not None else float("-inf")
            hi = self.max if self.max is not None else float("inf")
            return lo, hi
        half = 0.0
        if self.abs is not None:
            half = max(half, self.abs)
        if self.rel is not None:
            half = max(half, abs(expected) * self.rel)
        return expected - half, expected + half


@dataclass
class Result:
    """The verdict for a single measured feature."""

    name: str
    measured: float
    expected: float
    lower: float
    upper: float
    passed: bool
    units: str = ""
    requirement_id: str | None = None

    @property
    def error(self) -> float:
        return self.measured - self.expected

    @property
    def error_pct(self) -> float:
        return 100.0 * self.error / self.expected if self.expected else float("nan")

    def __str__(self) -> str:
        verdict = "PASS" if self.passed else "FAIL"
        tag = f" [{self.requirement_id}]" if self.requirement_id else ""
        return (
            f"{verdict}{tag} {self.name}: measured={self.measured:.6g}{self.units} "
            f"expected={self.expected:.6g}{self.units} "
            f"band=[{self.lower:.6g}, {self.upper:.6g}]"
        )


def compare(
    measured: float,
    expected: float,
    tol: Tolerance,
    name: str = "value",
    units: str = "",
    requirement_id: str | None = None,
) -> Result:
    """Compare a single measured value against an expected value + tolerance."""
    lo, hi = tol.band(expected)
    return Result(
        name=name,
        measured=float(measured),
        expected=float(expected),
        lower=lo,
        upper=hi,
        passed=bool(lo <= measured <= hi),
        units=units,
        requirement_id=requirement_id,
    )


@dataclass
class Check:
    """One expectation to evaluate against a waveform: measure ``kind`` ~ ``expected``."""

    kind: str
    expected: float
    tol: Tolerance
    units: str = ""


def compare_all(
    wf: Waveform,
    checks: Iterable[Check],
    requirement_id: str | None = None,
) -> list[Result]:
    """Run every check against the waveform and return the list of results."""
    results: list[Result] = []
    for chk in checks:
        m = measure(wf, chk.kind)
        results.append(
            compare(
                measured=m,
                expected=chk.expected,
                tol=chk.tol,
                name=chk.kind,
                units=chk.units,
                requirement_id=requirement_id,
            )
        )
    return results
