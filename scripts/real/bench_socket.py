#!/usr/bin/env python3

# No-VISA fallback: talk to the MSO44B over its raw Socket Server, for benches where
# tm_devices/VISA can't connect (e.g. link-local LAN where only the scope's Socket
# Server is reachable). Pure Python stdlib — no VISA, no tm_devices, no pyvisa.
#
# The scope's Socket Server must be ON. On this scope, Utility -> I/O -> Socket Server:
#   Enabled via Protocol = Terminal (Protocol "None" leaves the port closed), Port 4000.
# Terminal mode echoes commands / adds a prompt, so replies are cleaned below.
#
# Capabilities (the same shape as bench_scope.py + bench_configure.py, but over the
# socket instead of tm_devices):
#   --identify   *IDN?  ->  the scope's identity
#   --configure  apply a ScopeSetup (vertical/horizontal/trigger/transfer) then read
#                every setting back and print a PASS/FAIL table
#   --capture    pull a waveform as ASCII (comma-separated codes), scale it, summarise
#   --query      send any text SCPI query
# Capture uses ASCII encoding (not binary) so the curve round-trips through the
# Terminal-mode socket — the same approach the ../sim scripts use.
#
#   python bench_socket.py --host 169.254.8.134 --identify
#   python bench_socket.py --host 169.254.8.134 --configure
#   python bench_socket.py --host 169.254.8.134 --capture --channel 1

from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import time
from dataclasses import dataclass
from typing import Any


class SocketScope:
    """Minimal raw-socket SCPI client for the Tektronix Socket Server (Terminal mode)."""

    def __init__(self, host: str, port: int = 4000, timeout: float = 5.0) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(1.0)      # per-read timeout used to detect "reply done"
        self._drain()                  # discard any connect-time banner / prompt

    def _drain(self) -> None:
        """Read and throw away whatever is already waiting (banner, stale prompt)."""
        try:
            while self.sock.recv(4096):
                pass
        except socket.timeout:
            pass

    def _read(self) -> str:
        """Read until the scope goes quiet (a short read-timeout marks the end)."""
        chunks: list[bytes] = []
        try:
            while True:
                data = self.sock.recv(4096)
                if not data:
                    break
                chunks.append(data)
        except socket.timeout:
            pass
        return b"".join(chunks).decode(errors="replace")

    def query(self, cmd: str, *, debug: bool = False) -> str:
        self.sock.sendall(cmd.encode() + b"\n")
        time.sleep(0.2)
        raw = self._read()
        if debug:
            print(f"  raw reply: {raw!r}", file=sys.stderr)
        return _clean(raw, cmd)

    def query_raw(self, cmd: str) -> str:
        """Return the FULL reply, uncleaned — for big replies (a curve) that may
        arrive wrapped across many lines in Terminal mode."""
        self.sock.sendall(cmd.encode() + b"\n")
        time.sleep(0.2)
        return self._read()

    def write(self, cmd: str) -> None:
        self.sock.sendall(cmd.encode() + b"\n")
        time.sleep(0.1)
        self._drain()

    def close(self) -> None:
        self.sock.close()


def _clean(raw: str, cmd: str) -> str:
    """Strip Terminal-mode echo and prompt noise; return the meaningful reply line.

    Terminal mode may echo the command back and wrap the answer in prompt characters
    (e.g. a trailing '>'). We split into lines, drop the echoed command and empty/prompt
    lines, and return the last real line — which for a query is the response.
    """
    lines = [ln.strip(" \t\r\n>") for ln in raw.replace("\r", "\n").split("\n")]
    lines = [ln for ln in lines if ln and ln != cmd.strip()]
    return lines[-1] if lines else ""


# ---------------------------------------------------------------------------
# Configure — the same job as bench_configure.py, over the socket. Sends the
# standard Tek SCPI writes, then reads every setting back and checks it landed.
# ---------------------------------------------------------------------------
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


