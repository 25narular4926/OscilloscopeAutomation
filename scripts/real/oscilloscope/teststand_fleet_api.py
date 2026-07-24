#!/usr/bin/env python3
"""TestStand-facing API for driving MULTIPLE oscilloscopes at once (by alias).

The multi-scope twin of teststand_api.py. Where teststand_api holds ONE session, this
holds several, each identified by an alias string. Every function takes that alias as its
first argument, so a TestStand sequence can loop over scopes or address them by name.

Every function takes and returns ONLY primitive types (str / int / float / bool / list),
so TestStand can bind them straight to sequence variables.

You choose the port. connect_scope() and connect_discovered() both take an explicit port
(default 4000) - discovery finds the IPs, but it connects only on the port you give.

Typical use:
    aliases = connect_discovered(4000)          # ["MSO44_134", "MSO44_135", ...]
    for a in aliases:
        configure(a, "bench_full", "1,2")
        capture(a, "1,2")
        vmax = get_vmax(a, 1)                    # <- limits per scope
        save_png(a, "C:\\\\results\\\\" + a + ".png")
    disconnect_all()
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bench_socket as bs   # noqa: E402
import discovery            # noqa: E402

# ---------------------------------------------------------------------------
# Session state - keyed by alias.
# ---------------------------------------------------------------------------
_scopes: dict[str, bs.SocketScope] = {}
_waves: dict[str, dict[int, bs.Waveform]] = {}
_reports: dict[str, str] = {}
_record_length: dict[str, int] = {}
_last_found: list[dict] = []       # cache of the last scan, so connect_matching can reuse it


def _serial(idn: str) -> str:
    """'TEKTRONIX,MSO44,C012345,FV:2.0' -> 'C012345' (the serial number field)."""
    parts = [p.strip() for p in idn.split(",")]
    return parts[2] if len(parts) > 2 else ""


def _require_scope(alias: str) -> bs.SocketScope:
    if alias not in _scopes:
        raise RuntimeError(
            f"No scope connected as {alias!r}. Connected: {list_scopes() or 'none'}."
        )
    return _scopes[alias]


def _require_wave(alias: str, channel: int) -> bs.Waveform:
    waves = _waves.get(alias, {})
    if channel not in waves:
        raise RuntimeError(
            f"No captured data for {alias!r} CH{channel}. Call capture() first "
            f"(captured: {sorted(waves) or 'none'})."
        )
    return waves[channel]


def _parse_channels(channels: str) -> list[int]:
    return [int(c) for c in str(channels).split(",") if c.strip()]


# ---------------------------------------------------------------------------
# Connection (you choose the port).
# ---------------------------------------------------------------------------
def connect_scope(alias: str, host: str, port: int = 4000) -> str:
    """Open a session to one scope under `alias`. Returns its *IDN?.

    You pick the port (default 4000). Raises if the scope cannot be reached.
    """
    disconnect_scope(alias)                      # drop any stale session with this alias
    try:
        _scopes[alias] = bs.SocketScope(host, int(port))
    except OSError as exc:
        raise RuntimeError(
            f"Cannot reach {alias!r} at {host}:{port} - {exc}. "
            f"Is the Socket Server ON on that port?"
        ) from exc
    return _scopes[alias].query("*IDN?")


def connect_discovered(port: int = 4000, subnet: str = "", timeout: float = 0.5) -> list[str]:
    """Scan the network, *IDN?-probe every host, and open a session to each scope found.

    This is the "detect the available IPs, ping each with *IDN?, and whoever answers becomes
    a scope" flow. If several answer, several sessions are opened - one per scope.

    port    : the SCPI port to probe and connect on (you determine this).
    subnet  : "" -> auto-detect the local subnet(s). A CIDR like "192.168.1.0/24" -> scan it.
    timeout : per-host connect timeout in seconds.

    Returns the list of aliases created (model + last IP octet, e.g. "MSO44_134").
    """
    global _last_found
    _last_found = discovery.discover_scopes(port=int(port), subnet=subnet or None,
                                            timeout=timeout)
    aliases: list[str] = []
    for info in _last_found:
        alias = f"{info['model']}_{info['ip'].split('.')[-1]}"
        disconnect_scope(alias)
        _scopes[alias] = bs.SocketScope(info["ip"], int(port))
        aliases.append(alias)
    return aliases


def scan(port: int = 4000, subnet: str = "", timeout: float = 0.5) -> list[str]:
    """Discover scopes WITHOUT connecting, and return a readable line per scope so you can
    see each one's serial (to build your alias mapping). Also caches the result so a
    following connect_matching()/connect_discovered_as() reuses it instead of re-scanning.

        ['MSO44  serial=C012345  192.168.1.10:4000', 'MSO46  serial=C067890  ...']
    """
    global _last_found
    _last_found = discovery.discover_scopes(port=int(port), subnet=subnet or None,
                                            timeout=timeout)
    return [f"{i['model']}  serial={_serial(i['idn'])}  {i['ip']}:{i['port']}"
            for i in _last_found]


def connect_matching(alias: str, match: str, port: int = 4000, subnet: str = "",
                     timeout: float = 0.5) -> str:
    """Auto-discover, find the scope whose *IDN? contains `match`, and open it under YOUR
    `alias`. This is connect_scope WITHOUT typing an IP - you identify the scope by its
    serial (or model) instead. Call it once per scope you care about.

    match : any substring of the target scope's *IDN?, typically its serial number
            (printed on the instrument), e.g. "C012345".

    Reuses the last scan()/discovery if there is one, else scans now. Returns the connected
    scope's *IDN?, or "" if no discovered scope matched.
    """
    found = _last_found or discovery.discover_scopes(port=int(port), subnet=subnet or None,
                                                     timeout=timeout)
    m = str(match).upper()
    for info in found:
        if m in info["idn"].upper():
            disconnect_scope(alias)
            _scopes[alias] = bs.SocketScope(info["ip"], int(port))
            return _scopes[alias].query("*IDN?")
    return ""


def connect_discovered_as(mapping: dict, port: int = 4000, subnet: str = "",
                          timeout: float = 0.5) -> list[str]:
    """Auto-discover once, then open each matched scope under YOUR chosen alias.

    mapping : {match: alias}, where `match` is a substring of a scope's *IDN? (its serial
              or model), e.g. {"C012345": "cranking", "C067890": "ignition"}.

    Combines discovery (no IPs to type) with your own naming (so you can configure each
    differently by alias). Returns the aliases that matched and connected.
    """
    global _last_found
    _last_found = discovery.discover_scopes(port=int(port), subnet=subnet or None,
                                            timeout=timeout)
    connected: list[str] = []
    for info in _last_found:
        up = info["idn"].upper()
        for match, alias in mapping.items():
            if str(match).upper() in up:
                disconnect_scope(alias)
                _scopes[alias] = bs.SocketScope(info["ip"], int(port))
                connected.append(alias)
                break
    return connected


def disconnect_scope(alias: str) -> bool:
    """Close one scope's session (and drop its captured data). Safe if not connected."""
    sc = _scopes.pop(alias, None)
    if sc is not None:
        try:
            sc.close()
        except Exception:
            pass
    _waves.pop(alias, None)
    _reports.pop(alias, None)
    _record_length.pop(alias, None)
    return True


