"""The Transport interface — a thin, swappable SCPI line.

A Transport carries bytes/strings to and from an instrument. It knows nothing
about waveforms, scaling, or measurements. Three operations are enough to drive
both the MSO44B and the AFG31102:

* ``write``        — fire a command, expect no reply
* ``query``        — send a query, read an ASCII reply
* ``query_binary`` — send a query, read a raw IEEE-488.2 binary block (CURVe?)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class TransportError(RuntimeError):
    """Raised on any transport-level failure (timeout, I/O, closed session)."""


@runtime_checkable
class Transport(Protocol):
    """Minimal SCPI transport contract.

    Implementations: :class:`~scopeblock.transport.fake_transport.FakeTransport`
    (in-memory, for tests) and
    :class:`~scopeblock.transport.visa_transport.VisaTransport` (PyVISA-backed).
    """

    def write(self, command: str) -> None:
        """Send a command with no expected response."""
        ...

    def query(self, command: str) -> str:
        """Send a query and return the ASCII response, stripped of whitespace."""
        ...

    def query_binary(self, command: str) -> bytes:
        """Send a query and return the raw response bytes (an IEEE block)."""
        ...

    def close(self) -> None:
        """Release the underlying session."""
        ...