@dataclass
class Setting:
    label: str        # the SCPI header, e.g. "CH1:SCAle"
    expected: Any     # the value we wrote
    query: str        # the query to read it back, e.g. "CH1:SCAle?"


@dataclass
class CheckResult:
    label: str
    expected: Any
    readback: str
    ok: bool


def _channel_number(channel: str) -> int:
    digits = "".join(c for c in channel if c.isdigit())
    return int(digits) if digits else 1


def configure(scope: SocketScope, setup: ScopeSetup) -> list[Setting]:
    """Apply vertical/horizontal/trigger/transfer settings; return what was applied."""
    ch = setup.channel
    settings: list[Setting] = []

    def apply(base: str, value: Any) -> None:
        scope.write(f"{base} {value}")
        settings.append(Setting(base, value, f"{base}?"))

    # Vertical.
    scope.write(f"SELect:{ch} ON")
    if setup.vertical_scale is not None:
        apply(f"{ch}:SCAle", setup.vertical_scale)
    if setup.vertical_offset is not None:
        apply(f"{ch}:OFFSet", setup.vertical_offset)
    if setup.coupling:
        apply(f"{ch}:COUPling", setup.coupling)

    # Horizontal.
    if setup.sample_rate:
        apply("HORizontal:SAMPLERate", setup.sample_rate)
    if setup.horizontal_scale:
        apply("HORizontal:SCAle", setup.horizontal_scale)
    if setup.record_length:
        apply("HORizontal:RECOrdlength", setup.record_length)

    # Trigger (edge).
    if setup.trigger_source:
        apply("TRIGger:A:TYPe", "EDGE")
        apply("TRIGger:A:EDGE:SOUrce", setup.trigger_source)
        if setup.trigger_level is not None:
            tn = _channel_number(setup.trigger_source)
            apply(f"TRIGger:A:LEVel:CH{tn}", setup.trigger_level)
        if setup.trigger_slope:
            apply("TRIGger:A:EDGE:SLOpe", setup.trigger_slope)

    return settings


def _matches(expected: Any, readback: str) -> bool:
    """Compare a written value against its read-back, tolerant of formatting."""
    text = readback.strip().strip('"')
    if isinstance(expected, (int, float)):
        try:
            got = float(text)
        except ValueError:
            return False
        return abs(got - float(expected)) <= 1e-9 + 1e-3 * abs(float(expected))
    exp_s = str(expected).strip().strip('"').upper()
    got_s = text.upper()
    return exp_s == got_s or exp_s.startswith(got_s) or got_s.startswith(exp_s)


def verify(scope: SocketScope, settings: list[Setting]) -> list[CheckResult]:
    """Read every applied setting back off the scope and compare it to what we wrote."""
    results: list[CheckResult] = []
    for s in settings:
        readback = scope.query(s.query)
        results.append(CheckResult(s.label, s.expected, readback, _matches(s.expected, readback)))
    return results


def report(results: list[CheckResult]) -> bool:
    """Print a PASS/FAIL table; return True only if every check passed."""
    if not results:
        print("no settings to verify")
        return True
    label_w = max(len(r.label) for r in results)
    passed = sum(r.ok for r in results)
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"  [{mark}] {r.label:<{label_w}}  set {str(r.expected):<12} "
              f"readback {r.readback}")
    total = len(results)
    print(f"\n{passed}/{total} settings verified - "
          f"{'ALL PASSED' if passed == total else 'SOME FAILED'}")
    return passed == total


# ---------------------------------------------------------------------------
# Capture — the same job as bench_scope.py --capture, over the socket. Pulls an
# ASCII curve (comma-separated codes), applies the affine scaling, and can print
# a summary, save the samples to CSV, or plot them (ASCII in-terminal, or a PNG).
# ---------------------------------------------------------------------------
@dataclass
class Waveform:
    channel: str
    t: list[float]      # seconds
    v: list[float]      # volts
    dt: float
    t0: float


