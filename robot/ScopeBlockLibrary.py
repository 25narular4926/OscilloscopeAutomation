"""Robot Framework keyword library — a thin veneer over the scopeblock API.

Adds no test logic of its own; every keyword delegates to ``scopeblock``. Two
backends:

* ``Open Simulated Bench`` — a FakeTransport pre-loaded with a synthetic capture,
  so the suite runs in CI with no hardware (mirrors the pytest offline path).
* ``Open Bench`` — real VISA transports from SCOPE_RESOURCE / AFG_RESOURCE.

Install: ``pip install -e ".[robot]"`` then ``robot robot/``.
"""

from __future__ import annotations

import os
import sys

# Make the offline synthetic generators importable when run from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

from scopeblock import api  # noqa: E402
from scopeblock.transport.fake_transport import FakeTransport  # noqa: E402
from scopeblock.config.schema import ScopeSetup  # noqa: E402


class ScopeBlockLibrary:
    ROBOT_LIBRARY_SCOPE = "TEST"

    def __init__(self) -> None:
        self._scope_transport = None
        self._wf = None

    # -- backends --------------------------------------------------------

    def open_simulated_bench(self, frequency=1000.0, v_low=0.0, v_high=5.0, duty=40.0):
        """Load a synthetic capture into a FakeTransport (no hardware)."""
        from fixtures import make_pwm, signal_to_codes  # type: ignore

        wf = make_pwm(
            frequency=float(frequency), v_low=float(v_low),
            v_high=float(v_high), duty=float(duty),
        )
        codes, pre = signal_to_codes(wf, ymult=0.001)
        fake = FakeTransport()
        fake.load_curve(codes, pre)
        self._scope_transport = fake

    def open_bench(self):
        """Open the real MSO44B over VISA (needs SCOPE_RESOURCE)."""
        from scopeblock.transport.visa_transport import VisaTransport

        self._scope_transport = VisaTransport(os.environ["SCOPE_RESOURCE"])

    # -- actions ---------------------------------------------------------

    def configure_scope(self, channel="CH1", record_length=100000):
        api.configure(
            self._scope_transport,
            ScopeSetup(channel=channel, record_length=int(record_length)),
        )

    def acquire_waveform(self, source="CH1", timeout=10.0):
        self._wf = api.acquire(self._scope_transport, timeout=float(timeout), source=source)
        return self._wf

    # -- assertions ------------------------------------------------------

    def measure(self, kind):
        return api.measure(self._wf, kind)

    def measurement_should_match(self, kind, expected, tolerance_pct=2.0):
        from scopeblock.analysis.compare import Tolerance

        result = api.compare(
            self._wf, kind, float(expected), Tolerance(rel=float(tolerance_pct) / 100.0)
        )
        if not result.passed:
            raise AssertionError(str(result))
        return str(result)
