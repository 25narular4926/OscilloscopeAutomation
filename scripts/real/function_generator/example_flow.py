#!/usr/bin/env python3
"""Minimal function-generator flow: connect, set waveforms, turn the outputs on.

No command-line flags - edit the CONFIG block below and run. Drives one or more channels
of the AFG through teststand_api.py the same way a TestStand sequence would.

  python example_flow.py

The outputs are left ON when the script finishes (so the signals keep driving your bench).
To switch them off later:  python afg_socket.py --host <ip> --port 5025 --all-off
"""

import os
import sys

# Make "import teststand_api" resolve to the file sitting next to this script.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import teststand_api as afg

# ---- CONFIG: edit these ---------------------------------------------------
HOST = "169.254.8.135"   # AFG IP address
PORT = 5025              # LXI raw-SCPI socket (confirmed working on the AFG31000)

# One entry per channel you want to drive. Add or remove channels here; each is
# configured and switched on independently. shape: SIN | SQUare | RAMP | PULSe.
CHANNELS = {
    1: {"shape": "SIN",    "frequency": 1000.0, "amplitude": 2.0, "offset": 0.0, "duty": 0.0},
    2: {"shape": "SQUare", "frequency": 1000.0, "amplitude": 2.0, "offset": 0.0, "duty": 50.0},
}
# ---------------------------------------------------------------------------


def main() -> None:
    # 1. Connect.
    idn = afg.connect(HOST, PORT)
    print("connect ->", idn.strip())

    # 2. Set the waveform on every channel, and verify each landed.
    for ch, w in CHANNELS.items():
        ok = afg.set_waveform(ch, w["shape"], w["frequency"], w["amplitude"],
                              w["offset"], w["duty"])
        print(f"set_waveform(CH{ch}) -> {ok}")
        print(afg.get_config_report())

    # 3. Output every channel (switch each output ON). Drives real hardware.
    for ch in CHANNELS:
        afg.output_on(ch)
        print(f"output_on({ch}) -> is_on = {afg.output_is_on(ch)}")

    for ch, w in CHANNELS.items():
        print(f"CH{ch}: {w['amplitude']:g} Vpp {w['shape']} at {w['frequency']:g} Hz")

    # 4. Disconnect. Closes the socket only - the outputs stay ON.
    afg.disconnect()
    print("disconnect -> done (outputs stay ON)")


if __name__ == "__main__":
    main()
