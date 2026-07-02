"""End-to-end offline demo — no hardware required.

Mirrors the Week-1 exit gate ("drive a known signal, capture it, and recover
correct volts/time") and the Week-2 gate ("commanded values return within
tolerance"), but entirely against a FakeTransport:

    synthesize a known PWM  ->  encode to raw codes + preamble
                            ->  load into FakeTransport
                            ->  configure + acquire through the real pipeline
                            ->  measure + compare against the commanded values
                            ->  export

Run: ``python examples/offline_demo.py``
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests"))

from fixtures import make_pwm, signal_to_codes  # type: ignore  # noqa: E402

from scopeblock import api  # noqa: E402
from scopeblock.transport.fake_transport import FakeTransport  # noqa: E402
from scopeblock.config import load_test_case  # noqa: E402


def main() -> int:
    # 1. A known ECM-like PWM output: 1 kHz, 40% duty, 0-5 V.
    truth = make_pwm(frequency=1000.0, v_low=0.0, v_high=5.0, duty=40.0)

    # 2. Encode it the way the scope would return it, and load the fake transport.
    codes, preamble = signal_to_codes(truth, ymult=0.001)
    transport = FakeTransport()
    transport.load_curve(codes, preamble)

    # 3. Drive the real pipeline: configure -> acquire -> Waveform.
    case = load_test_case(os.path.join(os.path.dirname(__file__), "..", "configs",
                                       "pwm_40pct_1khz.toml"))
    api.configure(transport, case.setup)
    wf = api.acquire(transport, timeout=5.0)
    print(f"captured: {wf!r}")

    # 4. Measure + compare against the commanded values.
    print("\nmeasurements:")
    for k, v in api.measure_all(wf).items():
        print(f"  {k:12s} = {v:.6g}")

    print("\nverdicts:")
    results = api.run_test_case(wf, case)
    for r in results:
        print(f"  {r}")

    # 5. Export.
    out = os.path.join(os.path.dirname(__file__), "demo_capture.csv")
    api.export(wf, out)
    print(f"\nexported -> {out}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