def disconnect_all() -> bool:
    """Close every session. The safe way to end the sequence."""
    for alias in list(_scopes):
        disconnect_scope(alias)
    return True


def list_scopes() -> list[str]:
    """Aliases of every currently-connected scope."""
    return sorted(_scopes)


def is_connected(alias: str) -> bool:
    """True if a scope is connected under this alias."""
    return alias in _scopes


def identify(alias: str) -> str:
    """The scope's *IDN? string."""
    return _require_scope(alias).query("*IDN?")


# ---------------------------------------------------------------------------
# Configure / capture (per alias).
# ---------------------------------------------------------------------------
def list_setups() -> list[str]:
    """Names of the available named setups (shared by all scopes)."""
    return list(bs.SETUPS)


def configure(alias: str, setup_name: str = "bench_full", channels: str = "",
              duration_s: float = 0.0) -> bool:
    """Apply a named setup to one scope and verify every setting read back.

    Same behaviour as the single-scope configure(), but for the scope named `alias`.
    """
    scope = _require_scope(alias)
    setup = bs.SETUPS.get(setup_name)
    if setup is None:
        raise ValueError(f"Unknown setup {setup_name!r}. Available: {', '.join(bs.SETUPS)}")

    chans = _parse_channels(channels) or sorted(setup.channels) or [1]
    dur = float(duration_s) if duration_s and duration_s > 0 else None
    applied = bs.configure(scope, setup, chans, duration=dur)
    results = bs.verify(scope, applied)

    _record_length[alias] = 0
    for s in applied:
        lab = s.label.upper()
        if "RECO" in lab or "POIN" in lab:      # Tek HORizontal:RECOrdlength / Keysight :ACQuire:POINts
            try:
                _record_length[alias] = int(float(s.expected))
            except (TypeError, ValueError):
                _record_length[alias] = 0

    passed = sum(1 for r in results if r.ok)
    lines = [f"[{alias}] setup '{setup.name}' -> " + ", ".join(f"CH{c}" for c in chans), ""]
    width = max((len(r.label) for r in results), default=0)
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        lines.append(f"[{mark}] {r.label:<{width}}  set {r.expected}  readback {r.readback}")
    lines.append("")
    lines.append(f"{passed}/{len(results)} settings verified")
    _reports[alias] = "\n".join(lines)
    return passed == len(results)


