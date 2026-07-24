#!/usr/bin/env python3
"""Find Tektronix oscilloscopes on the LAN and open sessions to all of them.

Two ways to discover, both feeding the same *IDN? confirmation step:
  - mDNS / LXI (the proper LXI way): scopes advertise themselves over multicast DNS,
    so they are found anywhere on the LAN with no subnet needed. Uses the 'zeroconf'
    package (pip install zeroconf) - pure Python, no vendor VISA.
  - Subnet scan (pure stdlib, no installs): scan a /24 in parallel for the SCPI port.
    Reliable on a switch with static IPs; a good fallback if mDNS is blocked.

Either way, each candidate IP is confirmed by opening a socket, sending *IDN?, and
keeping the ones that answer as a Tektronix scope. ScopeFleet then opens a SocketScope
session to every discovered scope and lets you drive them together.

  python discovery.py                         # mDNS discover + list scopes
  python discovery.py --subnet 192.168.1.0/24 # scan a subnet instead
  python discovery.py --open                   # discover, open all, print each *IDN?
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

# Same folder: reuse the transport and the configure/capture engine.
import os
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import bench_socket as bs  # noqa: E402

# Ports a Tektronix scope may answer SCPI on: 4000 = Tek Socket Server (Terminal mode),
# 5025 = LXI raw SCPI socket. We try each until *IDN? comes back.
DEFAULT_PORTS = (4000, 5025)

# Scope vendors and model families (to keep scopes and drop other LXI gear like the AFG).
# Families cover both brands: MSO/MSOX, DPO, MDO, TDS, DSO/DSOX, EDUX.
_SCOPE_VENDORS = ("TEKTRONIX", "KEYSIGHT", "AGILENT")
_SCOPE_FAMILIES = ("MSO", "DPO", "MDO", "TDS", "TBS", "DSO", "EDUX")

# LXI mDNS service types instruments advertise.
_MDNS_SERVICES = ("_lxi._tcp.local.", "_scpi-raw._tcp.local.",
                  "_vxi-11._tcp.local.", "_hislip._tcp.local.")


# ---------------------------------------------------------------------------
# Identify a single host.
# ---------------------------------------------------------------------------
def probe_idn(ip: str, port: int, timeout: float = 1.0) -> str:
    """Open a socket, send *IDN?, and return the reply (empty string on any failure)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            try:                                   # drain any connect banner / prompt
                s.recv(4096)
            except socket.timeout:
                pass
            s.sendall(b"*IDN?\n")
            time.sleep(0.2)
            data = b""
            try:
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
            except socket.timeout:
                pass
            return data.decode(errors="replace")
    except OSError:
        return ""


def is_scope(idn: str) -> bool:
    """True if the *IDN? reply is a Tektronix or Keysight oscilloscope (not an AFG etc.)."""
    up = idn.upper()
    return (any(v in up for v in _SCOPE_VENDORS)
            and any(fam in up for fam in _SCOPE_FAMILIES))


def _idn_model(idn: str) -> str:
    """'TEKTRONIX,MSO44,C012345,...' -> 'MSO44'."""
    parts = [p.strip() for p in idn.split(",")]
    return parts[1] if len(parts) > 1 else idn.strip()


def confirm(ip: str, ports: tuple[int, ...] = DEFAULT_PORTS,
            timeout: float = 1.0) -> dict | None:
    """Try each port on `ip`; return {ip, port, idn, model} for the first that identifies
    as a Tektronix instrument, else None."""
    for port in ports:
        idn = probe_idn(ip, port, timeout)
        if "TEKTRONIX" in idn.upper():
            # Keep the meaningful line (Terminal mode may echo the command / add a prompt).
            line = next((ln.strip(" \t\r\n>") for ln in idn.replace("\r", "\n").split("\n")
                         if "TEKTRONIX" in ln.upper()), idn.strip())
            return {"ip": ip, "port": port, "idn": line, "model": _idn_model(line)}
    return None


