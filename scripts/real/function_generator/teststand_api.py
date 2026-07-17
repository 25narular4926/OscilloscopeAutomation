#!/usr/bin/env python3
"""TestStand-facing API for the AFG31102 function generator (via its raw Socket Server).

The function-generator twin of the oscilloscope's teststand_api.py. Every function takes
and returns ONLY primitive types (str / int / float / bool / list), so TestStand can bind
them straight to sequence variables with no parsing.

Requires: TestStand 2019 or newer (Python Module adapter). No VISA needed.


SAFETY
------
Setting up a waveform and switching an output ON are two SEPARATE calls. configure() and
set_waveform() never enable an output. You must call output_on() deliberately - it drives
real hardware. all_off() is the safe way to leave the bench.


HOW IT MAPS ONTO A TESTSTAND SEQUENCE
-------------------------------------
    Setup:    connect("169.254.8.135")           -> IDN string
              configure("sine_1k", "1")          -> Boolean (pass/fail)
              output_on(1)                        -> Boolean

    Main:     ... run the oscilloscope capture, measure, etc ...

    Cleanup:  all_off()                           -> Boolean
              disconnect()                        -> Boolean
"""

from __future__ import annotations

import os
import sys

# TestStand may load this module by file path, so its own folder might not be on the
# import search path. Put it there first, so "import afg_socket" resolves to the file
# sitting next to us.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import afg_socket as afg  # noqa: E402  (must come after the sys.path fix above)

# ---------------------------------------------------------------------------
# Module-level session state.
# ---------------------------------------------------------------------------
_gen: afg.SocketAFG | None = None
_config_report: str = ""


def _require_gen() -> afg.SocketAFG:
    if _gen is None:
        raise RuntimeError("Not connected. Call connect(host) first.")
    return _gen


def _parse_channels(channels: str) -> list[int]:
    """'1,2' -> [1, 2]. Empty string -> []."""
    return [int(c) for c in str(channels).split(",") if c.strip()]


# ---------------------------------------------------------------------------
# Connection  (TestStand: Setup / Cleanup)
# ---------------------------------------------------------------------------
def connect(host: str, port: int = 4000) -> str:
    """Open the connection to the AFG. Returns the *IDN? identity string.

    Raises if the AFG cannot be reached (TestStand shows this as a step error).
    """
    global _gen, _config_report
    disconnect()
    _config_report = ""
    try:
        _gen = afg.SocketAFG(host, int(port))
    except OSError as exc:
        raise RuntimeError(
            f"Cannot reach the AFG at {host}:{port} - {exc}. "
            f"Is the Socket Server ON (Terminal mode) on that port?"
        ) from exc
    return _gen.query("*IDN?")


def disconnect() -> bool:
    """Close the connection. Safe to call even if not connected. Always True.

    Note: this does NOT switch outputs off. Call all_off() first if you want the bench
    left quiet.
    """
    global _gen
    if _gen is not None:
        try:
            _gen.close()
        except Exception:
            pass
        _gen = None
    return True


def is_connected() -> bool:
    """True if a session is currently open."""
    return _gen is not None


def identify() -> str:
    """The AFG's *IDN? string (requires an open connection)."""
    return _require_gen().query("*IDN?")


# ---------------------------------------------------------------------------
# Configure  (TestStand: a Boolean pass/fail step)
# ---------------------------------------------------------------------------
def list_setups() -> list[str]:
    """Names of the available named setups, e.g. ['pulse_1k_50duty', 'sine_1k']."""
    return list(afg.SETUPS)


