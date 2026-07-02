"""AFG31102 stimulus driver.

Drives a known signal so the scope block can validate itself with no ECM in the
loop (the self-test oracle, deck slide 8). Turning an output ON is a real,
physical action — it only happens through :meth:`output`, never as a side effect
of construction or configuration.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..transport.base import Transport

SHAPES = {
    "SINE": "SINusoid",
    "SQUARE": "SQUare",
    "PULSE": "PULSe",
    "RAMP": "RAMP",
    "DC": "DC",
    "NOISE": "PRNoise",
}


@dataclass
class AFGSetup:
    """A single-channel stimulus definition (what the AFG should output)."""

    shape: str = "SQUARE"      # key into SHAPES
    frequency: float = 1_000.0  # Hz
    amplitude: float = 5.0      # Vpp
    offset: float = 0.0         # V
    duty: float | None = None   # percent, for PULSE/SQUARE
    channel: int = 1

    def normalized_shape(self) -> str:
        return SHAPES[self.shape.upper()]


class AFG31102:
    """Thin SCPI driver for the Tektronix AFG31102."""

    def __init__(self, transport: Transport) -> None:
        self.t = transport

    def idn(self) -> str:
        return self.t.query("*IDN?")

    def session_setup(self) -> str:
        idn = self.idn()
        self.t.write("HEADer OFF")
        self.t.write("VERBose OFF")
        return idn

    def configure(self, setup: AFGSetup) -> None:
        """Push a stimulus definition. Does NOT enable the output."""
        n = setup.channel
        self.t.write(f"SOURce{n}:FUNCtion:SHAPe {setup.normalized_shape()}")
        self.t.write(f"SOURce{n}:FREQuency {setup.frequency}")
        self.t.write(f"SOURce{n}:VOLTage:AMPLitude {setup.amplitude}")
        self.t.write(f"SOURce{n}:VOLTage:OFFSet {setup.offset}")
        if setup.duty is not None:
            self.t.write(f"SOURce{n}:PULSe:DCYCle {setup.duty}")

    def output(self, on: bool, channel: int = 1) -> None:
        """Enable/disable a physical output. The only method that drives the bench."""
        state = "ON" if on else "OFF"
        self.t.write(f"OUTPut{channel}:STATE {state}")

    def drive(self, setup: AFGSetup) -> None:
        """Convenience: configure then enable, in the safe order."""
        self.configure(setup)
        self.output(True, channel=setup.channel)

    def stop(self, channel: int = 1) -> None:
        self.output(False, channel=channel)
