#!/usr/bin/env python3
"""TestStand-facing API for the MSO44B (via the scope's raw Socket Server).

This is a thin wrapper around bench_socket.py, shaped for NI TestStand's Python
Module adapter. Every function here takes and returns ONLY primitive types
(str / int / float / bool / list), so TestStand can bind them straight to
sequence variables with no parsing.

Requires: TestStand 2019 or newer (Python Module adapter). No VISA needed.


HOW IT MAPS ONTO A TESTSTAND SEQUENCE
-------------------------------------
    Setup:      connect("169.254.8.134")            -> IDN string
                configure("bench_full")             -> Boolean (pass/fail)

    Main:       capture("1,2", 10000, True, 300)    -> Boolean (armed, waited, got data)
                get_vmax(1)                         -> Number  <- put LIMITS on this
                get_vmin(1)                         -> Number  <- and this
                save_png("C:\\results\\wave.png")   -> String (path written)

    Cleanup:    disconnect()


PASS/FAIL vs ERROR
------------------
  - A returned False means "the test failed" (e.g. a setting did not read back).
  - A raised exception means "something is broken" (e.g. cannot reach the scope).
    TestStand turns that into a step error with the message attached.


THREADING
---------
This module holds ONE open connection in a module-level session. That suits the
normal case (one scope, one sequence). If you need several scopes at once, run
them in separate TestStand processes, or ask for a handle-based API.
"""

from __future__ import annotations

import os
import sys

# TestStand loads this module by file path, and depending on how it does that, this
# file's own folder may NOT be on Python's import search path. Put it there first, so
# "import bench_socket" always resolves to the bench_socket.py sitting next to us.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bench_socket as bs  # noqa: E402  (must come after the sys.path fix above)

# ---------------------------------------------------------------------------
# Module-level session state.
# ---------------------------------------------------------------------------
_scope: bs.SocketScope | None = None
_waves: dict[int, bs.Waveform] = {}
_config_report: str = ""
_record_length: int = 0        # points the last configure() set on the scope's record


def _require_scope() -> bs.SocketScope:
    if _scope is None:
        raise RuntimeError("Not connected. Call connect(host) first.")
    return _scope


def _require_wave(channel: int) -> bs.Waveform:
    if channel not in _waves:
        raise RuntimeError(
            f"No captured data for CH{channel}. Call capture() first "
            f"(captured channels: {sorted(_waves) or 'none'})."
        )
    return _waves[channel]


def _parse_channels(channels: str) -> list[int]:
    """'1,2' -> [1, 2]. Empty string -> []."""
    return [int(c) for c in str(channels).split(",") if c.strip()]


# ---------------------------------------------------------------------------
# Connection  (TestStand: Setup / Cleanup)
# ---------------------------------------------------------------------------
def connect(host: str, port: int = 4000) -> str:
    """Open the connection to the scope. Returns the *IDN? identity string.

    Raises if the scope cannot be reached (TestStand shows this as a step error).
    """
    global _scope, _waves, _config_report
    disconnect()                      # drop any stale session first
    _waves = {}
    _config_report = ""
    try:
        _scope = bs.SocketScope(host, int(port))
    except OSError as exc:
        raise RuntimeError(
            f"Cannot reach the scope at {host}:{port} - {exc}. "
            f"Is the Socket Server ON (Utility -> I/O -> Socket Server, "
            f"Protocol = Terminal)?"
        ) from exc
    return _scope.query("*IDN?")


def disconnect() -> bool:
    """Close the connection. Safe to call even if not connected. Always True."""
    global _scope
    if _scope is not None:
        try:
            _scope.close()
        except Exception:
            pass
        _scope = None
    return True


def is_connected() -> bool:
    """True if a session is currently open."""
    return _scope is not None


def identify() -> str:
    """The scope's *IDN? string (requires an open connection)."""
    return _require_scope().query("*IDN?")


# ---------------------------------------------------------------------------
# Configure  (TestStand: a Boolean pass/fail step)
# ---------------------------------------------------------------------------
def list_setups() -> list[str]:
    """Names of the available named setups, e.g. ['default', 'bench', 'bench_full']."""
    return list(bs.SETUPS)


