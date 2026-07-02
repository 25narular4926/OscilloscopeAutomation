"""MSO44B driver — configure, acquire, curve-query orchestration.

Owns SCPI sequencing and sync hygiene (``HEADer OFF`` / ``VERBose OFF`` once per
session; ``*OPC?`` after an acquire; ``*ESR?`` / ``ALLEV?`` surfaced as
exceptions). Scaling is delegated to :mod:`scopeblock.scope.preamble`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..transport.base import Transport, TransportError
from ..waveform import Waveform
from .preamble import Preamble, decode_curve, to_waveform


class ScopeError(RuntimeError):
    """An instrument-reported error (from ``*ESR?`` / ``ALLEV?``)."""


class AcquireTimeout(ScopeError):
    """Acquisition did not complete (no trigger) within the timeout."""


@dataclass
class AppliedState:
    """What ``configure`` actually pushed to the scope — for traceability."""

    channel: str
    commands: list[str] = field(default_factory=list)
    idn: str = ""
    settings: dict[str, Any] = field(default_factory=dict)


class MSO44B:
    """Thin orchestration over a :class:`Transport`.

    The config passed to :meth:`configure` is duck-typed; it may be a
    :class:`scopeblock.config.schema.ScopeSetup` or any object exposing the same
    attributes (``channel``, ``vertical_scale``, ``vertical_offset``,
    ``record_length``, ``sample_rate`` or ``horizontal_scale``, ``trigger_*``,
    ``encoding``, ``byte_width``).
    """

    def __init__(self, transport: Transport) -> None:
        self.t = transport
        self._session_ready = False

    # -- session ---------------------------------------------------------

    def session_setup(self) -> str:
        """Put the session in a known, parse-friendly state. Returns ``*IDN?``."""
        idn = self.t.query("*IDN?")
        self.t.write("HEADer OFF")
        self.t.write("VERBose OFF")
        self.t.write("*CLS")  # clear status/error queue
        self._session_ready = True
        return idn

    def check_errors(self) -> None:
        """Raise :class:`ScopeError` if the event status register flags an error."""
        esr = int(self.t.query("*ESR?"))
        # bit 5 CME, bit 4 EXE, bit 3 DDE, bit 2 QYE — any of these is a problem.
        if esr & 0b0011_1100:
            events = self.t.query("ALLEV?")
            raise ScopeError(f"instrument error (ESR={esr}): {events}")

    # -- configure -------------------------------------------------------

    def configure(self, config: Any) -> AppliedState:
        """Apply channel/timebase/trigger + transfer settings from ``config``."""
        if not self._session_ready:
            idn = self.session_setup()
        else:
            idn = self.t.query("*IDN?")

        ch = getattr(config, "channel", "CH1")
        cmds: list[str] = []

        def send(cmd: str) -> None:
            self.t.write(cmd)
            cmds.append(cmd)

        # Vertical.
        send(f"SELect:{ch} ON")
        if getattr(config, "vertical_scale", None) is not None:
            send(f"{ch}:SCAle {config.vertical_scale}")
        if getattr(config, "vertical_offset", None) is not None:
            send(f"{ch}:OFFSet {config.vertical_offset}")
        if getattr(config, "coupling", None):
            send(f"{ch}:COUPling {config.coupling}")

        # Horizontal: prefer explicit sample rate + record length, else scale.
        if getattr(config, "sample_rate", None):
            send(f"HORizontal:SAMPLERate {config.sample_rate}")
        if getattr(config, "horizontal_scale", None):
            send(f"HORizontal:SCAle {config.horizontal_scale}")
        record_length = getattr(config, "record_length", None)
        if record_length:
            send(f"HORizontal:RECOrdlength {record_length}")

        # Trigger.
        if getattr(config, "trigger_source", None):
            send("TRIGger:A:TYPe EDGE")
            send(f"TRIGger:A:EDGE:SOUrce {config.trigger_source}")
            if getattr(config, "trigger_level", None) is not None:
                send(f"TRIGger:A:LEVel:{config.trigger_source} {config.trigger_level}")
            if getattr(config, "trigger_slope", None):
                send(f"TRIGger:A:EDGE:SLOpe {config.trigger_slope}")

        # Waveform transfer setup (the parse step).
        send(f"DATa:SOURce {ch}")
        send(f"DATa:ENCdg {getattr(config, 'encoding', 'SRIBinary')}")
        send(f"DATa:WIDth {getattr(config, 'byte_width', 2)}")
        send("DATa:STARt 1")
        send(f"DATa:STOP {record_length or 1_000_000}")

        self.check_errors()
        return AppliedState(
            channel=ch,
            commands=cmds,
            idn=idn,
            settings={
                "vertical_scale": getattr(config, "vertical_scale", None),
                "record_length": record_length,
                "trigger_source": getattr(config, "trigger_source", None),
            },
        )

    # -- acquire ---------------------------------------------------------

    def acquire(self, timeout: float = 10.0, source: str = "CH1") -> Waveform:
        """Run a single acquisition and pull one channel back as a ``Waveform``.

        Uses ``*OPC?`` to block until the capture completes. A transport timeout
        on ``*OPC?`` is surfaced as :class:`AcquireTimeout` — it never hangs
        forever (the transport must enforce ``timeout``).
        """
        if not self._session_ready:
            self.session_setup()

        self.t.write("ACQuire:STOPAfter SEQuence")  # single-sequence
        self.t.write("ACQuire:STATE RUN")
        try:
            # *OPC? returns '1' when the operation completes; the transport's
            # own timeout converts a no-trigger into an error rather than a hang.
            done = self.t.query("*OPC?")
        except TransportError as exc:
            raise AcquireTimeout(
                f"acquisition did not complete within {timeout}s (no trigger?)"
            ) from exc
        if done.strip() not in ("1", "+1"):
            raise AcquireTimeout(f"unexpected *OPC? response: {done!r}")

        self.check_errors()
        return self.read_waveform(source)

    def read_waveform(self, source: str = "CH1") -> Waveform:
        """Read preamble + ``CURVe?`` for ``source`` and scale to a ``Waveform``."""
        preamble = Preamble.from_transport(self.t, source=source)
        raw = self.t.query_binary("CURVe?")
        codes = decode_curve(raw, preamble)
        return to_waveform(codes, preamble, channel=source)
