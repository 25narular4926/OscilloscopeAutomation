"""The contract: a single ``Waveform`` object.

Live capture and file load both emit this exact shape. Nothing downstream knows
or cares which path produced it. Keep it dependency-light and serializable —
this is the single most important type in the repo; change it deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Waveform:
    """A scaled, time-domain capture in engineering units.

    Attributes
    ----------
    t:
        Sample times in **seconds**. Shape ``(N,)``.
    v:
        Sample values in **volts** (or whatever ``units`` says). Shape ``(N,)``.
    dt:
        Sample interval in seconds (``XINCR``). ``1 / dt`` is the sample rate.
    t0:
        Time of the first sample in seconds.
    channel:
        Source identifier, e.g. ``"CH1"``.
    units:
        Vertical engineering units, e.g. ``"V"``.
    meta:
        Raw preamble and provenance: scaling factors, encoding, record length,
        trigger index, timestamp, source id. Free-form but must stay
        JSON/​npz serializable (scalars and strings).
    """

    t: np.ndarray
    v: np.ndarray
    dt: float
    t0: float
    channel: str = "CH1"
    units: str = "V"
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.t = np.asarray(self.t, dtype=float)
        self.v = np.asarray(self.v, dtype=float)
        if self.t.shape != self.v.shape:
            raise ValueError(
                f"t and v must share a shape; got {self.t.shape} vs {self.v.shape}"
            )
        if self.t.ndim != 1:
            raise ValueError(f"Waveform must be 1-D; got {self.t.ndim}-D")

    # conveniences

    @property
    def n(self) -> int:
        """Number of samples."""
        return int(self.v.size)

    @property
    def sample_rate(self) -> float:
        """Sample rate in samples/second."""
        return 1.0 / self.dt if self.dt else float("nan")

    @property
    def duration(self) -> float:
        """Record duration in seconds."""
        return self.n * self.dt

    @classmethod
    def from_samples(
        cls,
        v: np.ndarray,
        dt: float,
        t0: float = 0.0,
        channel: str = "CH1",
        units: str = "V",
        meta: dict[str, Any] | None = None,
    ) -> "Waveform":
        """Build a Waveform from a value array and a sample interval.

        The time axis is generated as ``t0 + n * dt`` — the same affine form the
        scope layer produces from the preamble.
        """
        v = np.asarray(v, dtype=float)
        t = t0 + np.arange(v.size, dtype=float) * dt
        return cls(t=t, v=v, dt=dt, t0=t0, channel=channel, units=units, meta=meta or {})

    # serialization 

    def to_dict(self) -> dict[str, Any]:
        """Lightweight summary (no sample arrays) — handy for reports/logs."""
        return {
            "channel": self.channel,
            "units": self.units,
            "dt": self.dt,
            "t0": self.t0,
            "n": self.n,
            "sample_rate": self.sample_rate,
            "meta": self.meta,
        }

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return (
            f"Waveform(channel={self.channel!r}, n={self.n}, "
            f"dt={self.dt:.3e}s, t0={self.t0:.3e}s, units={self.units!r})"
        )
