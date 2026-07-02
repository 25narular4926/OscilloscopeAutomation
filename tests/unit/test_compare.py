"""The comparison engine — pass and fail, abs / rel / band tolerances."""

from __future__ import annotations

from scopeblock.analysis.compare import Check, Result, Tolerance, compare, compare_all
from tests.fixtures import make_pwm


def test_absolute_tolerance_pass_and_fail():
    r = compare(1000.0, 1000.0, Tolerance(abs=5.0), name="frequency")
    assert r.passed
    r2 = compare(1010.0, 1000.0, Tolerance(abs=5.0), name="frequency")
    assert not r2.passed


def test_relative_tolerance():
    r = compare(1040.0, 1000.0, Tolerance(rel=0.05), name="frequency")  # within 5%
    assert r.passed
    r2 = compare(1060.0, 1000.0, Tolerance(rel=0.05), name="frequency")
    assert not r2.passed


def test_explicit_band_overrides_abs_rel():
    tol = Tolerance(abs=0.01, min=0.0, max=10.0)
    r = compare(7.5, 5.0, tol, name="vpp")
    assert r.passed  # band wins over the tiny abs


def test_widest_of_abs_and_rel_wins():
    # abs=1 vs rel=0.05*1000=50 -> band is ±50
    tol = Tolerance(abs=1.0, rel=0.05)
    lo, hi = tol.band(1000.0)
    assert (lo, hi) == (950.0, 1050.0)


def test_result_error_fields():
    r = compare(1010.0, 1000.0, Tolerance(abs=20.0), name="freq", units="Hz")
    assert r.error == 10.0
    assert abs(r.error_pct - 1.0) < 1e-9
    assert "PASS" in str(r)


def test_compare_all_against_pwm():
    wf = make_pwm(frequency=1000.0, v_low=0.0, v_high=5.0, duty=40.0)
    checks = [
        Check("frequency", 1000.0, Tolerance(rel=0.01), units="Hz"),
        Check("duty", 40.0, Tolerance(abs=1.0), units="%"),
        Check("vamplitude", 5.0, Tolerance(rel=0.05), units="V"),
    ]
    results = compare_all(wf, checks, requirement_id="REQ-1")
    assert all(r.passed for r in results)
    assert all(r.requirement_id == "REQ-1" for r in results)