def get_config_report(alias: str) -> str:
    """The PASS/FAIL table from the last configure() on this scope."""
    return _reports.get(alias, "")


def capture(alias: str, channels: str = "1", points: int = 0, single: bool = False,
            timeout_s: float = 120.0) -> bool:
    """Capture one scope's channels into memory for its get_*/save_* calls.

    Same behaviour and rules as the single-scope capture() (single=False reads the record
    already on that scope; points=0 uses the record length that scope's configure set).
    """
    scope = _require_scope(alias)
    chans = _parse_channels(channels) or [1]
    n_points = int(points) if int(points) > 0 else (_record_length.get(alias, 0) or 1000)

    if single:
        if not bs.arm_single(scope, float(timeout_s)):
            raise TimeoutError(
                f"[{alias}] no trigger within {timeout_s:g} s. Check the trigger, and that "
                f"trigger mode is NORMal."
            )

    _waves[alias] = bs.acquire_many(scope, chans, n_points)
    return len(_waves[alias]) == len(chans)


def captured_channels(alias: str) -> list[int]:
    """Which channels returned data on this scope's last capture()."""
    return sorted(_waves.get(alias, {}))


# ---------------------------------------------------------------------------
# Measurements (per alias + channel). Same math as the single-scope API.
# ---------------------------------------------------------------------------
def get_vmax(alias: str, channel: int = 1) -> float:
    """Maximum volts."""
    return bs.measure_vmax(_require_wave(alias, int(channel)))


def get_vmin(alias: str, channel: int = 1) -> float:
    """Minimum volts."""
    return bs.measure_vmin(_require_wave(alias, int(channel)))


def get_mean(alias: str, channel: int = 1) -> float:
    """Mean (average) volts."""
    return bs.measure_mean(_require_wave(alias, int(channel)))


def get_rms(alias: str, channel: int = 1) -> float:
    """True RMS volts (any shape)."""
    return bs.measure_rms(_require_wave(alias, int(channel)))


def get_pulse_width(alias: str, channel: int = 1) -> float:
    """Positive pulse width in seconds (first pulse). 0.0 if flat / no complete pulse."""
    return bs.measure_pulse_width(_require_wave(alias, int(channel)))


def get_sample_count(alias: str, channel: int = 1) -> int:
    """How many samples were transferred."""
    return int(len(_require_wave(alias, int(channel)).v))


def get_dt(alias: str, channel: int = 1) -> float:
    """Seconds between consecutive samples."""
    return float(_require_wave(alias, int(channel)).dt)


def get_t0(alias: str, channel: int = 1) -> float:
    """Time of the first sample (negative = before the trigger)."""
    return float(_require_wave(alias, int(channel)).t0)


def get_duration(alias: str, channel: int = 1) -> float:
    """Time from the first sample to the last, in seconds."""
    wf = _require_wave(alias, int(channel))
    return float(wf.t[-1] - wf.t[0])


# ---------------------------------------------------------------------------
# Saving (per alias). One channel -> the path; several -> per-channel + joint.
# ---------------------------------------------------------------------------
def _ensure_dir(path: str) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)


def save_csv(alias: str, path: str) -> str:
    """Save this scope's captured data as CSV. Returns the path(s) written."""
    waves = _waves.get(alias)
    if not waves:
        raise RuntimeError(f"Nothing captured for {alias!r}. Call capture() first.")
    _ensure_dir(path)
    written: list[str] = []
    multi = len(waves) > 1
    for c in sorted(waves):
        p = bs._derive(path, waves[c].channel) if multi else path
        bs.save_csv(waves[c], p)
        written.append(p)
    if multi:
        p = bs._derive(path, "joint")
        bs.save_joint_csv(waves, p)
        written.append(p)
    return ";".join(written)


def save_png(alias: str, path: str) -> str:
    """Save a PNG plot of this scope's captured data. Returns the path(s) written."""
    waves = _waves.get(alias)
    if not waves:
        raise RuntimeError(f"Nothing captured for {alias!r}. Call capture() first.")
    _ensure_dir(path)
    written: list[str] = []
    multi = len(waves) > 1
    for c in sorted(waves):
        p = bs._derive(path, waves[c].channel) if multi else path
        bs.save_png(waves[c], p)
        written.append(p)
    if multi:
        p = bs._derive(path, "joint")
        bs.save_png_joint(waves, p)
        written.append(p)
    return ";".join(written)


# ---------------------------------------------------------------------------
# Raw SCPI (per alias).
# ---------------------------------------------------------------------------
def query(alias: str, scpi: str) -> str:
    """Send a SCPI query to one scope and return the reply."""
    return _require_scope(alias).query(str(scpi))


def send(alias: str, scpi: str) -> bool:
    """Send a SCPI command to one scope (no reply). Always True."""
    _require_scope(alias).write(str(scpi))
    return True
