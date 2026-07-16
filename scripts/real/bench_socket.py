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
import math
import os
import re
import socket
import struct
import sys
import time
import zlib
from dataclasses import dataclass, field
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
class ChannelSetup:
    """Vertical settings — these are PER CHANNEL, so each channel can differ."""
    scale: float | None = None        # volts/div
    offset: float | None = None       # volts
    coupling: str | None = None       # DC | AC | DCREJ
    termination: float | None = None  # input impedance in ohms: 1e6 (1 MOhm) or 50
    bandwidth: float | None = None    # input bandwidth limit in Hz, e.g. 500e6


@dataclass
class ScopeSetup:
    """A named bench setup: per-channel vertical + scope-wide horizontal/trigger."""
    name: str = "custom"
    # Per-channel vertical settings, keyed by channel number.
    channels: dict[int, ChannelSetup] = field(default_factory=dict)
    # Used for any channel you ask for that isn't listed in `channels`.
    default_channel: ChannelSetup = field(default_factory=ChannelSetup)
    # Horizontal — GLOBAL to the scope, sent once.
    # MANual lets you set sample rate and record length independently (this is the
    # "Manual" shown in the scope's Acquisition badge). AUTO derives them for you.
    horizontal_mode: str | None = None       # AUTO | MANual
    sample_rate: float | None = None         # samples/s
    horizontal_scale: float | None = None    # seconds/div
    record_length: int | None = None
    # Trigger position on the horizontal axis: percent of the record kept BEFORE the
    # trigger (Tek default 10%). 10 => trigger sits 10% in from the left edge.
    horizontal_position: float | None = None
    # Acquisition — GLOBAL. SAMple is the plain "Sample" mode in the Acquisition badge.
    acquire_mode: str | None = None          # SAMple | PEAKdetect | HIRes | AVErage | ENVelope
    # Trigger — GLOBAL to the scope, sent once.
    # MODE matters a lot for single-shot capture:
    #   AUTO   - if no trigger arrives, the scope triggers ANYWAY after a timeout, so an
    #            acquisition can complete WITHOUT your event ever happening.
    #   NORMal - the scope waits indefinitely for the real trigger condition. This is
    #            what you want when arming with --single to catch a specific event.
    trigger_mode: str | None = None          # AUTO | NORMal
    trigger_source: str | None = None
    trigger_level: float | None = None       # volts
    trigger_slope: str | None = None         # RISE | FALL


# ---------------------------------------------------------------------------
# Named setups. Pick one with  --setup <name>  ;  list them with --list-setups.
# Add your own here — it's just a dict entry.
# ---------------------------------------------------------------------------
SETUPS: dict[str, ScopeSetup] = {
    # Generic fast-timebase default (what the script shipped with).
    "default": ScopeSetup(
        name="default",
        channels={1: ChannelSetup(scale=0.5, offset=0.0, coupling="DC")},
        default_channel=ChannelSetup(scale=0.5, offset=0.0, coupling="DC"),
        sample_rate=1.25e9,
        record_length=100_000,
        horizontal_position=10.0,     # trigger 10% in from the left (Tek default)
        trigger_source="CH1",
        trigger_level=1.0,
        trigger_slope="RISE",
    ),

    # Matches the bench screenshot: CH1+CH2 at 5 V/div, 4 s/div, 250 S/s,
    # 10 kpts, trigger on CH1 @ 6.8 V.
    # Note these three are self-consistent: 250 S/s * 4 s/div * 10 div = 10,000 pts.
    "bench": ScopeSetup(
        name="bench",
        channels={
            1: ChannelSetup(scale=5.0, offset=0.0, coupling="DC"),
            2: ChannelSetup(scale=5.0, offset=0.0, coupling="DC"),
        },
        default_channel=ChannelSetup(scale=5.0, offset=0.0, coupling="DC"),
        sample_rate=250,
        horizontal_scale=4.0,
        record_length=10_000,
        horizontal_position=10.0,     # the "10%" shown in the Horizontal panel
        trigger_source="CH1",
        trigger_level=6.8,
        trigger_slope="FALL",   # flip to "RISE" if your trigger is rising-edge
    ),

    # ---------------------------------------------------------------------
    # "bench_full" — every setting readable from the scope's front-panel
    # badges, transcribed exactly:
    #
    #   Ch 1 / Ch 2   5 V/div, 1 MOhm, 500 MHz
    #   Horizontal    4 s/div (40 s total), SR 250 S/s, 4 ms/pt,
    #                 RL 10 kpts, position 10%
    #   Trigger       CH1, falling edge, 6.8 V
    #   Acquisition   Manual, Sample
    #
    # Cross-check: 250 S/s * 4 s/div * 10 div = 10,000 pts, and 1/250 = 4 ms/pt.
    # Both agree with the badges, so the numbers are self-consistent.
    #
    # Not set here (not shown in the badges / no reliable SCPI):
    #   - vertical offset (not on the badge, so we leave it alone)

    # python bench_socket.py --host 169.254.8.134 --capture --single --channels 1,2 --points 10000 --save wave.csv --plot-png wave.png --timeout 180

    #   - "12 bits" ADC resolution and "1 Acqs" (informational)
    # ---------------------------------------------------------------------
    "bench_full": ScopeSetup(
        name="bench_full",
        channels={
            1: ChannelSetup(scale=5.0, coupling="DC", termination=1e6, bandwidth=500e6),
            2: ChannelSetup(scale=5.0, coupling="DC", termination=1e6, bandwidth=500e6),
        },
        default_channel=ChannelSetup(scale=5.0, coupling="DC",
                                     termination=1e6, bandwidth=500e6),
        horizontal_mode="MANual",     # the "Manual" in the Acquisition badge
        sample_rate=250,              # SR: 250 S/s   (=> 4 ms/pt)
        horizontal_scale=4.0,         # 4 s/div       (=> 40 s across the screen)
        record_length=10_000,         # RL: 10 kpts
        horizontal_position=10.0,     # 10%
        acquire_mode="SAMple",        # "Sample" in the Acquisition badge
        # NORMal, not AUTO: in AUTO the scope force-triggers after a timeout, so a
        # --single acquisition can complete without your event ever occurring.
        trigger_mode="NORMal",
        trigger_source="CH1",
        trigger_level=6.8,
        trigger_slope="RISE",         # the falling-edge icon on the Trigger badge
    ),
}

