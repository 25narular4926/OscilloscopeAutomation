#!/usr/bin/env python3

#   set SCOPE_RESOURCE=TCPIP0::192.168.0.10::INSTR
#   python bench_scope.py --identify
#   python bench_scope.py --capture --channel 1

from __future__ import annotations

import argparse
import os
import sys

from tm_devices import DeviceManager
from tm_devices.drivers import MSO4B



# Connect: tm_devices auto-selects the MSO4B driver from *IDN? (the MSO44B
# resolves to it), so add_scope() opens the session AND identifies in one call.

def open_scope(dm: DeviceManager, scope_resource: str) -> MSO4B:
    return dm.add_scope(scope_resource, alias="scope")

# Print the scope's identity — no manual *IDN? needed.

def identify(scope: MSO4B) -> None:
  
    print(f"[{scope.name}] {scope.idn_string.strip()}")
    print(f"      model={scope.model}  channels={scope.total_channels}  "
          f"resource={scope.resource_expression}")



# Capture: pull the current acquisition off a channel and summarise it.
# curve_query returns the channel's samples as a list.

def capture(scope: MSO4B, channel: int) -> int:
    print(f"Scope: curve query on CH{channel} ...")
    curve = scope.curve_query(channel)
    if not curve:
        print("scope returned an empty curve", file=sys.stderr)
        return 1

    lo, hi = min(curve), max(curve)
    print(f"  {len(curve)} points  span {lo} .. {hi}")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Connect to the MSO44B via tm_devices and read from it.",
    )
    parser.add_argument("--scope-resource", default=None,
                        help="Scope VISA resource (overrides SCOPE_RESOURCE).")
    parser.add_argument("--identify", action="store_true",
                        help="Connect and print the scope's identity, then exit.")
    parser.add_argument("--capture", action="store_true",
                        help="Read the current acquisition off a channel and summarise it.")
    parser.add_argument("--channel", type=int, default=1,
                        help="Channel number to read. Default: 1.")
    parser.add_argument("--standalone", action="store_true",
                        help="Force the PyVISA-py backend (@py) — use on a machine with "
                             "no full VISA (e.g. LAN over VXI-11 with only pyvisa-py).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    scope_resource = args.scope_resource or os.environ.get("SCOPE_RESOURCE")
    if not scope_resource:
        print("No scope address. Set SCOPE_RESOURCE or pass --scope-resource.",
              file=sys.stderr)
        return 2

    try:
        # DeviceManager is a context manager: it closes the connection on exit.
        # currently verbose is off so we don't get any tm_devices clutter. If you want to see the SCPI traffic turn verbose On
        with DeviceManager(verbose=False) as dm:
            # Force PyVISA-py when there's no full VISA on this machine (LAN/VXI-11).
            if args.standalone:
                dm.visa_library = "@py"
            scope = open_scope(dm, scope_resource)
            identify(scope)

            if args.capture:
                return capture(scope, args.channel)
            return 0

    except Exception as exc:  # tm_devices raises SystemError/VISA errors on bad connect
        print(f"Error talking to the scope: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
