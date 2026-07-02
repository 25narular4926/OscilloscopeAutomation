"""Scope layer — turns raw transport bytes + preamble into a ``Waveform``.

Scaling lives here and nowhere else. The transport returns raw bytes and the
preamble; this layer applies the affine transform.
"""

from __future__ import annotations

from .preamble import Preamble, decode_curve, to_waveform
from .mso44b import MSO44B

__all__ = ["Preamble", "decode_curve", "to_waveform", "MSO44B"]