DEFAULT_SETUP = SETUPS["default"]   # kept for backwards compatibility


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


def configure(scope: SocketScope, setup: ScopeSetup,
              channels: list[int] | None = None,
              duration: float | None = None) -> list[Setting]:
    """Apply vertical/horizontal/trigger settings; return what was applied.

    Vertical settings (scale/offset/coupling) are PER-CHANNEL, so they're applied to
    every channel in `channels`. Horizontal and trigger settings are GLOBAL to the
    scope, so they're sent once regardless of how many channels are listed.

    duration : total seconds across the screen. If given, it OVERRIDES the setup's
        timebase: we hold the setup's sample rate fixed and derive both the s/div and
        the record length from it, so the user thinks in one intuitive number
        (seconds) instead of s/div + record length. This is the scope's own Manual-
        mode relationship: record_length = sample_rate * duration, s/div = duration/10.
        The named setup itself is left untouched (we override locally, not in place).
    """
    if channels is None:
        channels = sorted(setup.channels) or [1]
    settings: list[Setting] = []

    # Effective timebase. Default to the setup's values; a duration override recomputes
    # s/div and record length from it, keeping the setup's sample rate as the sampling
    # resolution (more seconds -> more points, same points-per-second).
    horizontal_scale = setup.horizontal_scale
    record_length = setup.record_length
    if duration is not None and duration > 0:
        horizontal_scale = duration / 10.0                 # 10 divisions across the screen
        if setup.sample_rate:
            record_length = max(1, round(setup.sample_rate * duration))

    def apply(base: str, value: Any) -> None:
        scope.write(f"{base} {value}")
        settings.append(Setting(base, value, f"{base}?"))

    # Vertical — per channel, each using ITS OWN ChannelSetup.
    for n in channels:
        cs = setup.channels.get(n, setup.default_channel)
        ch = f"CH{n}"
        scope.write(f"SELect:{ch} ON")
        if cs.scale is not None:
            apply(f"{ch}:SCAle", cs.scale)
        if cs.offset is not None:
            apply(f"{ch}:OFFSet", cs.offset)
        if cs.coupling:
            apply(f"{ch}:COUPling", cs.coupling)
        if cs.termination:
            apply(f"{ch}:TERmination", cs.termination)      # 1e6 = 1 MOhm, 50 = 50 Ohm
        if cs.bandwidth:
            apply(f"{ch}:BANdwidth", cs.bandwidth)          # e.g. 500e6 = 500 MHz

    # Horizontal — global, sent once.
    # MODE first: MANual must be set before sample rate / record length will stick.
    if setup.horizontal_mode:
        apply("HORizontal:MODE", setup.horizontal_mode)
    if setup.sample_rate:
        apply("HORizontal:SAMPLERate", setup.sample_rate)
    if horizontal_scale:
        apply("HORizontal:SCAle", horizontal_scale)
    if record_length:
        apply("HORizontal:RECOrdlength", record_length)
    if setup.horizontal_position is not None:
        # Where the trigger sits horizontally, as a % of the record before it.
        apply("HORizontal:POSition", setup.horizontal_position)

    # Acquisition — global, sent once.
    if setup.acquire_mode:
        apply("ACQuire:MODe", setup.acquire_mode)

    # Trigger (edge) — global, sent once.
    if setup.trigger_mode:
        # NORMal = wait for the real trigger. AUTO = fire anyway after a timeout,
        # which lets an acquisition complete without your event ever happening.
        apply("TRIGger:A:MODe", setup.trigger_mode)
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