def configure(setup_name: str, channels: str = "") -> bool:
    """Apply a named waveform setup, then read every setting back and check it landed.

    channels : "1" or "1,2" to force channels, or "" to use the channels the setup
               itself defines.

    Applies waveform parameters ONLY. Does NOT switch any output on - call output_on()
    for that. Returns True only if every setting read back correctly.
    """
    global _config_report
    gen = _require_gen()

    setup = afg.SETUPS.get(setup_name)
    if setup is None:
        raise ValueError(
            f"Unknown setup {setup_name!r}. Available: {', '.join(afg.SETUPS)}"
        )

    chans = _parse_channels(channels) or sorted(setup.channels) or [1]
    applied = afg.configure(gen, setup, chans)
    results = afg.verify(gen, applied)

    passed = sum(1 for r in results if r.ok)
    lines = [f"Setup '{setup.name}' applied to " + ", ".join(f"CH{c}" for c in chans), ""]
    width = max((len(r.label) for r in results), default=0)
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        lines.append(f"[{mark}] {r.label:<{width}}  set {r.expected}  readback {r.readback}")
    lines.append("")
    lines.append(f"{passed}/{len(results)} settings verified")
    _config_report = "\n".join(lines)

    return passed == len(results)


def get_config_report() -> str:
    """The PASS/FAIL table from the last configure(), as text for the report."""
    return _config_report


def set_waveform(channel: int = 1, shape: str = "SIN", frequency: float = 1000.0,
                 amplitude: float = 1.0, offset: float = 0.0,
                 duty_cycle: float = 0.0) -> bool:
    """Set one channel's waveform directly, without a named setup, and verify it.

    Use this when you want TestStand to pass the numbers in as step parameters instead of
    naming a config file. Does NOT switch the output on.

    channel    : 1 or 2.
    shape      : "SIN", "SQUare", "RAMP", "PULSe", etc.
    frequency  : Hz.
    amplitude  : Vpp.
    offset     : volts.
    duty_cycle : percent; only sent when it is > 0 (pulse/square).

    Returns True only if every setting read back correctly.
    """
    global _config_report
    gen = _require_gen()
    cw = afg.ChannelWaveform(
        shape=str(shape), frequency=float(frequency), amplitude=float(amplitude),
        offset=float(offset),
        duty_cycle=float(duty_cycle) if duty_cycle and duty_cycle > 0 else None,
    )
    setup = afg.WaveformSetup(name="direct", channels={int(channel): cw})
    applied = afg.configure(gen, setup, [int(channel)])
    results = afg.verify(gen, applied)

    passed = sum(1 for r in results if r.ok)
    lines = [f"CH{channel} waveform set directly", ""]
    width = max((len(r.label) for r in results), default=0)
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        lines.append(f"[{mark}] {r.label:<{width}}  set {r.expected}  readback {r.readback}")
    _config_report = "\n".join(lines)
    return passed == len(results)


# ---------------------------------------------------------------------------
# Output control  (TestStand: Boolean steps). EXPLICIT - drives real hardware.
# ---------------------------------------------------------------------------
def output_on(channel: int = 1) -> bool:
    """Switch a channel's output ON. This drives real hardware - call it deliberately."""
    afg.output_on(_require_gen(), int(channel))
    return True


def output_off(channel: int = 1) -> bool:
    """Switch a channel's output OFF."""
    afg.output_off(_require_gen(), int(channel))
    return True


def all_off() -> bool:
    """Switch every output OFF - the safe way to leave the bench in Cleanup."""
    afg.all_outputs_off(_require_gen())
    return True


def output_is_on(channel: int = 1) -> bool:
    """True if the channel's output is currently ON."""
    return afg.output_state(_require_gen(), int(channel))


# ---------------------------------------------------------------------------
# Raw SCPI escape hatch
# ---------------------------------------------------------------------------
def query(scpi: str) -> str:
    """Send any SCPI query and return the reply, e.g. query("SOURce1:FREQuency?")."""
    return _require_gen().query(str(scpi))


def send(scpi: str) -> bool:
    """Send any SCPI command (no reply expected). Always returns True."""
    _require_gen().write(str(scpi))
    return True


# ---------------------------------------------------------------------------
# One-shot helper - connect, do one thing, disconnect.
# ---------------------------------------------------------------------------
def quick_identify(host: str, port: int = 4000) -> str:
    """Connect, read *IDN?, disconnect. Returns the identity string."""
    connect(host, port)
    try:
        return identify()
    finally:
        disconnect()
