"""scopeblock — oscilloscope automation "scope block" for an ECM HIL bench.

Drives a Tektronix MSO44B over SCPI/VISA, parses captures into a single
``Waveform`` object, measures signal features, and compares them against
expected values with tolerances.

The whole pipeline (parse -> measure -> compare -> export) runs offline with a
``FakeTransport``; hardware is only required for live acquisition.

The public surface lives in :mod:`scopeblock.api`.
"""

from __future__ import annotations

from .waveform import Waveform

__all__ = ["Waveform"]
__version__ = "0.1.0"