# ---------------------------------------------------------------------------
# Discovery: mDNS (primary) and subnet scan (fallback / alternative).
# ---------------------------------------------------------------------------
def discover_mdns_ips(timeout: float = 4.0,
                      service_types: tuple[str, ...] = _MDNS_SERVICES) -> set[str]:
    """Browse the LXI mDNS services for `timeout` seconds; return the set of IPv4 hosts
    that answered. Requires the 'zeroconf' package."""
    try:
        from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
    except ImportError as exc:
        raise RuntimeError(
            "mDNS discovery needs the 'zeroconf' package. Install it with:\n"
            "    pip install zeroconf\n"
            "or use the subnet scan instead (scan_subnet / --subnet)."
        ) from exc

    ips: set[str] = set()

    class _Listener(ServiceListener):
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=2000)
            if info:
                for addr in info.parsed_addresses():
                    if ":" not in addr:            # IPv4 only
                        ips.add(addr)

        def update_service(self, zc, type_, name):
            self.add_service(zc, type_, name)

        def remove_service(self, zc, type_, name):
            pass

    zc = Zeroconf()
    listener = _Listener()
    browsers = [ServiceBrowser(zc, st, listener) for st in service_types]
    try:
        time.sleep(timeout)                        # let responses arrive
    finally:
        for b in browsers:
            b.cancel()
        zc.close()
    return ips


def scan_subnet(subnet: str, ports: tuple[int, ...] = DEFAULT_PORTS,
                timeout: float = 0.5, workers: int = 64) -> list[dict]:
    """Scan every host in `subnet` (e.g. '192.168.1.0/24') in parallel; return the
    confirmed Tektronix instruments as {ip, port, idn, model} dicts."""
    import ipaddress
    from concurrent.futures import ThreadPoolExecutor

    hosts = [str(ip) for ip in ipaddress.ip_network(subnet, strict=False).hosts()]
    found: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(lambda ip: confirm(ip, ports, timeout), hosts):
            if result:
                found.append(result)
    return found


def local_ipv4s() -> set[str]:
    """The machine's own IPv4 addresses (non-loopback), used to find which subnet(s) to
    scan. Pure stdlib - no traffic is actually sent."""
    ips: set[str] = set()
    try:                                          # primary interface toward the LAN
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))                # no packets sent for UDP connect()
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:                                          # any other interfaces (e.g. the bench NIC)
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return {ip for ip in ips if not ip.startswith("127.")}


def local_subnets(prefix: int = 24) -> list[str]:
    """The /prefix subnet(s) the machine is on, e.g. ['192.168.1.0/24'] - the address
    range we scan to find scopes."""
    import ipaddress
    return sorted({str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))
                   for ip in local_ipv4s()})


def discover_scopes(port: int | None = None, subnet: str | None = None,
                    scopes_only: bool = True, timeout: float = 0.5,
                    ports: tuple[int, ...] = DEFAULT_PORTS) -> list[dict]:
    """Find scopes by scanning the network and *IDN?-probing every host.

    For each reachable IP we open a socket and send *IDN?; whoever answers as a Tektronix
    scope becomes a result (so you can open a session to each). This is the "detect IPs,
    ping with *IDN?, keep the responders" approach.

    port    : the single SCPI port to probe (you determine this). None = try the defaults.
    subnet  : a CIDR like "192.168.1.0/24" to scan. None = auto-detect the local subnet(s).
    Returns a list of {ip, port, idn, model} dicts, one per scope found.
    """
    probe_ports = (int(port),) if port else tuple(ports)
    subnets = [subnet] if subnet else local_subnets()
    results: list[dict] = []
    seen: set[str] = set()
    for sn in subnets:
        for info in scan_subnet(sn, probe_ports, timeout):
            if info["ip"] not in seen:
                results.append(info)
                seen.add(info["ip"])
    if scopes_only:
        results = [r for r in results if is_scope(r["idn"])]
    return results


