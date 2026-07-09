#!/usr/bin/env python3


# cd c:/Users/25nar/KPITInternship/Autonomation/scripts

# python -c "import pyvisa, pyvisa_sim; print(pyvisa.__version__, pyvisa_sim.__version__)"

# python bench_configure.py --backend sim_mso44b.yaml@sim --resource "TCPIP0::sim-scope::INSTR"





from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any

# allows for validation without pydantic if the type is incorrect it would accept it silently
# however with pydantic it would catch the error immediately
from pydantic import BaseModel

from bench_identify import connect, identify

# default of none thus | None
class ScopeSetup(BaseModel):

    channel: str = "CH1"
    vertical_scale: float | None = None      # volts/div
    vertical_offset: float | None = None     # volts
    coupling: str | None = None              # DC | AC | DCREJect
    sample_rate: float | None = None         # samples/s
    horizontal_scale: float | None = None    # seconds/div
    record_length: int | None = None
    trigger_source: str | None = None
    trigger_level: float | None = None       # volts
    trigger_slope: str | None = None         # RISe | FALL
    encoding: str = "SRIBinary"
    byte_width: int = 2


@dataclass #dataclass since it just needs to hold and carry the data

# What configure() actually pushed — the traceable record of the setup. "output script"
class AppliedState:

    channel: str
    commands: list[str] = field(default_factory=list) # fresh entry or empty list/dict
    idn: str = ""
    settings: dict[str, Any] = field(default_factory=dict)


class ScopeError(RuntimeError):
    """An instrument-reported error, surfaced from *ESR? / ALLEV?."""


DEFAULT_SETUP = ScopeSetup(
    channel="CH1",
    vertical_scale=0.5,
    vertical_offset=0.0,
    coupling="DC",
    sample_rate=1.25e9,
    record_length=100_000,
    trigger_source="CH1",
    trigger_level=1.0,
    trigger_slope="RISe",
)


def check_errors(inst) -> None:
    """Raise ScopeError if the event status register flags a real error."""
    esr = int(inst.query("*ESR?").strip())
    # bit 5 CME, bit 4 EXE, bit 3 DDE, bit 2 QYE.
    if esr & 0b0011_1100:
        events = inst.query("ALLEV?").strip()
        raise ScopeError(f"instrument error (ESR={esr}): {events}")


def configure(inst, scope_setup: Any) -> AppliedState:
    """Apply vertical/horizontal/trigger/transfer settings; return the command log."""
    ch = getattr(scope_setup, "channel", "CH1")
    cmds: list[str] = []

    # returns cmds as the list in the AppliedState class

    def send(cmd: str) -> None:
        inst.write(cmd)
        cmds.append(cmd)
 
    # Vertical.
    send(f"SELect:{ch} ON")
    if getattr(scope_setup, "vertical_scale", None) is not None:
        send(f"{ch}:SCAle {scope_setup.vertical_scale}")
    if getattr(scope_setup, "vertical_offset", None) is not None:
        send(f"{ch}:OFFSet {scope_setup.vertical_offset}")
    if getattr(scope_setup, "coupling", None):
        send(f"{ch}:COUPling {scope_setup.coupling}")

    # Horizontal.
    if getattr(scope_setup, "sample_rate", None):
        send(f"HORizontal:SAMPLERate {scope_setup.sample_rate}")
    if getattr(scope_setup, "horizontal_scale", None):
        send(f"HORizontal:SCAle {scope_setup.horizontal_scale}")
    record_length = getattr(scope_setup, "record_length", None)
    if record_length:
        send(f"HORizontal:RECOrdlength {record_length}")

    # Trigger.
    if getattr(scope_setup, "trigger_source", None):
        send("TRIGger:A:TYPe EDGE")
        send(f"TRIGger:A:EDGE:SOUrce {scope_setup.trigger_source}")
        if getattr(scope_setup, "trigger_level", None) is not None:
            send(f"TRIGger:A:LEVel:{scope_setup.trigger_source} {scope_setup.trigger_level}")
        if getattr(scope_setup, "trigger_slope", None):
            send(f"TRIGger:A:EDGE:SLOpe {scope_setup.trigger_slope}")

    # Waveform transfer.
    send(f"DATa:SOURce {ch}")
    send(f"DATa:ENCdg {getattr(scope_setup, 'encoding', 'SRIBinary')}")
    send(f"DATa:WIDth {getattr(scope_setup, 'byte_width', 2)}")
    send("DATa:STARt 1")
    send(f"DATa:STOP {record_length or 1_000_000}")

    check_errors(inst)
    return AppliedState(
        channel=ch,
        commands=cmds,
        settings={
            "vertical_scale": getattr(scope_setup, "vertical_scale", None),
            "record_length": record_length,
            "trigger_source": getattr(scope_setup, "trigger_source", None),
        },
    )


# class _Recorder:
#     """In-memory stand-in for a VISA session: records writes, answers sync queries."""

#     def __init__(self) -> None:
#         self.history: list[str] = []

#     def write(self, cmd: str) -> None:
#         self.history.append(cmd)

#     def query(self, cmd: str) -> str:
#         self.history.append(cmd)
#         key = cmd.strip().upper()
#         if key == "*ESR?":
#             return "0"
#         if key.startswith("ALLEV"):
#             return '0,"No events to report - queue empty"'
#         return ""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure the MSO44B (vertical/horizontal/trigger/transfer).",
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the command log against an in-memory recorder — no hardware.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    scope_setup = DEFAULT_SETUP

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
        print("Set SCOPE_RESOURCE or pass --resource <string>, or use --dry-run.",
              file=sys.stderr)
        return 2

    try:
        inst, rm = connect(resource, args.timeout, backend)
        with inst:
            idn = identify(inst)
            print("IDN:", idn)

            applied = configure(inst, scope_setup)
            applied.idn = idn
            print(f"Applied {len(applied.commands)} commands to {applied.channel}.")

            # Read one setting back as a sanity check.
            readback = inst.query("HORizontal:RECOrdlength?").strip()
            print("HORizontal:RECOrdlength? ->", readback)
            print("ERR:", inst.query("ALLEV?").strip() or "none")
        return 0

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



# ways to use this configuration script


# #from bench_configure import ScopeSetup, configure, _Recorder

# setup = ScopeSetup(
#     channel="CH1",
#     vertical_scale=0.5,
#     coupling="DC",
#     record_length=100_000,
#     trigger_source="CH1",
#     trigger_level=1.0,
#     trigger_slope="RISe",
# )

# # dry-run offline:
# applied = configure(_Recorder(), setup)
# print(applied.commands)

# # or on real hardware:
# # from bench_identify import connect, identify
# # inst, rm = connect(resource, 5000, "")
# # with inst:
# #     configure(inst, setup)

