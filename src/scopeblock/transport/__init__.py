"""Transport layer — all SCPI/VISA I/O sits behind the ``Transport`` interface.

Whether commands go out over LAN, USB, or a fake in-memory backend is an
implementation detail the rest of the code never sees. Nothing outside this
package and ``scope/`` may import pyvisa or build SCPI strings.
"""

from __future__ import annotations

from .base import Transport, TransportError
from .fake_transport import FakeTransport

__all__ = ["Transport", "TransportError", "FakeTransport"]
