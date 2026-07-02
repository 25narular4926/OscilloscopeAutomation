"""The public surface — the integration boundary for the larger test API.

Keep these signatures small, typed, and stable. The larger API's verification
block integrates against exactly these functions; everything else in the package
is an implementation detail behind them.

    configure(transport, config) -> AppliedState
    acquire(transport, timeout)  -> Waveform
    load(path)                   -> Waveform
    measure(wf, kind, **params)  -> float
    compare(wf, expected, tol)   -> Result
    export(wf, path, fmt)        -> None

Plus a couple of higher-level conveniences (``run_test_case``, ``self_test``)
that the bench and the larger API find useful, built only from the above.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .waveform import Waveform
from .transport.base import Transport
from .scope.mso44b import MSO44B, AppliedState
from .analysis.measure import measure as _measure_one, measure_all as _measure_all
from .analysis.compare import Result, Tolerance, compare as _compare_value, compare_all
from .config.schema import ScopeSetup, TestCase
from .io.export import export as _export, load_waveform as _load


# -- the six core functions ---------------------------------------------


def configure(transport: Transport, config: ScopeSetup | Any) -> AppliedState:
    """Apply a scope setup over the transport. Returns what was pushed."""
    return MSO44B(transport).configure(config)


def acquire(transport: Transport, timeout: float = 10.0, source: str = "CH1") -> Waveform:
    """Run one acquisition and return a scaled ``Waveform``."""
    return MSO44B(transport).acquire(timeout=timeout, source=source)


def load(path: str | Path) -> Waveform:
    """Load a saved capture (``.npz`` or ``.csv``) into a ``Waveform``."""
    return _load(path)


def measure(wf: Waveform, kind: str, **params) -> float:
    """Extract one named feature from a waveform (see ``analysis.measure``)."""
    return _measure_one(wf, kind, **params)


def compare(
    wf: Waveform,
    kind: str,
    expected: float,
    tol: Tolerance,
    requirement_id: str | None = None,
) -> Result:
    """Measure ``kind`` on ``wf`` and compare it to ``expected`` within ``tol``."""
    m = measure(wf, kind)
    return _compare_value(
        measured=m,
        expected=expected,
        tol=tol,
        name=kind,
        requirement_id=requirement_id,
    )


def export(wf: Waveform, path: str | Path, fmt: str | None = None) -> None:
    """Write a waveform to ``path`` as csv / npz / mf4."""
    _export(wf, path, fmt)


# -- conveniences built from the core -----------------------------------


def measure_all(wf: Waveform) -> dict[str, float]:
    """The common feature set in one call (for verification blocks that want a dict)."""
    return _measure_all(wf)


def run_test_case(wf: Waveform, case: TestCase) -> list[Result]:
    """Evaluate a loaded :class:`TestCase` against a captured waveform.

    Tags every result with the case's requirement id for ASPICE traceability.
    """
    return compare_all(wf, case.checks(), requirement_id=case.requirement_id)


def self_test(
    scope_transport: Transport,
    afg_transport: Transport,
    setup: ScopeSetup,
    stimulus: "AFGSetup",  # noqa: F821 - forward ref to keep afg import lazy
    checks=None,
    timeout: float = 10.0,
) -> list[Result]:
    """The AFG loopback: drive a known signal, capture it, measure, and compare.

    Validates the whole scope block with no ECM in the loop. ``checks`` defaults
    to asserting the commanded frequency/amplitude/duty within sensible bands.
    This is the only API call that physically drives the bench (AFG output ON).
    """
    from .afg.afg31102 import AFG31102, AFGSetup  # lazy: keep core import light

    assert isinstance(stimulus, AFGSetup)
    afg = AFG31102(afg_transport)
    afg.session_setup()
    scope = MSO44B(scope_transport)
    scope.configure(setup)

    if checks is None:
        checks = _default_loopback_checks(stimulus)

    afg.drive(stimulus)
    try:
        wf = scope.acquire(timeout=timeout, source=setup.channel)
    finally:
        afg.stop(channel=stimulus.channel)
    return compare_all(wf, checks, requirement_id="SELF-TEST")


def _default_loopback_checks(stimulus: "AFGSetup"):  # noqa: F821
    from .analysis.compare import Check

    checks = [
        Check("frequency", stimulus.frequency, Tolerance(rel=0.02), units="Hz"),
        Check("vamplitude", stimulus.amplitude, Tolerance(rel=0.05), units="V"),
    ]
    if stimulus.duty is not None:
        checks.append(Check("duty", stimulus.duty, Tolerance(abs=2.0), units="%"))
    return checks


__all__ = [
    "configure",
    "acquire",
    "load",
    "measure",
    "measure_all",
    "compare",
    "export",
    "run_test_case",
    "self_test",
    "AppliedState",
    "Result",
    "Tolerance",
]
