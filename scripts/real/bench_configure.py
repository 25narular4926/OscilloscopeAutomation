#!/usr/bin/env python3


#   set SCOPE_RESOURCE=TCPIP0::192.168.0.10::INSTR
#   python bench_configure.py

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from tm_devices import DeviceManager
from tm_devices.drivers import MSO4B



# The requested setup and the applied record. Plain dataclasses keep this
# script self-contained on tm_devices + stdlib (the pydantic config schema is
# Phase 2's config/ module).

@dataclass
class ScopeSetup:
    channel: str = "CH1"
    vertical_scale: float | None = None      # volts/div
    vertical_offset: float | None = None     # volts
    coupling: str | None = None              # DC | AC | DCREJ
    sample_rate: float | None = None         # samples/s
    horizontal_scale: float | None = None    # seconds/div
    record_length: int | None = None
    trigger_source: str | None = None
    trigger_level: float | None = None       # volts
    trigger_slope: str | None = None         # RISE | FALL
    encoding: str = "SRIBINARY"
    byte_width: int = 2


@dataclass
class Setting:
  
    label: str          # human/SCPI label, e.g. "CH1:SCAle"
    expected: Any       # the value we wrote
    node: Any           # the command leaf (has .write() / .query())


@dataclass
class AppliedState:
    channel: str
    settings: list[Setting] = field(default_factory=list)
    idn: str = ""


@dataclass
class CheckResult:
    label: str
    expected: Any
    readback: str
    ok: bool


DEFAULT_SETUP = ScopeSetup(
    channel="CH1",
    vertical_scale=0.5,
    vertical_offset=0.0,
    coupling="DC",
    sample_rate=1.25e9,
    record_length=100_000,
    trigger_source="CH1",
    trigger_level=1.0,
    trigger_slope="RISE",
)


def _channel_number(channel: str) -> int:
    """'CH1' -> 1. The command tree keys channels by number."""
    digits = "".join(c for c in channel if c.isdigit())
    return int(digits) if digits else 1


def configure(scope: MSO4B, setup: ScopeSetup) -> AppliedState:
    """Apply vertical/horizontal/trigger/transfer settings; return the applied record.

    Each write also records the command node it wrote to, so `verify()` can later read
    every setting back off the instrument and confirm it landed.
    """
    ch = setup.channel
    n = _channel_number(ch)
    cmds = scope.commands
    settings: list[Setting] = []

    def apply(node: Any, value: Any, label: str) -> None:
        node.write(value)
        settings.append(Setting(label, value, node))

    # Vertical.
    scope.turn_channel_on(ch)
    if setup.vertical_scale is not None:
        apply(cmds.ch[n].scale, setup.vertical_scale, f"{ch}:SCAle")
    if setup.vertical_offset is not None:
        apply(cmds.ch[n].offset, setup.vertical_offset, f"{ch}:OFFSet")
    if setup.coupling:
        apply(cmds.ch[n].coupling, setup.coupling, f"{ch}:COUPling")

    # Horizontal.
    if setup.sample_rate:
        apply(cmds.horizontal.samplerate, setup.sample_rate, "HORizontal:SAMPLERate")
    if setup.horizontal_scale:
        apply(cmds.horizontal.scale, setup.horizontal_scale, "HORizontal:SCAle")
    if setup.record_length:
        apply(cmds.horizontal.recordlength, setup.record_length, "HORizontal:RECOrdlength")

    # Trigger (edge).
    if setup.trigger_source:
        apply(cmds.trigger.a.type, "EDGE", "TRIGger:A:TYPe")
        apply(cmds.trigger.a.edge.source, setup.trigger_source, "TRIGger:A:EDGE:SOUrce")
        if setup.trigger_level is not None:
            tn = _channel_number(setup.trigger_source)
            apply(cmds.trigger.a.level.ch[tn], setup.trigger_level, f"TRIGger:A:LEVel:CH{tn}")
        if setup.trigger_slope:
            apply(cmds.trigger.a.edge.slope, setup.trigger_slope, "TRIGger:A:EDGE:SLOpe")

    # Waveform transfer window.
    apply(cmds.data.source, ch, "DATa:SOURce")
    apply(cmds.data.encdg, setup.encoding, "DATa:ENCdg")
    apply(cmds.data.width, setup.byte_width, "DATa:WIDth")
    apply(cmds.data.start, 1, "DATa:STARt")
    apply(cmds.data.stop, setup.record_length or 1_000_000, "DATa:STOP")

    return AppliedState(channel=ch, settings=settings, idn=scope.idn_string.strip())