# --- CSV formatting -------------------------------------------------------
# One place that decides how numbers look, so every CSV (per-channel and joint)
# is formatted identically and the columns line up.

def _time_decimals(dt: float) -> int:
    """How many decimal places to print for the time column.

    Derived from the sample step dt so consecutive timestamps are distinguishable
    AND every row uses the SAME number of decimals (an aligned, monotonic column
    that never flips into scientific notation). ~3 extra digits below dt.
    """
    if not dt or dt <= 0:
        return 9
    return min(15, max(3, int(math.ceil(-math.log10(abs(dt)))) + 3))


def _fmt_time(t: float, decimals: int) -> str:
    return f"{t:.{decimals}f}"


def _fmt_volts(v: float) -> str:
    return f"{v:.6g}"


def save_csv(wf: Waveform, path: str) -> None:
    """Write one scaled waveform as CSV, columns: index, time_s, volts.

    Rows are in time order; the time column uses a fixed decimal count (from dt) so
    it stays aligned and readable in a spreadsheet.
    """
    import csv
    dec = _time_decimals(wf.dt)
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["index", "time_s", "volts"])
        for i, (t, v) in enumerate(zip(wf.t, wf.v)):
            writer.writerow([i, _fmt_time(t, dec), _fmt_volts(v)])
    print(f"saved {len(wf.v)} samples to {path}")