def configure(setup_name: str = "bench_full", channels: str = "",
              duration_s: float = 0.0) -> bool:
    """Apply a named setup, then read EVERY setting back and check it landed.

    channels : "1,2" to force specific channels, or "" to use the channels the
               setup itself defines.
    duration_s : total seconds you want the capture to span. 0 = keep the setup's
               own timebase. Any positive value OVERRIDES it: the scope's sample rate
               is held fixed and the s/div and record length are recomputed from the
               duration (record_length = sample_rate * duration_s). So you pick one
               intuitive number - "I want a 10 second capture" - instead of juggling
               s/div and record length. capture() then defaults to reading exactly
               that many points, so the whole span comes back with no extra math.

    Returns True only if every setting read back correctly. Use get_config_report()
    afterwards to put the detail into the TestStand report.
    """
    global _config_report, _record_length
    scope = _require_scope()

    setup = bs.SETUPS.get(setup_name)
    if setup is None:
        raise ValueError(
            f"Unknown setup {setup_name!r}. Available: {', '.join(bs.SETUPS)}"
        )

    chans = _parse_channels(channels) or sorted(setup.channels) or [1]
    dur = float(duration_s) if duration_s and duration_s > 0 else None
    applied = bs.configure(scope, setup, chans, duration=dur)
    results = bs.verify(scope, applied)

    # Remember the record length that actually landed, so capture() can default its
    # transfer size to the full record instead of making the user recompute points.
    _record_length = 0
    for s in applied:
        lab = s.label.upper()
        if "RECO" in lab or "POIN" in lab:      # Tek HORizontal:RECOrdlength / Keysight :ACQuire:POINts
            try:
                _record_length = int(float(s.expected))
            except (TypeError, ValueError):
                _record_length = 0

    passed = sum(1 for r in results if r.ok)
    lines = [
        f"Setup '{setup.name}' applied to " + ", ".join(f"CH{c}" for c in chans),
        "",
    ]
    width = max((len(r.label) for r in results), default=0)
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        lines.append(f"[{mark}] {r.label:<{width}}  set {r.expected}  "
                     f"readback {r.readback}")
    lines.append("")
    lines.append(f"{passed}/{len(results)} settings verified")
    _config_report = "\n".join(lines)

    return passed == len(results)


def get_config_report() -> str:
    """The PASS/FAIL table from the last configure(), as text for the report."""
    return _config_report


def arm() -> bool:
    """Arm the scope for ONE acquisition and return immediately (does NOT wait).

    Call this BEFORE the event/waveform is generated. The scope then waits for the
    trigger, captures a single record, and freezes it - even while your sequence moves
    on to the generation step. Afterwards, read that frozen record with
    capture(single=False), then save().

    Typical TestStand order:
        connect -> configure -> arm -> [generate waveform] -> capture(single=False)
        -> save -> disconnect

    Returns True once armed. Requires a prior configure() so the trigger mode is NORMal
    (in AUTO the scope self-triggers and may freeze a record WITHOUT your event).
    """
    return bs.arm_acquisition(_require_scope())


def is_acquisition_complete() -> bool:
    """True once the armed acquisition has finished (the scope has stopped).

    This is a one-shot snapshot of the scope's state RIGHT NOW - it does NOT wait. Call
    it once and it returns immediately: True if the record is already complete, False if
    the scope is still armed/acquiring. To actually wait for the record, use
    wait_until_complete() instead (or loop on this in TestStand).
    """
    return not bs.is_running(_require_scope())


def wait_until_complete(timeout_s: float = 120.0) -> bool:
    """Block until the armed acquisition finishes (the record is complete), then return.

    Put this step AFTER the waveform-generation step and BEFORE capture(). It waits
    exactly as long as the record needs - no more, no less - regardless of when in the
    event the trigger fired.

    Returns True once the scope has stopped (record complete). Raises TimeoutError if it
    never completes within timeout_s, which almost always means the trigger never fired
    (check the trigger level/slope/source, or that the signal crossed the threshold).
    """
    scope = _require_scope()
    if not bs.wait_until_stopped(scope, float(timeout_s)):
        raise TimeoutError(
            f"Acquisition did not complete within {timeout_s:g} s. The trigger likely "
            f"never fired - check the trigger level/slope/source, or that the event "
            f"actually crossed the trigger threshold."
        )
    return True


