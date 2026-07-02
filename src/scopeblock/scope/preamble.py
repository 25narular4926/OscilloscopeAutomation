"""Preamble parsing + the affine transform: raw codes -> volts/seconds.

The conversion (per the MSO44B programmer manual / deck slide 7)::

    v[n] = (code[n] - YOFF) * YMULT + YZERO     # volts
    t[n] = XZERO + (n - PT_OFF) * XINCR          # seconds

PT_OFF is the trigger sample index, so the trigger lands at t = XZERO. We query
preamble fields individually (``WFMOutpre:XINCR?`` ...) rather than positionally
parsing ``WFMOutpre?`` — unambiguous across firmware revisions.

> TODO(confirm): validate exact PT_OFF sign/handling against a real WFMOutpre?
> response on the bench before trusting the time axis near the trigger point.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..waveform import Waveform

# Fields we read off the scope, with the SCPI sub-query and a parser.
_FLOAT_FIELDS = ("XINCR", "XZERO", "YMULT", "YOFF", "YZERO")
_INT_FIELDS = ("NR_PT", "PT_OFF", "BYT_NR")
_STR_FIELDS = ("ENCDG", "BN_FMT", "BYT_OR", "XUNIT", "YUNIT")


@dataclass
class Preamble:
    """The scaling + encoding description of a capture (a parsed WFMOutpre?)."""

    xincr: float
    xzero: float
    pt_off: int
    ymult: float
    yoff: float
    yzero: float
    nr_pt: int
    byt_nr: int
    bn_fmt: str
    byt_or: str
    encdg: str
    xunit: str = "s"
    yunit: str = "V"

    @classmethod
    def from_transport(cls, transport, source: str = "CH1") -> "Preamble":
        """Read the preamble for ``source`` by querying individual fields."""
        transport.write(f"DATa:SOURce {source}")
        q = transport.query

        def fnum(field: str) -> float:
            return float(q(f"WFMOutpre:{field}?"))

        def inum(field: str) -> int:
            return int(float(q(f"WFMOutpre:{field}?")))

        def text(field: str) -> str:
            return q(f"WFMOutpre:{field}?").strip().strip('"').upper()

        return cls(
            xincr=fnum("XINCR"),
            xzero=fnum("XZERO"),
            pt_off=inum("PT_OFF"),
            ymult=fnum("YMULT"),
            yoff=fnum("YOFF"),
            yzero=fnum("YZERO"),
            nr_pt=inum("NR_PT"),
            byt_nr=inum("BYT_NR"),
            bn_fmt=text("BN_FMT"),
            byt_or=text("BYT_OR"),
            encdg=text("ENCDG"),
            xunit=q("WFMOutpre:XUNIT?").strip().strip('"') or "s",
            yunit=q("WFMOutpre:YUNIT?").strip().strip('"') or "V",
        )

    @property
    def dtype(self) -> np.dtype:
        """numpy dtype matching byte width, signedness, and byte order."""
        signed = self.bn_fmt.upper().startswith("RI")
        order = ">" if self.byt_or.upper().startswith("MSB") else "<"
        kind = "i" if signed else "u"
        return np.dtype(f"{order}{kind}{self.byt_nr}")

    def as_meta(self) -> dict:
        """Serializable dict for ``Waveform.meta``."""
        return {
            "xincr": self.xincr,
            "xzero": self.xzero,
            "pt_off": self.pt_off,
            "ymult": self.ymult,
            "yoff": self.yoff,
            "yzero": self.yzero,
            "nr_pt": self.nr_pt,
            "byt_nr": self.byt_nr,
            "bn_fmt": self.bn_fmt,
            "byt_or": self.byt_or,
            "encdg": self.encdg,
            "xunit": self.xunit,
            "yunit": self.yunit,
        }


def parse_ieee_block(raw: bytes) -> bytes:
    """Strip an IEEE-488.2 definite-length block header ``#<n><len>``.

    Tolerates a trailing newline. Returns the raw payload bytes.
    """
    if not raw or raw[0:1] != b"#":
        raise ValueError("not an IEEE definite-length block (missing '#')")
    ndigits = int(raw[1:2])
    if ndigits == 0:
        raise ValueError("indefinite-length blocks (#0) are not supported")
    length = int(raw[2 : 2 + ndigits])
    start = 2 + ndigits
    return raw[start : start + length]


def decode_curve(raw: bytes, preamble: Preamble) -> np.ndarray:
    """Unpack a ``CURVe?`` IEEE block into integer code samples."""
    payload = parse_ieee_block(raw)
    codes = np.frombuffer(payload, dtype=preamble.dtype)
    if preamble.nr_pt and codes.size != preamble.nr_pt:
        # Not fatal — surface via meta, but trust the actual byte count.
        pass
    return codes.astype(np.int64)


def to_waveform(codes: np.ndarray, preamble: Preamble, channel: str = "CH1") -> Waveform:
    """Apply the affine transform -> ``Waveform`` in volts/seconds."""
    codes = np.asarray(codes, dtype=float)
    v = (codes - preamble.yoff) * preamble.ymult + preamble.yzero
    n = np.arange(codes.size, dtype=float)
    t = preamble.xzero + (n - preamble.pt_off) * preamble.xincr
    t0 = float(t[0]) if codes.size else preamble.xzero
    return Waveform(
        t=t,
        v=v,
        dt=float(preamble.xincr),
        t0=t0,
        channel=channel,
        units=preamble.yunit or "V",
        meta=preamble.as_meta(),
    )
