"""PyVISA-backed SCPI transport — the only place that imports pyvisa.

Kept deliberately thin: open a resource, write/query strings, read binary blocks.
All scaling, parsing, and sync logic lives in the ``scope/`` layer above. pyvisa
is an optional dependency (``pip install -e ".[hardware]"``); importing this
module without it raises a clear error only when you actually try to connect.
"""

from __future__ import annotations

from .base import TransportError

try:  # pragma: no cover - import guard, exercised only on the bench
    import pyvisa
except ImportError:  # pragma: no cover
    pyvisa = None  # type: ignore[assignment]


class VisaTransport:
    """A SCPI transport over VISA (LAN/USB/GPIB).

    Parameters
    ----------
    resource:
        VISA resource string, e.g. ``"TCPIP0::192.168.0.10::INSTR"`` (LAN) or
        ``"USB0::0x0699::0x0522::<serial>::INSTR"`` (USB). Comes from an env var
        (``SCOPE_RESOURCE`` / ``AFG_RESOURCE``) — never hardcoded.
    timeout_ms:
        Per-operation VISA timeout in milliseconds.
    backend:
        Optional pyvisa backend spec, e.g. ``"@py"`` for pyvisa-py. Defaults to
        the system VISA (NI-VISA / TekVISA).
    """

    def __init__(self, resource: str, timeout_ms: int = 10_000, backend: str = "") -> None:
        if pyvisa is None:
            raise TransportError(
                "pyvisa is not installed. Install the hardware extra: "
                'pip install -e ".[hardware]"'
            )
        self.resource = resource
        try:
            self._rm = pyvisa.ResourceManager(backend)
            self._inst = self._rm.open_resource(resource)
            self._inst.timeout = timeout_ms
        except Exception as exc:  # pyvisa raises a variety of errors
            raise TransportError(f"failed to open VISA resource {resource!r}: {exc}") from exc

    def write(self, command: str) -> None:
        try:
            self._inst.write(command)
        except Exception as exc:
            raise TransportError(f"write failed for {command!r}: {exc}") from exc

    def query(self, command: str) -> str:
        try:
            return self._inst.query(command).strip()
        except Exception as exc:
            raise TransportError(f"query failed for {command!r}: {exc}") from exc

    def query_binary(self, command: str) -> bytes:
        """Read a raw IEEE binary block. We keep the bytes raw and decode in the
        scope layer so encoding decisions live in exactly one place."""
        try:
            self._inst.write(command)
            return bytes(self._inst.read_raw())
        except Exception as exc:
            raise TransportError(f"binary query failed for {command!r}: {exc}") from exc

    def close(self) -> None:
        try:
            self._inst.close()
            self._rm.close()
        except Exception:  # pragma: no cover - best effort on teardown
            pass
