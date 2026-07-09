#!/usr/bin/env python3
"""
bench_identify.py — Function 2 (connect / identify) implementation.

This script replaces the initial bench_identity to explicitly contain `connect` 
and `identify` methods, maintaining the standalone pyvisa libraries and 
infrastructure detailed in bench_connect.py.

Exit codes Shortcut: 
    0  session opened, instrument identified, error queue clean
    1  VISA I/O failure (open/timeout/etc.)
    2  no resource: SCOPE_RESOURCE unset and --resource not given (resources listed)
    3  pyvisa not installed
"""
from __future__ import annotations

import argparse
import os
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Connect to the MSO44B through pyvisa and identify it.",
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
        help="I/O timeout in milliseconds (hard cap so a no-reply cannot hang). Default: 5000.",
    )
    parser.add_argument(
        "--backend",
        default=None,
        help="VISA backend, e.g. '@py' (pyvisa-py) or 'scripts/sim_mso44b.yaml@sim' (pyvisa-sim).",
    )
    return parser.parse_args(argv)


def connect(resource: str, timeout: int, backend: str):
    #Acts as the VisaTransport layer.
    #Opens the VISA resource and configures basic I/O settings.
    #does not know anything about oscilloscpe, SCPI, or others, just estabolishes a secure VISA connection
    import pyvisa
    
    try:
        rm = pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()
    except Exception as exc:
        print(f"No VISA backend available: {exc}", file=sys.stderr)
        raise

    inst = rm.open_resource(resource)
    # hard cap timeout
    inst.timeout = timeout          
    inst.read_termination = "\n"
    inst.write_termination = "\n"
    
    return inst, rm


def identify(inst) -> str:

   # Queries *IDN?, sets session hygiene, and returns the identity.
    
    # does not know about any connection, just expects an instrument and thus calls .query
    # Confirm instrument identity
    idn = inst.query("*IDN?").strip()
    
    for cmd in ("HEADer OFF", "VERBose OFF", "*CLS"):
        inst.write(cmd)
        
    return idn


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

    # No resource given: list what VISA can see and exit with guidance.
    if not resource:
        print("SCOPE_RESOURCE is unset and --resource was not given.", file=sys.stderr)
        try:
            rm = pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()
            visible = rm.list_resources()
        except Exception as exc:
            visible = ()
            print(f"(could not list resources: {exc})", file=sys.stderr)
        print("Visible VISA resources:", visible or "(none found)", file=sys.stderr)
        print("Set SCOPE_RESOURCE or pass --resource <string> and retry.", file=sys.stderr)
        return 2

    try:
        # 1. Connect 
        inst, rm = connect(resource, args.timeout, backend)
        
        with inst:
            # 2. Identify 
            idn = identify(inst)
            print("IDN:", idn)

            # Check errors to prove the queue is clean 
            errors = inst.query("ALLEV?").strip()
            print("ERR:", errors if errors else "none")
            
        return 0

    except pyvisa.errors.VisaIOError as exc:
        print(f"VISA I/O error talking to {resource!r}: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())