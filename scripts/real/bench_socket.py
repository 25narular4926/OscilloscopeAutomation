#!/usr/bin/env python3

# No-VISA fallback: talk to the MSO44B over its raw Socket Server, for benches where
# tm_devices/VISA can't connect (e.g. link-local LAN where only the scope's Socket
# Server is reachable). Pure Python stdlib — no VISA, no tm_devices, no pyvisa.
#
# The scope's Socket Server must be ON. On this scope, Utility -> I/O -> Socket Server:
#   Enabled via Protocol = Terminal (Protocol "None" leaves the port closed), Port 4000.
# Terminal mode echoes commands / adds a prompt, so replies are cleaned below.
#
# Text SCPI only (identify / queries). Binary curve transfer is NOT handled here.
#
#   python bench_socket.py --host 169.254.8.134 --identify
#   python bench_socket.py --host 169.254.8.134 --query "HORizontal:RECOrdlength?"

from __future__ import annotations

import argparse
import os
import socket
import sys
import time


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
        print(f"Could not connect to {host}:{args.port} — {exc}", file=sys.stderr)
        print("Is the scope's Socket Server ON (Protocol = Terminal) on that port?",
              file=sys.stderr)
        return 1

    try:
        if args.query:
            print(scope.query(args.query, debug=args.debug))
        else:  # default action is identify
            print("IDN:", scope.query("*IDN?", debug=args.debug))
        return 0
    finally:
        scope.close()


if __name__ == "__main__":
    sys.exit(main())