def snapshot_config(name: str, channels: str = "") -> str:
    """Read the scope's CURRENT front-panel settings and save them as configs/<name>.json.

    This is the "tune by hand, then freeze it" step: dial the scope in manually, run
    this once, and from then on configure(name) reproduces exactly those settings. The
    inverse of configure().

    channels : "1,2" to capture specific channels, or "" to auto-detect the ones that
               are currently displayed on the scope.

    Returns the path of the JSON file written. Reloads the setups so the new one is
    immediately usable via configure(name) in this same session.
    """
    scope = _require_scope()
    chans = _parse_channels(channels) or None
    path = bs.snapshot_to_configs(scope, str(name), chans)
    bs.SETUPS = bs.load_setups()      # make the just-saved setup available right away
    return path


def get_record_length() -> int:
    """Record length (points) the last configure() set. 0 if none was applied.

    This is what capture() transfers by default, and (record_length / sample_rate)
    is the total capture duration in seconds.
    """
    return int(_record_length)


# ---------------------------------------------------------------------------
# Capture  (TestStand: a Boolean step, then Number steps for the measurements)
# ---------------------------------------------------------------------------
def capture(channels: str = "1", points: int = 0, single: bool = False,
            timeout_s: float = 120.0) -> bool:
    """Capture one or more channels and hold the data for the get_* functions.

    channels  : "1" or "1,2" etc.
    points    : samples to transfer. 0 (the default) means "the full record length
                the last configure() set" - so a configure(duration_s=...) followed by
                capture() returns the whole span automatically. Pass a positive number
                to transfer only the first N samples.
    single    : True  -> arm ONE acquisition and WAIT for the real trigger, then
                         read it. Use this to catch a specific event.
                 False -> just read whatever record is already in the scope's memory.
    timeout_s : with single=True, how long to wait for the trigger + record.

    Returns True if data came back for EVERY requested channel.
    Raises if single=True and the trigger never fired (that is an error, not a
    silent failure, so TestStand tells you).
    """
    global _waves
    scope = _require_scope()
    chans = _parse_channels(channels) or [1]

    n_points = int(points) if int(points) > 0 else (_record_length or 1000)

    if single:
        if not bs.arm_single(scope, float(timeout_s)):
            raise TimeoutError(
                f"No trigger within {timeout_s:g} s. Check the trigger level/slope, "
                f"and that trigger mode is NORMal (in AUTO the scope self-triggers)."
            )

    _waves = bs.acquire_many(scope, chans, n_points)
    return len(_waves) == len(chans)


def captured_channels() -> list[int]:
    """Which channels actually returned data on the last capture()."""
    return sorted(_waves)


# --- measurements: bind these to TestStand Number steps and apply LIMITS -----
# The math lives in bench_socket (measure_*), so the single-scope and fleet APIs agree.
def get_vmax(channel: int = 1) -> float:
    """Maximum volts."""
    return bs.measure_vmax(_require_wave(int(channel)))


def get_vmin(channel: int = 1) -> float:
    """Minimum volts."""
    return bs.measure_vmin(_require_wave(int(channel)))


def get_mean(channel: int = 1) -> float:
    """Mean (average) volts."""
    return bs.measure_mean(_require_wave(int(channel)))


def get_rms(channel: int = 1) -> float:
    """RMS (root-mean-square) volts: sqrt(mean(v^2)) over the whole record.

    This is the true RMS of the captured samples, so it works for any shape (sine,
    square, pulse, noise), not just a sine wave.
    """
    return bs.measure_rms(_require_wave(int(channel)))


def get_pulse_width(channel: int = 1) -> float:
    """Positive pulse width in seconds: how long the FIRST pulse stays high.

    Levels from percentiles (robust to spikes), midpoint threshold with hysteresis, and
    interpolated crossing times. Returns 0.0 if the signal is flat or no complete pulse
    is found in the record.
    """
    return bs.measure_pulse_width(_require_wave(int(channel)))