def ascii_plot(wf: Waveform, width: int = 70, height: int = 21) -> None:

    
    v, n = wf.v, len(wf.v)
    vmin, vmax = _v_range(v)              # pads a flat signal so it sits mid-plot
    span = vmax - vmin
    grid = [[" "] * width for _ in range(height)]

    def row_of(val: float) -> int:
        return max(0, min(height - 1, round((vmax - val) / span * (height - 1))))

    # zero line first, so samples draw over it
    if vmin <= 0 <= vmax:
        grid[row_of(0.0)] = ["-"] * width

    for col in range(width):
        lo = col * n // width
        hi = max(lo + 1, (col + 1) * n // width)
        seg = v[lo:hi]
        top, bot = row_of(max(seg)), row_of(min(seg))   # min..max of this column's samples
        for r in range(top, bot + 1):
            grid[r][col] = "*"

    # ASCII only (the Windows console can't render box-drawing characters).
    print(f"  {vmax:+.3g} V +" + "-" * width)
    for r in grid:
        print("           |" + "".join(r))
    print(f"  {vmin:+.3g} V +" + "-" * width)
    print(f"             {wf.t[0]:g} s{' ' * max(1, width - 14)}{wf.t[-1]:g} s")


# ===========================================================================
# PNG output.
#
# matplotlib is OPTIONAL. If it is installed we use it (nicer axes/fonts). If it
# is not, we fall back to a built-in renderer that writes a real PNG using only
# the standard library (zlib + struct). That keeps this script's promise: it runs
# with ZERO pip installs, which matters on a locked-down TestStand machine.
# ===========================================================================

# A tiny 5x7 bitmap font - just the characters the axis labels and legend need.
_FONT: dict[str, list[str]] = {
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": [".###.", "#...#", "....#", "..##.", "....#", "#...#", ".###."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": ["..##.", ".#...", "#....", "####.", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "...#.", ".##.."],
    ".": [".....", ".....", ".....", ".....", ".....", ".##..", ".##.."],
    "-": [".....", ".....", ".....", "#####", ".....", ".....", "....."],
    "+": [".....", "..#..", "..#..", "#####", "..#..", "..#..", "....."],
    "e": [".....", ".....", ".###.", "#...#", "#####", "#....", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "s": [".....", ".....", ".####", "#....", ".###.", "....#", "####."],
    "C": [".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    ":": [".....", ".##..", ".##..", ".....", ".##..", ".##..", "....."],
    " ": [".....", ".....", ".....", ".....", ".....", ".....", "....."],
}

# One colour per channel, in channel order.
_TRACE_COLORS = [(198, 156, 0), (0, 150, 200), (200, 60, 60), (60, 160, 60)]


def _v_range(values: list[float]) -> tuple[float, float]:
    """The voltage range to plot over.

    A perfectly FLAT signal (e.g. a zero waveform) has vmin == vmax, which would
    otherwise put the trace exactly on the plot border and make it invisible - or
    divide by zero. Pad it symmetrically so a flat line is drawn down the MIDDLE.
    """
    vmin, vmax = min(values), max(values)
    if vmax == vmin:
        pad = abs(vmax) * 0.1 or 1.0        # 10% of the level, or +/-1 V at exactly 0
        vmin, vmax = vmin - pad, vmax + pad
    return vmin, vmax


class _Canvas:
    """A tiny RGB pixel canvas that can write itself out as a real PNG."""

    def __init__(self, w: int, h: int, bg: tuple[int, int, int] = (255, 255, 255)) -> None:
        self.w, self.h = w, h
        self.buf = bytearray(bytes(bg) * (w * h))

    def px(self, x: int, y: int, c: tuple[int, int, int]) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i:i + 3] = bytes(c)

    def hline(self, x0: int, x1: int, y: int, c) -> None:
        for x in range(min(x0, x1), max(x0, x1) + 1):
            self.px(x, y, c)

    def vline(self, x: int, y0: int, y1: int, c) -> None:
        for y in range(min(y0, y1), max(y0, y1) + 1):
            self.px(x, y, c)

    def line(self, x0: int, y0: int, x1: int, y1: int, c) -> None:
        """Bresenham - used when there are fewer samples than pixel columns."""
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        while True:
            self.px(x0, y0, c)
            if x0 == x1 and y0 == y1:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def rect(self, x0: int, y0: int, x1: int, y1: int, c) -> None:
        self.hline(x0, x1, y0, c)
        self.hline(x0, x1, y1, c)
        self.vline(x0, y0, y1, c)
        self.vline(x1, y0, y1, c)

    def text(self, x: int, y: int, s: str, c, scale: int = 2) -> None:
        cx = x
        for ch in s:
            glyph = _FONT.get(ch, _FONT[" "])
            for ry, row in enumerate(glyph):
                for rx, bit in enumerate(row):
                    if bit == "#":
                        for sy in range(scale):
                            for sx in range(scale):
                                self.px(cx + rx * scale + sx, y + ry * scale + sy, c)
            cx += 6 * scale
        return

    def save(self, path: str) -> None:
        """Encode as an 8-bit RGB PNG. Pure stdlib: zlib + struct."""
        raw = bytearray()
        for y in range(self.h):
            raw.append(0)                       # per-scanline filter type 0 (None)
            i = y * self.w * 3
            raw += self.buf[i:i + self.w * 3]

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0)  # 8-bit truecolour
        png = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", ihdr)
               + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
               + chunk(b"IEND", b""))
        with open(path, "wb") as fh:
            fh.write(png)


