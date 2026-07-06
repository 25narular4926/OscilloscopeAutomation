#!/usr/bin/env python3
"""
bench_configure.py — Function 3 (configure) as a standalone script.

Pushes a complete scope setup — vertical, horizontal, trigger, and the
waveform-transfer settings — to the MSO44B and returns exactly what was sent
(an AppliedState command log) for traceability. Reuses connect()/identify()
from bench_identify.py for the session; scaling happens nowhere here.

The SCPI is emitted in a deliberate order via a send() helper that skips any
field left None, so a partial config leaves the rest at instrument defaults:

    vertical    SELect:<ch> ON, <ch>:SCAle, <ch>:OFFSet, <ch>:COUPling
    horizontal  HORizontal:SAMPLERate, HORizontal:SCAle, HORizontal:RECOrdlength
    trigger     TRIGger:A:TYPe EDGE, :EDGE:SOUrce, :LEVel:<src>, :EDGE:SLOpe
    transfer    DATa:SOURce/ENCdg/WIDth/STARt/STOP

--dry-run builds the command log against an in-memory recorder (no hardware),
so the exact SCPI and its order can be verified offline.

Exit codes:
    0  configured, error queue clean (or dry-run printed the command log)
    1  VISA I/O failure or an instrument-reported error
    2  no resource: SCOPE_RESOURCE unset and --resource not given
    3  pyvisa not installed
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from bench_identify import connect, identify


class ScopeSetup(BaseModel):
    """A validated scope setup. Any None field is left at the instrument default."""

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


@dataclass
class AppliedState:
    """What configure() actually pushed — the traceable record of the setup."""

    channel: str
    commands: list[str] = field(default_factory=list)
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


def configure(inst, config: Any) -> AppliedState:
    """Apply vertical/horizontal/trigger/transfer settings; return the command log."""
    ch = getattr(config, "channel", "CH1")
    cmds: list[str] = []

    def send(cmd: str) -> None:
        inst.write(cmd)
        cmds.append(cmd)

    # Vertical.
    send(f"SELect:{ch} ON")
    if getattr(config, "vertical_scale", None) is not None:
        send(f"{ch}:SCAle {config.vertical_scale}")
    if getattr(config, "vertical_offset", None) is not None:
        send(f"{ch}:OFFSet {config.vertical_offset}")
    if getattr(config, "coupling", None):
        send(f"{ch}:COUPling {config.coupling}")

    # Horizontal.
    if getattr(config, "sample_rate", None):
        send(f"HORizontal:SAMPLERate {config.sample_rate}")
    if getattr(config, "horizontal_scale", None):
        send(f"HORizontal:SCAle {config.horizontal_scale}")
    record_length = getattr(config, "record_length", None)
    if record_length:
        send(f"HORizontal:RECOrdlength {record_length}")

    # Trigger.
    if getattr(config, "trigger_source", None):
        send("TRIGger:A:TYPe EDGE")
        send(f"TRIGger:A:EDGE:SOUrce {config.trigger_source}")
        if getattr(config, "trigger_level", None) is not None:
            send(f"TRIGger:A:LEVel:{config.trigger_source} {config.trigger_level}")
        if getattr(config, "trigger_slope", None):
            send(f"TRIGger:A:EDGE:SLOpe {config.trigger_slope}")

    # Waveform transfer.
    send(f"DATa:SOURce {ch}")
    send(f"DATa:ENCdg {getattr(config, 'encoding', 'SRIBinary')}")
    send(f"DATa:WIDth {getattr(config, 'byte_width', 2)}")
    send("DATa:STARt 1")
    send(f"DATa:STOP {record_length or 1_000_000}")

    check_errors(inst)
    return AppliedState(
        channel=ch,
        commands=cmds,
        settings={
            "vertical_scale": getattr(config, "vertical_scale", None),
            "record_length": record_length,
            "trigger_source": getattr(config, "trigger_source", None),
        },
    )


class _Recorder:
    """In-memory stand-in for a VISA session: records writes, answers sync queries."""

    def __init__(self) -> None:
        self.history: list[str] = []

    def write(self, cmd: str) -> None:
        self.history.append(cmd)

    def query(self, cmd: str) -> str:
        self.history.append(cmd)
        key = cmd.strip().upper()
        if key == "*ESR?":
            return "0"
        if key.startswith("ALLEV"):
            return '0,"No events to report - queue empty"'
        return ""


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
    config = DEFAULT_SETUP

    if args.dry_run:
        applied = configure(_Recorder(), config)
        print("DRY RUN — commands that would be sent:")
        for cmd in applied.commands:
            print("  ", cmd)
        return 0

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

            applied = configure(inst, config)
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