def acquire(scope: SocketScope, channel: int = 1, points: int = 1000) -> Waveform | None:
    """Pull an ASCII curve off a channel and scale it to a Waveform (None if empty)."""
    source = f"CH{channel}"
    scope.write(f"DATa:SOURce {source}")
    scope.write("DATa:ENCdg ASCii")          # ASCII so the curve comes back as text
    scope.write("DATa:STARt 1")
    scope.write(f"DATa:STOP {points}")

    def qf(field: str) -> float:
        return float(scope.query(f"WFMOutpre:{field}?"))

    xincr = qf("XINCR")
    xzero = qf("XZERO")
    pt_off = int(qf("PT_OFF"))
    ymult = qf("YMULT")
    yoff = qf("YOFF")
    yzero = qf("YZERO")

    # Read the curve RAW (not line-cleaned): a big ASCII curve can arrive wrapped
    # across many lines in Terminal mode. Drop the echoed command, then strip ALL
    # whitespace before splitting on commas — that heals numbers split across a
    # wrap boundary (e.g. "18\n0" -> "180") and removes any trailing prompt.
    raw = scope.query_raw("CURVe?")
    raw = re.sub(r"(?i)curve\?", "", raw)          # drop echoed command
    raw = re.sub(r"[^0-9eE+.\-,]", "", raw)        # keep only number/comma chars
    codes: list[float] = []
    for tok in raw.split(","):
        try:
            codes.append(float(tok))
        except ValueError:
            pass
    if not codes:
        return None

    v = [(c - yoff) * ymult + yzero for c in codes]
    t = [xzero + (i - pt_off) * xincr for i in range(len(v))]
    return Waveform(source, t, v, xincr, t[0])


def summarize(wf: Waveform) -> None:
    vmin, vmax = min(wf.v), max(wf.v)
    print(f"Waveform: {len(wf.v)} samples on {wf.channel}")
    print(f"  dt   = {wf.dt:g} s   t0 = {wf.t0:g} s")
    print(f"  span = {wf.t[0]:g} .. {wf.t[-1]:g} s")
    print(f"  Vpp  = {vmax - vmin:g} V  (min {vmin:g}, max {vmax:g})")


def save_csv(wf: Waveform, path: str) -> None:
    """Write the scaled waveform as CSV: index, time_s, volts."""
    import csv
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["index", "time_s", "volts"])
        for i, (t, v) in enumerate(zip(wf.t, wf.v)):
            writer.writerow([i, f"{t:.9g}", f"{v:.6g}"])
    print(f"saved {len(wf.v)} samples to {path}")


def ascii_plot(wf: Waveform, width: int = 70, height: int = 21) -> None:
    """Draw a simple ASCII plot of the waveform in the terminal (no libraries)."""
    v, n = wf.v, len(wf.v)
    vmin, vmax = min(v), max(v)
    span = (vmax - vmin) or 1.0
    grid = [[" "] * width for _ in range(height)]

    # zero line first, so samples draw over it
    if vmin <= 0 <= vmax:
        zrow = round((vmax - 0.0) / span * (height - 1))
        grid[zrow] = ["-"] * width

    for col in range(width):
        idx = round(col * (n - 1) / (width - 1)) if width > 1 else 0
        row = round((vmax - v[idx]) / span * (height - 1))
        grid[row][col] = "*"

    # ASCII only (the Windows console can't render box-drawing characters).
    print(f"  {vmax:+.3g} V +" + "-" * width)
    for r in grid:
        print("           |" + "".join(r))
    print(f"  {vmin:+.3g} V +" + "-" * width)
    print(f"             {wf.t[0]:g} s{' ' * max(1, width - 14)}{wf.t[-1]:g} s")