# ---------------------------------------------------------------------------
# A fleet of scope sessions.
# ---------------------------------------------------------------------------
class ScopeFleet:
    """Open and drive SocketScope sessions to several scopes at once, by alias."""

    def __init__(self) -> None:
        self.scopes: dict[str, bs.SocketScope] = {}

    def add(self, alias: str, host: str, port: int = 4000) -> str:
        """Open a session and return its *IDN?."""
        sc = bs.SocketScope(host, port)
        self.scopes[alias] = sc
        return sc.query("*IDN?").strip()

    @classmethod
    def from_discovery(cls, found: list[dict]) -> "ScopeFleet":
        """Build a fleet from discover_scopes() output. Aliases are the model + last IP
        octet, e.g. 'MSO44_134', so they are stable and human-readable."""
        fleet = cls()
        for info in found:
            alias = f"{info['model']}_{info['ip'].split('.')[-1]}"
            fleet.scopes[alias] = bs.SocketScope(info["ip"], info["port"])
        return fleet

    def identify_all(self) -> dict[str, str]:
        return {alias: sc.query("*IDN?").strip() for alias, sc in self.scopes.items()}

    def configure_all(self, setup_name: str,
                      channels: list[int] | None = None) -> dict[str, bool]:
        """Apply a named setup to every scope; return {alias: all_verified}."""
        setup = bs.SETUPS.get(setup_name)
        if setup is None:
            raise ValueError(f"Unknown setup {setup_name!r}. Available: {', '.join(bs.SETUPS)}")
        out: dict[str, bool] = {}
        for alias, sc in self.scopes.items():
            results = bs.verify(sc, bs.configure(sc, setup, channels))
            out[alias] = all(r.ok for r in results)
        return out

    def capture_all(self, channels: list[int],
                    points: int = 10000) -> dict[str, dict]:
        """Read the given channels off every scope; return {alias: {ch: Waveform}}."""
        return {alias: bs.acquire_many(sc, channels, points)
                for alias, sc in self.scopes.items()}

    def close_all(self) -> None:
        for sc in self.scopes.values():
            try:
                sc.close()
            except Exception:
                pass
        self.scopes.clear()


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Find Tektronix scopes by scanning the LAN and *IDN?-probing hosts.")
    ap.add_argument("--subnet", default=None, metavar="CIDR",
                    help="Scan this subnet, e.g. 192.168.1.0/24. Default: auto-detect the "
                         "local subnet(s).")
    ap.add_argument("--port", type=int, default=None,
                    help="SCPI port to probe (you determine this). Default: try 4000 and 5025.")
    ap.add_argument("--timeout", type=float, default=0.5,
                    help="Per-host connect timeout in seconds. Default 0.5.")
    ap.add_argument("--all", action="store_true",
                    help="Keep all Tektronix instruments, not just oscilloscopes.")
    ap.add_argument("--open", action="store_true",
                    help="Open a session to every discovered scope and print each *IDN?.")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    targets = args.subnet or ", ".join(local_subnets()) or "(no local subnet found)"
    port_note = args.port if args.port else f"{DEFAULT_PORTS}"
    print(f"Scanning {targets} on port {port_note} with *IDN? ...")
    found = discover_scopes(port=args.port, subnet=args.subnet,
                            scopes_only=not args.all, timeout=args.timeout)

    if not found:
        print("No scopes found.", file=sys.stderr)
        return 1

    print(f"\nFound {len(found)} instrument(s):")
    for f in found:
        print(f"  {f['ip']}:{f['port']}  {f['idn']}")

    if args.open:
        fleet = ScopeFleet.from_discovery(found)
        try:
            print("\nOpened sessions:")
            for alias, idn in fleet.identify_all().items():
                print(f"  [{alias}] {idn}")
        finally:
            fleet.close_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
