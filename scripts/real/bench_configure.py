#!/usr/bin/env python3

# Scope configuration on real hardware, via Tektronix's tm_devices driver.
#
# The tm_devices counterpart to ../sim/bench_configure.py. Same idea — apply
# vertical / horizontal / trigger / transfer settings from a ScopeSetup — but instead
# of hand-building SCPI strings it drives the driver's typed command tree
# (scope.commands.*), each node of which maps 1:1 to the SCPI the sim script sends.
#
# tm_devices verifies every write against the instrument's error queue by default, so a
# rejected setting raises instead of failing silently — no hand-written check_errors().
#
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
class AppliedState:
    channel: str
    settings: dict[str, Any] = field(default_factory=dict)
    idn: str = ""


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
    """Apply vertical/horizontal/trigger/transfer settings; return the applied record."""
    ch = setup.channel
    n = _channel_number(ch)
    applied: dict[str, Any] = {}

    def log(key: str, value: Any) -> None:
        applied[key] = value

    cmds = scope.commands

    # Vertical.
    scope.turn_channel_on(ch)
    log(f"{ch} display", "ON")
    if setup.vertical_scale is not None:
        cmds.ch[n].scale.write(setup.vertical_scale)      # CH<n>:SCAle
        log(f"{ch}:SCAle", setup.vertical_scale)
    if setup.vertical_offset is not None:
        cmds.ch[n].offset.write(setup.vertical_offset)    # CH<n>:OFFSet
        log(f"{ch}:OFFSet", setup.vertical_offset)
    if setup.coupling:
        cmds.ch[n].coupling.write(setup.coupling)         # CH<n>:COUPling
        log(f"{ch}:COUPling", setup.coupling)

    # Horizontal.
    if setup.sample_rate:
        cmds.horizontal.samplerate.write(setup.sample_rate)      # HORizontal:SAMPLERate
        log("HORizontal:SAMPLERate", setup.sample_rate)
    if setup.horizontal_scale:
        cmds.horizontal.scale.write(setup.horizontal_scale)      # HORizontal:SCAle
        log("HORizontal:SCAle", setup.horizontal_scale)
    if setup.record_length:
        cmds.horizontal.recordlength.write(setup.record_length)  # HORizontal:RECOrdlength
        log("HORizontal:RECOrdlength", setup.record_length)

    # Trigger.
    if setup.trigger_source:
        cmds.trigger.a.type.write("EDGE")                        # TRIGger:A:TYPe EDGE
        cmds.trigger.a.edge.source.write(setup.trigger_source)   # TRIGger:A:EDGE:SOUrce
        log("TRIGger:A:EDGE:SOUrce", setup.trigger_source)
        if setup.trigger_level is not None:
            tn = _channel_number(setup.trigger_source)
            cmds.trigger.a.level.ch[tn].write(setup.trigger_level)  # TRIGger:A:LEVel:CH<tn>
            log(f"TRIGger:A:LEVel:CH{tn}", setup.trigger_level)
        if setup.trigger_slope:
            cmds.trigger.a.edge.slope.write(setup.trigger_slope)    # TRIGger:A:EDGE:SLOpe
            log("TRIGger:A:EDGE:SLOpe", setup.trigger_slope)

    # Waveform transfer window.
    cmds.data.source.write(ch)                               # DATa:SOURce
    cmds.data.encdg.write(setup.encoding)                    # DATa:ENCdg
    cmds.data.width.write(setup.byte_width)                  # DATa:WIDth
    cmds.data.start.write(1)                                 # DATa:STARt
    cmds.data.stop.write(setup.record_length or 1_000_000)   # DATa:STOP
    log("DATa:ENCdg", setup.encoding)
    log("DATa:WIDth", setup.byte_width)

    return AppliedState(channel=ch, settings=applied, idn=scope.idn_string.strip())


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
            print(f"Applied {len(applied.settings)} settings to {applied.channel}.")

            # Read one setting back as a sanity check that the writes landed.
            readback = scope.commands.horizontal.recordlength.query()
            print("HORizontal:RECOrdlength? ->", readback)
            return 0

    except Exception as exc:  # tm_devices raises SystemError/VISA errors on bad connect
        print(f"Error configuring the scope: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
