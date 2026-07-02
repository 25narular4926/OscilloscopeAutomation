"""An in-memory fake Transport — the whole offline story depends on this.

It answers the handshake/sync queries, records every command written (so
``configure`` can be asserted), and can serve a synthetic ``CURVe?`` IEEE block
plus the matching ``WFMOutpre?`` preamble built from a code array. No hardware,
no pyvisa.
"""

from __future__ import annotations

import struct
from typing import Callable

import numpy as np

from .base import TransportError


def encode_ieee_block(payload: bytes) -> bytes:
    """Wrap ``payload`` in an IEEE-488.2 definite-length block: ``#<n><len><data>``."""
    length = str(len(payload))
    header = f"#{len(length)}{length}".encode("ascii")
    return header + payload


class FakeTransport:
    """Programmable in-memory SCPI transport.

    Parameters
    ----------
    idn:
        Response served for ``*IDN?``.

    Notes
    -----
    * ``write`` appends to :attr:`history` so tests can assert the command stream.
    * ``query`` looks up :attr:`responses` (exact match, case-insensitive on the
      command), falling back to built-in handshake defaults, then to a registered
      ``default_query`` callable.
    * ``load_curve`` installs a synthetic capture answerable by ``WFMOutpre?`` /
      individual ``WFMOutpre:<field>?`` queries and ``CURVe?``.
    """

    DEFAULT_PREAMBLE = {
        "BYT_NR": 2,
        "BIT_NR": 16,
        "ENCDG": "BINARY",
        "BN_FMT": "RI",  # signed integer
        "BYT_OR": "MSB",
        "WFID": "Ch1, DC coupling, 1.0V/div, 100us/div",
        "NR_PT": 0,  # filled in by load_curve
        "PT_FMT": "Y",
        "XUNIT": "s",
        "XINCR": 1e-6,
        "XZERO": 0.0,
        "PT_OFF": 0,
        "YUNIT": "V",
        "YMULT": 1.0,
        "YOFF": 0.0,
        "YZERO": 0.0,
    }

    def __init__(self, idn: str = "TEKTRONIX,MSO44B,FAKE0001,FV:2.0.0") -> None:
        self.history: list[str] = []
        self.responses: dict[str, str] = {}
        self.default_query: Callable[[str], str] | None = None
        self._idn = idn
        self._curve_block: bytes | None = None
        self._preamble: dict[str, object] = {}
        self.closed = False

    # -- Transport interface --------------------------------------------

    def write(self, command: str) -> None:
        if self.closed:
            raise TransportError("transport is closed")
        self.history.append(command)

    def query(self, command: str) -> str:
        if self.closed:
            raise TransportError("transport is closed")
        self.history.append(command)
        cmd = command.strip()
        key = cmd.upper()

        # Explicit canned responses win.
        if key in self.responses:
            return self.responses[key]

        # Built-in handshake / sync defaults.
        if key == "*IDN?":
            return self._idn
        if key in ("*OPC?", "BUSY?"):
            return "1" if key == "*OPC?" else "0"
        if key == "*ESR?":
            return "0"
        if key in ("ALLEV?", "ALLEV:ALL?"):
            return '0,"No events to report - queue empty"'

        # Preamble field queries: WFMOUTPRE:XINCR? etc., and the whole-string form.
        if key.startswith("WFMOUTPRE"):
            resp = self._answer_preamble(key)
            if resp is not None:
                return resp

        if self.default_query is not None:
            return self.default_query(cmd)

        raise TransportError(f"FakeTransport has no response for query: {command!r}")

    def query_binary(self, command: str) -> bytes:
        if self.closed:
            raise TransportError("transport is closed")
        self.history.append(command)
        key = command.strip().upper()
        if key.startswith("CURVE") or key.startswith("CURV"):
            if self._curve_block is None:
                raise TransportError("no curve loaded; call load_curve() first")
            return self._curve_block
        raise TransportError(f"FakeTransport has no binary response for: {command!r}")

    def close(self) -> None:
        self.closed = True

    # -- test helpers ----------------------------------------------------

    def load_curve(
        self,
        codes: np.ndarray,
        preamble: dict[str, object] | None = None,
    ) -> None:
        """Install a synthetic capture.

        ``codes`` are the raw integer sample codes (what ``CURVe?`` returns).
        ``preamble`` overrides any of :attr:`DEFAULT_PREAMBLE`; ``NR_PT``,
        ``BYT_NR`` and ``BIT_NR`` are derived from ``codes`` if not given.
        """
        codes = np.asarray(codes)
        pre = dict(self.DEFAULT_PREAMBLE)
        if preamble:
            pre.update(preamble)
        pre["NR_PT"] = int(codes.size)
        self._preamble = pre

        byt_nr = int(pre["BYT_NR"])
        signed = str(pre["BN_FMT"]).upper().startswith("RI")
        order = ">" if str(pre["BYT_OR"]).upper().startswith("MSB") else "<"
        fmt_char = {1: "b", 2: "h", 4: "i"}[byt_nr] if signed else {1: "B", 2: "H", 4: "I"}[byt_nr]
        packed = struct.pack(f"{order}{codes.size}{fmt_char}", *codes.astype(int).tolist())
        self._curve_block = encode_ieee_block(packed)

    def set_response(self, command: str, response: str) -> None:
        """Register a canned ASCII response for an exact query string."""
        self.responses[command.strip().upper()] = response

    def _answer_preamble(self, key: str) -> str | None:
        if not self._preamble:
            return None
        # Whole-string form: WFMOUTPRE?  -> Tek positional order.
        if key in ("WFMOUTPRE?", "WFMPRE?"):
            p = self._preamble
            return ";".join(
                str(p[f])
                for f in (
                    "BYT_NR", "BIT_NR", "ENCDG", "BN_FMT", "BYT_OR", "WFID",
                    "NR_PT", "PT_FMT", "XUNIT", "XINCR", "XZERO", "PT_OFF",
                    "YUNIT", "YMULT", "YOFF", "YZERO",
                )
            )
        # Field form: WFMOUTPRE:XINCR?  -> last token before '?'.
        field = key.split(":")[-1].rstrip("?")
        if field in self._preamble:
            return str(self._preamble[field])
        return None
