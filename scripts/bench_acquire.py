#!/usr/bin/env python3

# from __future__ import annotations

# python bench_acquire.py --backend sim_mso44b.yaml@sim --resource "TCPIP0::sim-scope::INSTR"



import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from bench_configure import ScopeError, check_errors
from bench_identify import connect, identify


class AcquireTimeout(ScopeError):
    """The capture did not complete within the timeout — a typed no-trigger error."""


@dataclass
class Waveform:

    t: np.ndarray                       # seconds
    v: np.ndarray                       # volts
    dt: float                           # sample interval (s)
    t0: float                           # time of the first sample (s)
    channel: str
    units: str = "V"                    # default of V
    meta: dict[str, Any] = field(default_factory=dict)    # scaling factors

    def __len__(self) -> int:
        return int(self.v.size)


# Timeout-ish exceptions that mean "the capture never completed". pyvisa is imported
# lazily and its timeout error added only if the import succeeds.
def _timeout_errors() -> tuple[type[BaseException], ...]:
    errs: list[type[BaseException]] = [TimeoutError]
    try:
        import pyvisa

        errs.append(pyvisa.errors.VisaIOError)
    except Exception:
        pass
    return tuple(errs)


def acquire(inst, timeout: float = 10.0, source: str = "CH1") -> Waveform:

    # A real pyvisa session enforces the wall-clock cap via its own timeout; set it
    # to the acquire timeout so a no-trigger raises instead of blocking forever.
    if hasattr(inst, "timeout"):
        inst.timeout = int(timeout * 1000)  # pyvisa timeout is in milliseconds

    # single sequence, then arm. Explicit single-shot, never free-run.
    inst.write("ACQuire:STOPAfter SEQuence")
    inst.write("ACQuire:STATE RUN")

    # block until the capture completes; a timeout is a typed no-trigger error.
    try:
        opc = inst.query("*OPC?").strip()  # asks if the operation is complete
    except _timeout_errors() as exc:
        raise AcquireTimeout(
            f"capture did not complete within {timeout:g}s (no trigger?): {exc}"
        ) from exc
    if opc != "1":
        raise AcquireTimeout(f"unexpected *OPC? reply {opc!r} (expected '1')")

    check_errors(inst)
    return read_waveform(inst, source)

# set VISA timeout ─► arm single-seq ─► *OPC? (blocks) ─┬─ timeout ─► raise AcquireTimeout
#                                                        ├─ not "1"  ─► raise AcquireTimeout
#                                                        └─ "1" ─► check_errors ─┬─ fault ─► raise ScopeError
#                                                                                └─ ok ─► read_waveform ─► Waveform
# the flow diagram


def read_waveform(inst, source: str = "CH1") -> Waveform:

    # noticed that the CURVe command cannot be run using binary in the sim so thus have to convert the wave into ASCII 

    inst.write(f"DATa:SOURce {source}")
    inst.write("DATa:ENCdg ASCii")     # ASCII so the curve round-trips through pyvisa-sim

    def qf(field_name: str) -> str:
        return inst.query(f"WFMOutpre:{field_name}?").strip()  # WFMOutpre is a tektronix command group holding metadata that describes the next CURVe? transfer

    xincr = float(qf("XINCR"))
    xzero = float(qf("XZERO"))
    pt_off = int(float(qf("PT_OFF")))
    ymult = float(qf("YMULT"))
    yoff = float(qf("YOFF"))
    yzero = float(qf("YZERO"))
    nr_pt = int(float(qf("NR_PT")))
    try:
        units = qf("YUNit").strip('"') or source
    except Exception:
        units = "V"

    codes = _read_curve_ascii(inst)
    if nr_pt and codes.size != nr_pt:
        # Not fatal — trust the response length, but the discrepancy is recorded in meta.
        pass

    v = (codes - yoff) * ymult + yzero
    n = np.arange(v.size, dtype=np.float64)
    t = xzero + (n - pt_off) * xincr

    return Waveform(
        t=t,
        v=v,
        dt=xincr,
        t0=float(t[0]) if t.size else xzero,
        channel=source,
        units=units,
        meta={
            "XINCR": xincr, "XZERO": xzero, "PT_OFF": pt_off,
            "YMULT": ymult, "YOFF": yoff, "YZERO": yzero,
            "NR_PT": nr_pt, "ENCDG": "ASCII",
        },
    )


def _read_curve_ascii(inst) -> np.ndarray:
    """Read an ASCII CURVe? response — comma-separated integer codes — as a float array."""
    raw = inst.query("CURVe?").strip()
    if not raw:
        raise ValueError("CURVe? response was empty")
    return np.array([float(tok) for tok in raw.split(",") if tok], dtype=np.float64)


def _summarize(wf: Waveform) -> None:
    print(f"Waveform: {len(wf)} samples on {wf.channel}")
    print(f"  dt   = {wf.dt:g} s   t0 = {wf.t0:g} s")
    print(f"  span = {wf.t[0]:g} .. {wf.t[-1]:g} s")
    print(f"  Vpp  = {wf.v.max() - wf.v.min():g} {wf.units}  "
          f"(min {wf.v.min():g}, max {wf.v.max():g})")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Arm a single-sequence capture on the MSO44B and read it back.",
    )
    parser.add_argument(
        "--resource",
        default=None,
        help="VISA resource string (overrides the SCOPE_RESOURCE env var).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=5000,
        help="Connection I/O timeout in milliseconds. Default: 5000.",
    )
    parser.add_argument(
        "--backend",
        default=None,
        help="VISA backend, e.g. '@py' (pyvisa-py) or 'sim_mso44b.yaml@sim'.",
    )
    parser.add_argument(
        "--source",
        default="CH1",
        help="Channel to read back, e.g. CH1. Default: CH1.",
    )
    parser.add_argument(
        "--acquire-timeout",
        type=float,
        default=10.0,
        help="Max seconds to wait for the capture to complete. Default: 10.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # --- Hardware / sim: connect, identify, then acquire. ---
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

    try:
        inst, rm = connect(resource, args.timeout, backend)
        with inst:
            idn = identify(inst)
            print("IDN:", idn)
            wf = acquire(inst, timeout=args.acquire_timeout, source=args.source)
            _summarize(wf)
        return 0

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