def save_png(wf: Waveform, path: str) -> bool:
    """Save a PNG plot via matplotlib if it's installed; otherwise say so."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed - skipping PNG. Install it with "
              "'pip install matplotlib', or use --plot for an ASCII plot.",
              file=sys.stderr)
        return False
    plt.figure(figsize=(9, 4))
    plt.plot(wf.t, wf.v, linewidth=0.8)
    plt.xlabel("time (s)")
    plt.ylabel("volts")
    plt.title(f"{wf.channel} capture ({len(wf.v)} samples)")
    plt.grid(True, alpha=0.3)
    plt.savefig(path, dpi=110, bbox_inches="tight")
    plt.close()
    print(f"saved plot to {path}")
    return True


def capture(scope: SocketScope, channel: int = 1, points: int = 1000, *,
            save: str | None = None, plot: bool = False,
            plot_png: str | None = None) -> int:
    wf = acquire(scope, channel, points)
    if wf is None:
        print("no curve data returned (is a signal being acquired?)", file=sys.stderr)
        return 1
    summarize(wf)
    if save:
        save_csv(wf, save)
    if plot:
        ascii_plot(wf)
    if plot_png:
        save_png(wf, plot_png)
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Talk to the MSO44B over its raw Socket Server (no VISA needed).",
    )
    parser.add_argument("--host", default=None,
                        help="Scope IP address (overrides the SCOPE_HOST env var).")
    parser.add_argument("--port", type=int, default=4000,
                        help="Socket Server port. Default: 4000.")
    parser.add_argument("--identify", action="store_true",
                        help="Query *IDN? and print the scope's identity (the default).")
    parser.add_argument("--configure", action="store_true",
                        help="Apply DEFAULT_SETUP and print a read-back PASS/FAIL table.")
    parser.add_argument("--capture", action="store_true",
                        help="Pull an ASCII waveform off a channel and summarise it.")
    parser.add_argument("--channel", type=int, default=1,
                        help="Channel number for --configure/--capture. Default: 1.")
    parser.add_argument("--points", type=int, default=1000,
                        help="Number of samples to transfer for --capture. Default: 1000.")
    parser.add_argument("--save", metavar="CSV", default=None,
                        help="With --capture: save the waveform to a CSV file (index, time, volts).")
    parser.add_argument("--plot", action="store_true",
                        help="With --capture: draw an ASCII plot in the terminal (no libraries).")
    parser.add_argument("--plot-png", metavar="PNG", default=None,
                        help="With --capture: save a PNG plot (needs matplotlib).")
    parser.add_argument("--query", metavar="SCPI", default=None,
                        help="Send an arbitrary SCPI query and print the reply.")
    parser.add_argument("--debug", action="store_true",
                        help="Also print the raw (uncleaned) reply to stderr.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    host = args.host or os.environ.get("SCOPE_HOST")
    if not host:
        print("No scope host. Pass --host <ip> or set SCOPE_HOST.", file=sys.stderr)
        return 2

    try:
        scope = SocketScope(host, args.port)
    except OSError as exc:
        print(f"Could not connect to {host}:{args.port} - {exc}", file=sys.stderr)
        print("Is the scope's Socket Server ON (Protocol = Terminal) on that port?",
              file=sys.stderr)
        return 1

    try:
        if args.query:
            print(scope.query(args.query, debug=args.debug))
            return 0
        if args.configure:
            setup = DEFAULT_SETUP
            setup.channel = f"CH{args.channel}"
            print("IDN:", scope.query("*IDN?"))
            applied = configure(scope, setup)
            print(f"Applied {len(applied)} settings to {setup.channel}. Reading them back:\n")
            return 0 if report(verify(scope, applied)) else 1
        if args.capture:
            return capture(scope, args.channel, args.points,
                           save=args.save, plot=args.plot, plot_png=args.plot_png)
        # default action is identify
        print("IDN:", scope.query("*IDN?", debug=args.debug))
        return 0
    finally:
        scope.close()


if __name__ == "__main__":
    sys.exit(main())
