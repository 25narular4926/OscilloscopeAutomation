#!/usr/bin/env python3

# from __future__ import annotations
#
# Acquire a waveform, then VALIDATE THE WHOLE WAVE against expected values.
# This is the self-test oracle: drive a known signal, capture it, and assert the
# pipeline reports back what was commanded — no ECM in the loop.
#
# python bench_validate.py --backend sim_mso44b.yaml@sim --resource "TCPIP0::sim-scope::INSTR"

import argparse
import os
import sys
from dataclasses import dataclass

import numpy as np

from bench_acquire import AcquireTimeout, Waveform, acquire
from bench_configure import ScopeError
from bench_identify import connect, identify


# ---------------------------------------------------------------------------
# One validation check: a named comparison with a pass/fail verdict.
# ---------------------------------------------------------------------------
@dataclass
class Check:
    name: str
    ok: bool
    measured: str
    expected: str
    detail: str = ""


# ---------------------------------------------------------------------------
# Feature extraction — measurement, not sample-by-sample compare.
# Levels come from percentiles (never raw min/max, which outliers wreck);
# frequency from rising-edge periods with hysteresis (don't double-count noise).
# ---------------------------------------------------------------------------
def _levels(v: np.ndarray) -> tuple[float, float]:
    """Robust V_high / V_low via percentiles (0.5 / 99.5)."""
    v_low = float(np.percentile(v, 0.5))
    v_high = float(np.percentile(v, 99.5))
    return v_high, v_low


def measure_vpp(v: np.ndarray) -> float:
    v_high, v_low = _levels(v)
    return v_high - v_low


def measure_offset(v: np.ndarray) -> float:
    """DC offset: the median is robust to the sine's symmetric excursions."""
    return float(np.median(v))


