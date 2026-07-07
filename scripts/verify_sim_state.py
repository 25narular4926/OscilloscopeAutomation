#!/usr/bin/env python3
"""
verify_sim_state.py — prove configure() actually lands on the (simulated) scope.

Runs the real configure() path against the pyvisa-sim MSO44B, then queries every
setting back and asserts the readback matches what was sent. Because the sim models
each setting as a stateful property (see sim_mso44b.yaml), a matching readback proves
the write reached the instrument — not just that the script emitted a string.

This is the round-trip complement to `bench_configure.py --dry-run` (which only shows
the SCPI that *would* be sent). No hardware required.

Usage (run from the scripts/ folder):
    python verify_sim_state.py

Exit codes:
    0  every setting stored and read back correctly
    1  at least one setting did not round-trip
"""
from __future__ import annotations

import os
import sys

from bench_configure import configure, DEFAULT_SETUP
from bench_identify import connect, identify

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "sim_mso44b.yaml") + "@sim"
RESOURCE = "TCPIP0::sim-scope::INSTR"


def main() -> int:
    inst, rm = connect(RESOURCE, 5000, BACKEND)
    with inst:
        idn = identify(inst)
        applied = configure(inst, DEFAULT_SETUP)

        print(f"IDN: {idn}")
        print(f"Applied {len(applied.commands)} commands. Reading each back from the sim:\n")

        rows = []
        ok = 0
        for cmd in applied.commands:
            head, _, value = cmd.partition(" ")     # "CH1:SCAle 0.5" -> head, "0.5"
            query = head + "?"                       # "CH1:SCAle?"
            readback = inst.query(query).strip()
            passed = readback == value
            ok += passed
            rows.append((query, value, readback, "PASS" if passed else "FAIL"))

    w1 = max(len(r[0]) for r in rows)
    w2 = max(len(r[1]) for r in rows)
    w3 = max(len(r[2]) for r in rows)
    print(f"{'QUERY'.ljust(w1)}  {'SENT'.ljust(w2)}  {'READBACK'.ljust(w3)}  RESULT")
    print(f"{'-' * w1}  {'-' * w2}  {'-' * w3}  ------")
    for q, v, rb, res in rows:
        print(f"{q.ljust(w1)}  {v.ljust(w2)}  {rb.ljust(w3)}  {res}")

    print(f"\n{ok}/{len(rows)} settings stored and read back correctly.")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