# the _matches comparator, 0.1% tolerance + tiny floor, so instrument formatting like 500.0000E-3 for 0.5 or 0.0E+0 for 0.0 still matches.
# Strings: case-insensitive, accepts the scope's abbreviated echo (RIS for RISE).
# gets you within an error
def _matches(expected: Any, readback: str) -> bool:
    """Compare a written value against its read-back, tolerant of instrument formatting."""
    text = readback.strip().strip('"')
    if isinstance(expected, (int, float)):
        try:
            got = float(text)
        except ValueError:
            return False
        # 0.1% relative + a tiny absolute floor (handles 0.0 and exponent formatting).
        return abs(got - float(expected)) <= 1e-9 + 1e-3 * abs(float(expected))

    exp_s = str(expected).strip().strip('"').upper()
    got_s = text.upper()
    return exp_s == got_s or exp_s.startswith(got_s) or got_s.startswith(exp_s)


def verify(scope: MSO4B, applied: AppliedState) -> list[CheckResult]:
    """Query every applied setting back off the scope and compare it to what we wrote.

    Most command leaves expose their own ``.query()``; a few (e.g. ``CH:SCAle``) are
    modeled write-only, so fall back to a raw query built from the node's ``cmd_syntax``.
    """
    results: list[CheckResult] = []
    for s in applied.settings:
        try:
            if hasattr(s.node, "query"):
                readback = str(s.node.query()).strip()
            else:
                readback = str(scope.query(f"{s.node.cmd_syntax}?")).strip()
            ok = _matches(s.expected, readback)
        except Exception as exc:                    # a query that errors counts as a fail
            readback, ok = f"<query error: {exc}>", False
        results.append(CheckResult(s.label, s.expected, readback, ok))
    return results


def report(results: list[CheckResult]) -> bool:
    """Print a PASS/FAIL table; return True only if every check passed."""
    label_w = max((len(r.label) for r in results), default=0)
    passed = sum(r.ok for r in results)
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"  [{mark}] {r.label:<{label_w}}  set {str(r.expected):<12} "
              f"readback {r.readback}")
    total = len(results)
    print(f"\n{passed}/{total} settings verified - "
          f"{'ALL PASSED' if passed == total else 'SOME FAILED'}")
    return passed == total


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure the MSO44B (vertical/horizontal/trigger/transfer) via tm_devices.",
    )
    parser.add_argument("--scope-resource", default=None,
                        help="Scope VISA resource (overrides SCOPE_RESOURCE).")
    parser.add_argument("--channel", default="CH1",
                        help="Channel to configure. Default: CH1.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    scope_resource = args.scope_resource or os.environ.get("SCOPE_RESOURCE")
    if not scope_resource:
        print("No scope address. Set SCOPE_RESOURCE or pass --scope-resource.",
              file=sys.stderr)
        return 2

    setup = DEFAULT_SETUP
    setup.channel = args.channel

    try:
        with DeviceManager(verbose=False) as dm:
            scope = dm.add_scope(scope_resource, alias="scope")
            print("IDN:", scope.idn_string.strip())

            applied = configure(scope, setup)
            print(f"Applied {len(applied.settings)} settings to {applied.channel}. "
                  f"Reading them back:\n")

            # Read EVERY applied setting back off the scope and check it landed.
            all_ok = report(verify(scope, applied))
            return 0 if all_ok else 1

    except Exception as exc:  # tm_devices raises SystemError/VISA errors on bad connect
        print(f"Error configuring the scope: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
