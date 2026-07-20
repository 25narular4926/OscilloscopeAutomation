#!/usr/bin/env python3
"""Minimal function-generator flow: connect, set the waveform, turn the output on.

No command-line flags - edit the values in the CONFIG block below and run. This drives
the AFG through teststand_api.py the same way a TestStand sequence would.

  python example_flow.py

The output is left ON when the script finishes (so the signal keeps driving your bench).
To switch it off later:  python afg_socket.py --host <ip> --all-off
"""

import os
import sys

# Make "import teststand_api" resolve to the file sitting next to this script.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import teststand_api as afg

# ---- CONFIG: edit these ---------------------------------------------------
HOST      = "169.254.8.135"   # AFG IP address
CHANNEL   = 1                 # which output to drive
SHAPE     = "SIN"             # SIN | SQUare | RAMP | PULSe
FREQUENCY = 1000.0            # Hz
AMPLITUDE = 2.0               # Vpp
OFFSET    = 0.0               # volts
DUTY      = 0.0               # percent (pulse/square only; 0 = ignore)
# ---------------------------------------------------------------------------


def main() -> None:
    # 1. Connect.
    idn = afg.connect(HOST)
    print("connect ->", idn.strip())

    # 2. Configure / set the waveform on the channel, and verify it landed.
    ok = afg.set_waveform(CHANNEL, SHAPE, FREQUENCY, AMPLITUDE, OFFSET, DUTY)
    print(f"set_waveform(CH{CHANNEL}) -> {ok}")
    print(afg.get_config_report())

    # 3. Output the waveform (switch the output ON). Drives real hardware.
    afg.output_on(CHANNEL)
    print(f"output_on({CHANNEL}) -> is_on = {afg.output_is_on(CHANNEL)}")
    print(f"CH{CHANNEL} is now driving {AMPLITUDE:g} Vpp {SHAPE} at {FREQUENCY:g} Hz.")

    # 4. Disconnect. This closes the socket only - it does NOT switch the output off,
    #    so the AFG keeps driving the signal after the script exits.
    afg.disconnect()
    print("disconnect -> done (output stays ON)")


if __name__ == "__main__":
    main()
