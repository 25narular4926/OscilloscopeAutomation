#!/usr/bin/env python3
"""
Exit codes Shortcut: 
    0  session opened, instrument identified, closed clean
    2  no resource: SCOPE_RESOURCE unset and --resource not given (resources listed)
    3  pyvisa not installed
    1  VISA I/O failure (open/timeout/etc.)
"""
from __future__ import annotations

import argparse
import os
import sys


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open a VISA session to the MSO44B and confirm it responds.",
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
        help="I/O timeout in milliseconds (hard cap so a no-reply cannot hang). "
        "Default: 5000.",
    )
    parser.add_argument(
        "--backend",
        default=None,
        help="VISA backend, e.g. '@py' (pyvisa-py) or "
        "'scripts/sim_mso44b.yaml@sim' (pyvisa-sim). Overrides the VISA_BACKEND "
        "env var. Default: the system VISA (NI-VISA / TekVISA).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

  
    try:
        import pyvisa
    except ImportError:
        print("pyvisa is not installed. Install it with:  pip install pyvisa",
              file=sys.stderr)
        return 3

    resource = args.resource or os.environ.get("SCOPE_RESOURCE") # use --resource if it was given;
    # otherwise fall back to the SCOPE_RESOURCE environment variable, instrument address never hardcoded


    # create the backend
    backend = args.backend or os.environ.get("VISA_BACKEND", "")

    # loads the VISA driver if no pyvisa backend is found then pyvisa raise ValueError
    try:
        rm = pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()
    except Exception as exc:
        print(f"No VISA backend available: {exc}", file=sys.stderr)
        print("Install NI-VISA or TekVISA, or for a pure-Python backend: "
              "pip install pyvisa-py", file=sys.stderr)
        return 3

    # No resource given: list what VISA can see and exit with guidance.
    if not resource:  # triggers only if you gave neither --resource nor SCOPE_RESOURCE.
        print("SCOPE_RESOURCE is unset and --resource was not given.", file=sys.stderr)
        try:
            visible = rm.list_resources()
        except Exception as exc: 
            visible = ()
            print(f"(could not list resources: {exc})", file=sys.stderr)
        print("Visible VISA resources:", visible or "(none found)", file=sys.stderr)
        print("Set SCOPE_RESOURCE or pass --resource <string> and retry.",
              file=sys.stderr)
        return 2

    try:
        with rm.open_resource(resource) as inst:  #creates a context manager
            inst.timeout = args.timeout          
            inst.read_termination = "\n"
            inst.write_termination = "\n"

            # Handshake: prove two-way communication.
            idn = inst.query("*IDN?").strip()
            print("IDN:", idn) # prints out identity of the resource or device

            # Session hygiene
            for cmd in ("HEADer OFF", "VERBose OFF", "*CLS"):
                inst.write(cmd)

            # Read the error/event queue back — empty means the commands stuck.
            errors = inst.query("ALLEV?").strip()
            print("ERR:", errors)

        return 0
    # catches VISA IO Error essentially timeout, cable pulled, or bad command
    except pyvisa.errors.VisaIOError as exc:
        print(f"VISA I/O error talking to {resource!r}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