def get_sample_count(channel: int = 1) -> int:
    """How many samples were transferred."""
    return int(len(_require_wave(int(channel)).v))


def get_dt(channel: int = 1) -> float:
    """Seconds between consecutive samples (the sampling interval)."""
    return float(_require_wave(int(channel)).dt)


def get_t0(channel: int = 1) -> float:
    """Time of the first sample, in seconds (negative = before the trigger)."""
    return float(_require_wave(int(channel)).t0)


def get_duration(channel: int = 1) -> float:
    """Time from the first sample to the last, in seconds."""
    wf = _require_wave(int(channel))
    return float(wf.t[-1] - wf.t[0])


# --- full arrays: bind to TestStand Number-array variables if you need them ---
def get_volts(channel: int = 1) -> list[float]:
    """Every sample, in volts."""
    return [float(x) for x in _require_wave(int(channel)).v]


def get_times(channel: int = 1) -> list[float]:
    """The time of every sample, in seconds."""
    return [float(x) for x in _require_wave(int(channel)).t]


# ---------------------------------------------------------------------------
# Saving artefacts  (TestStand: String steps - the returned path can be attached
# to the report)
# ---------------------------------------------------------------------------
def save_csv(path: str) -> str:
    """Save the captured data as CSV. Returns the path(s) written, semicolon-separated.

    One channel  -> exactly the path you gave.
    Several      -> path_CH1.csv, path_CH2.csv, ... plus a combined path_joint.csv.
    """
    if not _waves:
        raise RuntimeError("Nothing captured. Call capture() first.")
    _ensure_dir(path)
    written: list[str] = []
    multi = len(_waves) > 1
    for c in sorted(_waves):
        p = bs._derive(path, _waves[c].channel) if multi else path
        bs.save_csv(_waves[c], p)
        written.append(p)
    if multi:
        p = bs._derive(path, "joint")
        bs.save_joint_csv(_waves, p)
        written.append(p)
    return ";".join(written)


def save_png(path: str) -> str:
    """Save a PNG plot. Returns the path(s) written, semicolon-separated.

    One channel  -> exactly the path you gave.
    Several      -> path_CH1.png, ... plus a combined overlay path_joint.png.

    NO third-party packages are required. If matplotlib happens to be installed it
    is used (nicer axes); otherwise a built-in pure-stdlib renderer writes the PNG.
    Either way this works on a locked-down TestStand machine with nothing installed.
    """
    if not _waves:
        raise RuntimeError("Nothing captured. Call capture() first.")

    _ensure_dir(path)
    written: list[str] = []
    multi = len(_waves) > 1
    for c in sorted(_waves):
        p = bs._derive(path, _waves[c].channel) if multi else path
        bs.save_png(_waves[c], p)
        written.append(p)
    if multi:
        p = bs._derive(path, "joint")
        bs.save_png_joint(_waves, p)
        written.append(p)
    return ";".join(written)


def _ensure_dir(path: str) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)


# ---------------------------------------------------------------------------
# Raw SCPI escape hatch
# ---------------------------------------------------------------------------
def query(scpi: str) -> str:
    """Send any SCPI query and return the reply, e.g. query("TRIGger:A:MODe?")."""
    return _require_scope().query(str(scpi))


def send(scpi: str) -> bool:
    """Send any SCPI command (no reply expected). Always returns True."""
    _require_scope().write(str(scpi))
    return True


def is_scope_running() -> bool:
    """True if the scope is acquiring (so its record can be overwritten under you)."""
    return bs.is_running(_require_scope())


# ---------------------------------------------------------------------------
# One-shot helpers - connect, do one thing, disconnect. Handy for a quick
# TestStand step where you do not want to manage a session.
# ---------------------------------------------------------------------------
def quick_identify(host: str, port: int = 4000) -> str:
    """Connect, read *IDN?, disconnect. Returns the identity string."""
    connect(host, port)
    try:
        return identify()
    finally:
        disconnect()