def _rising_edges(t: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Rising-edge crossing times of the mid level, gated by hysteresis.

    Interpolates the exact crossing between the straddling samples so the period
    isn't quantised to whole samples.
    """
    v_high, v_low = _levels(v)
    amp = v_high - v_low
    if amp <= 0:
        return np.empty(0)
    mid = 0.5 * (v_high + v_low)
    hyst = 0.10 * amp                       # 10% band suppresses noise re-triggers

    edges: list[float] = []
    armed = True                            # must dip below (mid - hyst) to re-arm
    for i in range(1, v.size):
        if v[i] < mid - hyst:
            armed = True
        if armed and v[i - 1] <= mid < v[i]:
            frac = (mid - v[i - 1]) / (v[i] - v[i - 1])
            edges.append(t[i - 1] + frac * (t[i] - t[i - 1]))
            armed = False
    return np.asarray(edges, dtype=float)


def measure_frequency(t: np.ndarray, v: np.ndarray) -> float:
    """Frequency from the mean interval between successive rising edges."""
    edges = _rising_edges(t, v)
    if edges.size < 2:
        return float("nan")
    return 1.0 / float(np.mean(np.diff(edges)))


def fit_sine(t: np.ndarray, v: np.ndarray, freq: float) -> dict:
    """Least-squares fit v ≈ a·sin(ωt) + b·cos(ωt) + c at a fixed frequency.

    Linear in (a, b, c), so one lstsq solves it. Returns the fitted amplitude and
    offset plus the residual stats — the residual is the WHOLE-WAVE check: if every
    sample follows the expected sinusoid, max|residual| stays tiny.
    """
    w = 2.0 * np.pi * freq
    basis = np.column_stack([np.sin(w * t), np.cos(w * t), np.ones_like(t)])
    coef, *_ = np.linalg.lstsq(basis, v, rcond=None)
    model = basis @ coef
    resid = v - model
    a, b, c = coef
    return {
        "amplitude": float(np.hypot(a, b)),
        "offset": float(c),
        "rms": float(np.sqrt(np.mean(resid**2))),
        "max_abs": float(np.max(np.abs(resid))) if resid.size else float("nan"),
    }


# ---------------------------------------------------------------------------
# The validation itself.
# ---------------------------------------------------------------------------
@dataclass
class Expected:
    freq: float
    vpp: float
    offset: float
    samples: int          # 0 = don't check the count


@dataclass
class Tolerances:
    freq_rel: float       # relative, e.g. 0.05 = 5%
    vpp_rel: float        # relative
    offset_abs: float     # volts
    shape_abs: float      # volts, on max|residual|


def validate(wf: Waveform, exp: Expected, tol: Tolerances) -> list[Check]:
    checks: list[Check] = []

    # --- Structural: is this even a well-formed record? ---
    if exp.samples:
        checks.append(Check(
            "sample count", len(wf) == exp.samples,
            str(len(wf)), str(exp.samples),
        ))

    finite = bool(np.all(np.isfinite(wf.v)) and np.all(np.isfinite(wf.t)))
    checks.append(Check("all samples finite", finite,
                        "no NaN/Inf" if finite else "NaN/Inf present", "no NaN/Inf"))

    mono = bool(wf.t.size >= 2 and np.all(np.diff(wf.t) > 0))
    checks.append(Check("time strictly increasing", mono,
                        "monotonic" if mono else "NON-monotonic", "monotonic"))

    # dt should agree with the actual sample spacing.
    if wf.t.size >= 2:
        dt_meas = float(np.median(np.diff(wf.t)))
        dt_ok = abs(dt_meas - wf.dt) <= 1e-9 + 1e-3 * abs(wf.dt)
        checks.append(Check("dt matches time axis", dt_ok,
                            f"{dt_meas:g} s", f"{wf.dt:g} s"))

    # --- Features: does the wave carry the commanded signal? ---
    vpp = measure_vpp(wf.v)
    checks.append(Check(
        "Vpp", abs(vpp - exp.vpp) <= tol.vpp_rel * exp.vpp,
        f"{vpp:.4g} {wf.units}", f"{exp.vpp:g} +/-{tol.vpp_rel*100:g}%",
    ))

    offset = measure_offset(wf.v)
    checks.append(Check(
        "offset", abs(offset - exp.offset) <= tol.offset_abs,
        f"{offset:.4g} {wf.units}", f"{exp.offset:g} +/-{tol.offset_abs:g}",
    ))

    freq = measure_frequency(wf.t, wf.v)
    freq_ok = np.isfinite(freq) and abs(freq - exp.freq) <= tol.freq_rel * exp.freq
    checks.append(Check(
        "frequency", bool(freq_ok),
        f"{freq:.5g} Hz" if np.isfinite(freq) else "no edges",
        f"{exp.freq:g} +/-{tol.freq_rel*100:g}%",
    ))

    # --- Whole-wave: every sample must lie on the expected sinusoid. ---
    fit_freq = freq if np.isfinite(freq) else exp.freq
    fit = fit_sine(wf.t, wf.v, fit_freq)
    shape_ok = np.isfinite(fit["max_abs"]) and fit["max_abs"] <= tol.shape_abs
    checks.append(Check(
        "shape (sine fit residual)", bool(shape_ok),
        f"max|resid|={fit['max_abs']:.4g} {wf.units} (rms={fit['rms']:.3g})",
        f"max|resid| <= {tol.shape_abs:g} {wf.units}",
        detail=f"fit amplitude={fit['amplitude']:.4g}, offset={fit['offset']:.4g}",
    ))

    return checks


def _print_report(wf: Waveform, checks: list[Check]) -> bool:
    print(f"Validating {len(wf)} samples on {wf.channel} "
          f"(dt={wf.dt:g} s, span {wf.t[0]:g}..{wf.t[-1]:g} s)\n")

    name_w = max(len(c.name) for c in checks)
    all_ok = True
    for c in checks:
        all_ok &= c.ok
        mark = "PASS" if c.ok else "FAIL"
        print(f"  [{mark}] {c.name:<{name_w}}  measured {c.measured}  "
              f"| expected {c.expected}")
        if c.detail:
            print(f"         {' ' * name_w}  ({c.detail})")

    print()
    print("RESULT:", "ALL CHECKS PASSED" if all_ok else "VALIDATION FAILED")
    return all_ok


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acquire a waveform and validate the whole wave against expected values.",
    )
    # Connection (mirrors bench_acquire).
    parser.add_argument("--resource", default=None,
                        help="VISA resource string (overrides SCOPE_RESOURCE).")
    parser.add_argument("--timeout", type=int, default=5000,
                        help="Connection I/O timeout in ms. Default: 5000.")
    parser.add_argument("--backend", default=None,
                        help="VISA backend, e.g. '@py' or 'sim_mso44b.yaml@sim'.")
    parser.add_argument("--source", default="CH1",
                        help="Channel to read back. Default: CH1.")
    parser.add_argument("--acquire-timeout", type=float, default=10.0,
                        help="Max seconds to wait for the capture. Default: 10.")

    # Expected signal — defaults match the sim's synthetic sine.
    parser.add_argument("--expected-freq", type=float, default=25000.0,
                        help="Expected frequency in Hz. Default: 25000 (sim).")
    parser.add_argument("--expected-vpp", type=float, default=4.0,
                        help="Expected peak-to-peak volts. Default: 4.0 (sim).")
    parser.add_argument("--expected-offset", type=float, default=0.0,
                        help="Expected DC offset in volts. Default: 0.0 (sim).")
    parser.add_argument("--expected-samples", type=int, default=200,
                        help="Expected sample count (0 = don't check). Default: 200 (sim).")

    # Tolerances.
    parser.add_argument("--freq-tol", type=float, default=0.05,
                        help="Frequency tolerance, relative. Default: 0.05 (5%%).")
    parser.add_argument("--vpp-tol", type=float, default=0.05,
                        help="Vpp tolerance, relative. Default: 0.05 (5%%).")
    parser.add_argument("--offset-tol", type=float, default=0.05,
                        help="Offset tolerance, absolute volts. Default: 0.05.")
    parser.add_argument("--shape-tol", type=float, default=0.05,
                        help="Max sine-fit residual, absolute volts. Default: 0.05.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        import pyvisa
    except ImportError:
        print("pyvisa is not installed. Install it with:  pip install pyvisa",
              file=sys.stderr)
        return 3

    resource = args.resource or os.environ.get("SCOPE_RESOURCE")
    backend = args.backend or os.environ.get("VISA_BACKEND", "")

    if not resource:
        print("SCOPE_RESOURCE is unset and --resource was not given.", file=sys.stderr)
        print("Set SCOPE_RESOURCE or pass --resource <string>.", file=sys.stderr)
        return 2

    exp = Expected(
        freq=args.expected_freq, vpp=args.expected_vpp,
        offset=args.expected_offset, samples=args.expected_samples,
    )
    tol = Tolerances(
        freq_rel=args.freq_tol, vpp_rel=args.vpp_tol,
        offset_abs=args.offset_tol, shape_abs=args.shape_tol,
    )

    try:
        inst, rm = connect(resource, args.timeout, backend)
        with inst:
            idn = identify(inst)
            print("IDN:", idn)
            wf = acquire(inst, timeout=args.acquire_timeout, source=args.source)
            ok = _print_report(wf, validate(wf, exp, tol))
        return 0 if ok else 1

    except AcquireTimeout as exc:
        print(f"no trigger within {args.acquire_timeout:g}s: {exc}", file=sys.stderr)
        return 1
    except ScopeError as exc:
        print(f"instrument error talking to {resource!r}: {exc}", file=sys.stderr)
        return 1
    except pyvisa.errors.VisaIOError as exc:
        print(f"VISA I/O error talking to {resource!r}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