def _render_png(waves: dict[int, Waveform], path: str) -> None:
    """Draw the waveform(s) and write a PNG - no third-party libraries at all."""
    W, H = 960, 420
    L, R, T, B = 84, 22, 34, 48           # margins
    pw, ph = W - L - R, H - T - B

    GRID, AXIS, TXT, ZERO = (228, 228, 228), (90, 90, 90), (40, 40, 40), (175, 175, 175)
    cv = _Canvas(W, H)

    chans = sorted(waves)
    all_v = [val for c in chans for val in waves[c].v]
    vmin, vmax = _v_range(all_v)          # pads a flat signal so it sits mid-plot
    span = vmax - vmin
    t = waves[chans[0]].t

    def Y(v: float) -> int:
        return T + int((vmax - v) / span * (ph - 1))

    # grid, zero line, axes box
    for k in range(1, 10):
        cv.vline(L + k * pw // 10, T, T + ph, GRID)
    for k in range(1, 8):
        cv.hline(L, L + pw, T + k * ph // 8, GRID)
    if vmin <= 0 <= vmax:
        cv.hline(L, L + pw, Y(0.0), ZERO)
    cv.rect(L, T, L + pw, T + ph, AXIS)

    # traces
    for idx, c in enumerate(chans):
        col = _TRACE_COLORS[idx % len(_TRACE_COLORS)]
        v, n = waves[c].v, len(waves[c].v)
        if n >= pw:
            # more samples than pixels: draw each column's min..max so peaks survive
            for xcol in range(pw):
                lo = xcol * n // pw
                hi = max(lo + 1, (xcol + 1) * n // pw)
                seg = v[lo:hi]
                cv.vline(L + xcol, Y(max(seg)), Y(min(seg)), col)
        else:
            # fewer samples than pixels: join them up so the trace stays continuous
            for i in range(n - 1):
                x0 = L + i * (pw - 1) // max(1, n - 1)
                x1 = L + (i + 1) * (pw - 1) // max(1, n - 1)
                cv.line(x0, Y(v[i]), x1, Y(v[i + 1]), col)

    # labels
    cv.text(4, T - 4, f"{vmax:.3g} V", TXT)
    cv.text(4, T + ph - 10, f"{vmin:.3g} V", TXT)
    cv.text(L, T + ph + 12, f"{t[0]:.3g} s", TXT)
    cv.text(L + pw - 90, T + ph + 12, f"{t[-1]:.3g} s", TXT)

    # legend, in each trace's colour
    lx = L + 8
    for idx, c in enumerate(chans):
        name = waves[c].channel
        cv.text(lx, 8, name, _TRACE_COLORS[idx % len(_TRACE_COLORS)])
        lx += 6 * 2 * (len(name) + 1)

    cv.save(path)


def _write_png(waves: dict[int, Waveform], path: str) -> bool:
    """Write a PNG of one or more waveforms. Always succeeds - matplotlib optional."""
    chans = sorted(waves)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        _render_png(waves, path)          # built-in, zero dependencies
        print(f"saved plot to {path}  (built-in renderer; "
              f"'pip install matplotlib' for a nicer one)")
        return True

    plt.figure(figsize=(10, 4.5))
    for c in chans:
        plt.plot(waves[c].t, waves[c].v, linewidth=0.8, label=waves[c].channel)
    plt.xlabel("time (s)")
    plt.ylabel("volts")
    plt.title("Capture: " + ", ".join(waves[c].channel for c in chans))
    if len(chans) > 1:
        plt.legend()
    plt.grid(True, alpha=0.3)
    plt.savefig(path, dpi=110, bbox_inches="tight")
    plt.close()
    print(f"saved plot to {path}")
    return True


def save_png(wf: Waveform, path: str) -> bool:
    """Save a PNG plot of one waveform. Works with or without matplotlib."""
    return _write_png({_channel_number(wf.channel): wf}, path)


# ---------------------------------------------------------------------------
# Multi-channel: capture several channels, then emit BOTH per-channel outputs
# and a joint (all-channels-together) output.
# ---------------------------------------------------------------------------
def _derive(path: str, suffix: str) -> str:
    """'wave.csv' + 'CH1' -> 'wave_CH1.csv'."""
    root, ext = os.path.splitext(path)
    return f"{root}_{suffix}{ext}"


def acquire_many(scope: SocketScope, channels: list[int],
                 points: int = 1000) -> dict[int, Waveform]:
    """Capture each channel in turn. Channels with no data are skipped (with a note)."""
    waves: dict[int, Waveform] = {}
    for ch in channels:
        wf = acquire(scope, ch, points)
        if wf is None:
            print(f"CH{ch}: no curve data - is the channel displayed and acquiring?",
                  file=sys.stderr)
            continue
        waves[ch] = wf
    return waves


def save_joint_csv(waves: dict[int, Waveform], path: str) -> None:
    """One CSV holding every channel against a shared time column.

    Channels are laid out left-to-right in ascending order (CH1, CH2, ...); the time
    column is formatted the same way as the per-channel files so everything aligns.
    """
    import csv
    chans = sorted(waves)
    n = min(len(waves[c].v) for c in chans)
    t = waves[chans[0]].t
    dec = _time_decimals(waves[chans[0]].dt)
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["index", "time_s"] + [f"{waves[c].channel}_volts" for c in chans])
        for i in range(n):
            writer.writerow([i, _fmt_time(t[i], dec)] + [_fmt_volts(waves[c].v[i]) for c in chans])
    print(f"saved joint CSV ({len(chans)} channels x {n} samples) to {path}")


def ascii_plot_joint(waves: dict[int, Waveform], width: int = 70, height: int = 21) -> None:
    """Overlay every channel on one ASCII plot, sharing a single voltage axis."""
    chans = sorted(waves)
    symbols = ["*", "+", "o", "x"]
    marks = {c: symbols[i % len(symbols)] for i, c in enumerate(chans)}

    all_v = [val for c in chans for val in waves[c].v]
    vmin, vmax = _v_range(all_v)          # pads a flat signal so it sits mid-plot
    span = vmax - vmin
    grid = [[" "] * width for _ in range(height)]

    def row_of(val: float) -> int:
        return max(0, min(height - 1, round((vmax - val) / span * (height - 1))))

    if vmin <= 0 <= vmax:
        grid[row_of(0.0)] = ["-"] * width

    for c in chans:
        v, n, mark = waves[c].v, len(waves[c].v), marks[c]
        for col in range(width):
            lo = col * n // width
            hi = max(lo + 1, (col + 1) * n // width)
            seg = v[lo:hi]
            for r in range(row_of(max(seg)), row_of(min(seg)) + 1):
                cur = grid[r][col]
                # '#' marks where channels overlap
                grid[r][col] = mark if cur in (" ", "-", mark) else "#"

    t = waves[chans[0]].t
    legend = "  ".join(f"{marks[c]}={waves[c].channel}" for c in chans) + "  #=overlap"
    print(f"  {vmax:+.3g} V +" + "-" * width)
    for r in grid:
        print("           |" + "".join(r))
    print(f"  {vmin:+.3g} V +" + "-" * width)
    print(f"             {t[0]:g} s{' ' * max(1, width - 14)}{t[-1]:g} s")
    print(f"             {legend}")


def save_png_joint(waves: dict[int, Waveform], path: str) -> bool:
    """One PNG with every channel overlaid on a shared voltage axis, with a legend.

    Works with or without matplotlib (falls back to the built-in renderer).
    """
    return _write_png(waves, path)


# ---------------------------------------------------------------------------
# Acquisition control.
#
# --capture only READS whatever record is already in the scope's memory. That is
# fine if the scope is Stopped (the record is frozen and stays there indefinitely),
# but if the scope is Running the record gets overwritten by each new trigger.
#
# is_running()  -> lets us warn about that.
# arm_single()  -> makes it deterministic: arm ONE acquisition, wait for the
#                  trigger and the record to fill, and only then read.
# ---------------------------------------------------------------------------
def is_running(scope: SocketScope) -> bool:
    """True if the scope is currently acquiring (so its record can change under us)."""
    return scope.query("ACQuire:STATE?").strip().upper() in ("1", "ON", "RUN")


def free_run(scope: SocketScope, timeout: float = 60.0, poll: float = 0.5) -> bool:
    """Put the scope in continuous AUTO acquisition and wait for ONE fresh record.

    Use this when you just want whatever the scope is showing - including a flat or
    zero signal that would never satisfy a real trigger. AUTO makes the scope
    self-trigger, so a record always fills.

    We then STOP the scope, so the record is frozen while we read it back (our read
    takes several queries, and a running scope would overwrite it mid-read).
    """
    scope.write("TRIGger:A:MODe AUTO")          # self-trigger; don't wait for an edge
    scope.write("ACQuire:STOPAfter RUNSTop")    # continuous, not single-sequence
    scope.write("ACQuire:STATE RUN")

    def num_acq() -> int:
        try:
            return int(float(scope.query("ACQuire:NUMACq?") or 0))
        except ValueError:
            return 0

    start_n = num_acq()
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        time.sleep(poll)
        if num_acq() > start_n:                 # a NEW record has completed
            scope.write("ACQuire:STATE STOP")   # freeze it so the read is consistent
            print(f"  free-run: got a fresh record after "
                  f"{time.monotonic() - start:.1f} s")
            return True
    return False


def arm_acquisition(scope: SocketScope) -> bool:
    """Arm ONE acquisition and return IMMEDIATELY (does not wait for the trigger).

    Use this to catch an event that happens AFTER you arm: the scope waits for the
    trigger, captures a single record, and STOPS (freezes) it. The armed state lives
    on the scope itself, so the event is captured even if nothing is polling - you can
    read the frozen record later with a plain capture (single=False). Returns True
    once the arm command is sent.
    """
    # In AUTO mode the scope force-triggers after a timeout, so the record can fill
    # WITHOUT your event ever happening. Warn loudly - this is a trap.
    mode = scope.query("TRIGger:A:MODe?").strip().upper()
    if mode.startswith("AUTO"):
        print("WARNING: trigger mode is AUTO. The scope will trigger by itself after a\n"
              "         timeout, so the record may fill WITHOUT your event.\n"
              "         Use a setup with trigger_mode='NORMal' (e.g. --setup bench_full),\n"
              "         or send: --query \"TRIGger:A:MODe NORMal\"", file=sys.stderr)

    scope.write("ACQuire:STOPAfter SEQuence")   # one shot, do not free-run
    scope.write("ACQuire:STATE RUN")            # arm it; returns without waiting
    return True


def arm_single(scope: SocketScope, timeout: float = 120.0, poll: float = 0.5) -> bool:
    """Arm exactly ONE acquisition and BLOCK until it completes.

    Same arm as arm_acquisition(), then polls ACQuire:STATE? until the scope reports
    stopped. Returns False if it never completed within `timeout` (the trigger never
    fired). Only use this when the event occurs DURING this call; if the event happens
    later (e.g. a separate step generates it), use arm_acquisition() before it instead.
    """
    arm_acquisition(scope)
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if not is_running(scope):
            print(f"  acquisition complete after {time.monotonic() - start:.1f} s")
            return True
        time.sleep(poll)    # explicit poll interval - do not spin on the scope
    return False


def capture(scope: SocketScope, channels: list[int], points: int = 1000, *,
            save: str | None = None, plot: bool = False,
            plot_png: str | None = None, single: bool = False,
            free: bool = False, timeout: float = 120.0) -> int:
    if free:
        print(f"Free-run: AUTO trigger + continuous acquisition; waiting up to "
              f"{timeout:g} s for one fresh record...")
        if not free_run(scope, timeout):
            print(f"no fresh record within {timeout:g} s. Is the scope able to acquire? "
                  f"(a slow timebase needs a long record - e.g. 250 S/s x 10 kpts = 40 s)",
                  file=sys.stderr)
            return 1
    elif single:
        print(f"Arming a single acquisition; waiting up to {timeout:g} s for the "
              f"trigger and the record to fill...")
        if not arm_single(scope, timeout):
            print(f"acquisition did not complete within {timeout:g} s - did the trigger "
                  f"ever fire?", file=sys.stderr)
            return 1
    elif is_running(scope):
        print("WARNING: the scope is RUNNING, so the record can be overwritten while we "
              "read it.\n         Stop the scope, or use --single for a deterministic "
              "capture.", file=sys.stderr)

    waves = acquire_many(scope, channels, points)
    if not waves:
        print("no curve data from any channel (is a signal being acquired?)",
              file=sys.stderr)
        return 1

    multi = len(waves) > 1

    # --- per-channel outputs ---
    for c in sorted(waves):
        wf = waves[c]
        print(f"--- {wf.channel} ---")
        summarize(wf)
        if save:
            save_csv(wf, _derive(save, wf.channel) if multi else save)
        if plot:
            ascii_plot(wf)
        if plot_png:
            save_png(wf, _derive(plot_png, wf.channel) if multi else plot_png)
        print()

    # --- joint outputs (only meaningful with 2+ channels) ---
    if multi:
        print("--- JOINT (all channels) ---")
        if save:
            save_joint_csv(waves, _derive(save, "joint"))
        if plot:
            ascii_plot_joint(waves)
        if plot_png:
            save_png_joint(waves, _derive(plot_png, "joint"))
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
                        help="Apply a setup (see --setup) and print a read-back PASS/FAIL table.")
    parser.add_argument("--setup", default="default", metavar="NAME",
                        help="Which named setup --configure applies. Default: 'default'. "
                             "See --list-setups.")
    parser.add_argument("--list-setups", action="store_true",
                        help="Print the available named setups and exit.")
    parser.add_argument("--capture", action="store_true",
                        help="Pull an ASCII waveform off a channel and summarise it.")
    parser.add_argument("--channel", type=int, default=None,
                        help="Single channel for --configure/--capture. If omitted, "
                             "--configure uses the setup's own channels and --capture uses CH1.")
    parser.add_argument("--channels", default=None, metavar="LIST",
                        help="Comma-separated channels for --capture, e.g. 1,2. Each is "
                             "captured separately AND combined into joint outputs.")
    parser.add_argument("--points", type=int, default=1000,
                        help="Number of samples to transfer for --capture. Default: 1000.")
    parser.add_argument("--duration", type=float, default=None, metavar="SECONDS",
                        help="With --configure: total seconds the capture should span. "
                             "Overrides the setup's timebase - holds its sample rate and "
                             "recomputes s/div and record length from the duration.")
    parser.add_argument("--single", action="store_true",
                        help="With --capture: arm ONE acquisition and wait for it to "
                             "complete before reading. Use this to deterministically "
                             "capture the NEXT event instead of whatever is in memory.")
    parser.add_argument("--free-run", action="store_true",
                        help="With --capture: put the scope in AUTO + continuous "
                             "acquisition, wait for one fresh record, then read it. "
                             "Use this to grab whatever is on screen - including a flat "
                             "or zero signal that would never fire a real trigger.")
    parser.add_argument("--timeout", type=float, default=120.0, metavar="SEC",
                        help="With --single: how long to wait for the acquisition to "
                             "complete. Default: 120 seconds.")
    parser.add_argument("--save", metavar="CSV", default=None,
                        help="With --capture: save to CSV. Multi-channel writes one file per "
                             "channel (wave_CH1.csv, ...) plus a joint wave_joint.csv.")
    parser.add_argument("--plot", action="store_true",
                        help="With --capture: ASCII plot in the terminal (no libraries). "
                             "Multi-channel also draws a joint overlay plot.")
    parser.add_argument("--plot-png", metavar="PNG", default=None,
                        help="With --capture: save a PNG. Uses matplotlib if installed, "
                             "otherwise a built-in renderer (no packages needed). "
                             "Multi-channel writes one PNG per channel plus a joint one.")
    parser.add_argument("--query", metavar="SCPI", default=None,
                        help="Send an arbitrary SCPI query and print the reply.")
    parser.add_argument("--send", metavar="SCPI", default=None,
                        help="Send an arbitrary SCPI COMMAND (no reply expected), e.g. "
                             "--send \"TRIGger:A:MODe AUTO\".")
    parser.add_argument("--debug", action="store_true",
                        help="Also print the raw (uncleaned) reply to stderr.")
    return parser.parse_args(argv)


def _print_setups() -> None:
    def chan_desc(cs: ChannelSetup) -> str:
        bits = [f"{cs.scale} V/div"]
        if cs.coupling:
            bits.append(cs.coupling)
        if cs.termination:
            bits.append("1 MOhm" if cs.termination >= 1e6 else f"{cs.termination:g} Ohm")
        if cs.bandwidth:
            bits.append(f"{cs.bandwidth / 1e6:g} MHz")
        return "/".join(bits)

    for name, s in SETUPS.items():
        print(f"  {name}")
        for n, cs in sorted(s.channels.items()):
            print(f"      CH{n}      : {chan_desc(cs)}")
        print(f"      horizontal: {s.horizontal_scale} s/div, {s.sample_rate} S/s, "
              f"{s.record_length} pts, pos {s.horizontal_position}%"
              + (f", mode {s.horizontal_mode}" if s.horizontal_mode else ""))
        print(f"      trigger  : {s.trigger_source} @ {s.trigger_level} V {s.trigger_slope}")
        if s.acquire_mode:
            print(f"      acquire  : {s.acquire_mode}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.list_setups:
        print("Available setups (use --setup NAME):")
        _print_setups()
        return 0

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

    # --channels (e.g. "1,2") wins; then a single --channel; else CH1 for capture.
    if args.channels:
        channels = [int(c) for c in args.channels.split(",") if c.strip()]
    elif args.channel is not None:
        channels = [args.channel]
    else:
        channels = [1]

    try:
        if args.send:
            scope.write(args.send)
            print(f"sent: {args.send}")
            return 0
        if args.query:
            print(scope.query(args.query, debug=args.debug))
            return 0
        if args.configure:
            setup = SETUPS.get(args.setup)
            if setup is None:
                print(f"Unknown setup {args.setup!r}. Available: "
                      f"{', '.join(SETUPS)}", file=sys.stderr)
                return 2
            # With no --channel/--channels, configure exactly the channels the setup defines.
            cfg_channels = channels if (args.channels or args.channel is not None) \
                else (sorted(setup.channels) or [1])
            print("IDN:", scope.query("*IDN?"))
            applied = configure(scope, setup, cfg_channels, duration=args.duration)
            names = ", ".join(f"CH{c}" for c in cfg_channels)
            print(f"Applied {len(applied)} settings from setup '{setup.name}' to {names}. "
                  f"Reading them back:\n")
            return 0 if report(verify(scope, applied)) else 1
        if args.capture:
            return capture(scope, channels, args.points,
                           save=args.save, plot=args.plot, plot_png=args.plot_png,
                           single=args.single, free=args.free_run,
                           timeout=args.timeout)
        # default action is identify
        print("IDN:", scope.query("*IDN?", debug=args.debug))
        return 0
    finally:
        scope.close()


if __name__ == "__main__":
    sys.exit(main())
