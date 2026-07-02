# scopeblock — oscilloscope automation for the ECM HIL bench

Automated **output verification** for an engine-control-module hardware-in-loop
bench. It drives a **Tektronix MSO44B** over SCPI/VISA, pulls captures back to
the PC, parses them into a single `Waveform`, measures signal features, and
compares them against expected values with tolerances — turning manual scope
reading into automated, traceable pass/fail checks.

A **Tektronix AFG31102** function generator is the self-test oracle: drive a
known signal, capture it, run the whole pipeline, and assert the pipeline reports
back what was commanded — validating the block with no ECM in the loop.

This is **one block inside a larger test API**. The integration boundary is
[`src/scopeblock/api.py`](src/scopeblock/api.py) — small, typed, stable.

## Why it's built this way

- **Offline-first.** The whole pipeline (parse → measure → compare → export) runs
  and is tested with **no hardware**, using a `FakeTransport` and synthetic
  waveforms. Hardware is needed only for live acquisition.
- **One contract.** Everything funnels through the [`Waveform`](src/scopeblock/waveform.py)
  object. Live capture and file load emit the exact same shape.
- **Transport is thin and swappable.** All SCPI/VISA I/O sits behind the
  [`Transport`](src/scopeblock/transport/base.py) interface — LAN, USB, or an
  in-memory fake are an implementation detail.

## Install

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate on POSIX)
pip install -e ".[dev]"          # core + dev tools (pytest, ruff, mypy)
pip install -e ".[hardware]"     # add pyvisa for live bench acquisition
pip install -e ".[mf4,robot]"    # optional: MF4 export, Robot acceptance layer
```

Instrument addresses come from env vars — never hardcoded:

```bash
set SCOPE_RESOURCE=TCPIP0::192.168.0.10::INSTR
set AFG_RESOURCE=TCPIP0::192.168.0.11::INSTR
```

## Test

```bash
pytest                 # unit tests only — hardware tests auto-skip (no bench needed)
pytest -m hardware     # bench tests (requires SCOPE_RESOURCE / AFG_RESOURCE)
pytest --junitxml=report.xml   # ASPICE-friendly requirement → test → verdict evidence
robot robot/           # optional human-readable acceptance suite (runs offline too)
```

The full offline suite runs in well under a second.

## The public surface (`scopeblock.api`)

This is what the larger API integrates against. Keep it stable.

| Function | Purpose |
|---|---|
| `configure(transport, config) -> AppliedState` | Push channel / timebase / trigger / transfer settings to the MSO44B. |
| `acquire(transport, timeout) -> Waveform` | Run a single acquisition; return a scaled `Waveform`. |
| `load(path) -> Waveform` | Reload a saved capture (`.npz` lossless, or `.csv`). |
| `measure(wf, kind, **params) -> float` | Extract one feature: `frequency`, `period`, `duty`, `vpp`, `vamplitude`, `vhigh`, `vlow`, `mean`, `rms`, `rise_time`, `fall_time`, `edges`. |
| `compare(wf, kind, expected, tol) -> Result` | Measure and verdict against a tolerance band. |
| `export(wf, path, fmt) -> None` | Write CSV / npz / MF4. |
| `measure_all(wf) -> dict` | The common feature set in one call (for a verification block that wants a dict). |
| `run_test_case(wf, case) -> list[Result]` | Evaluate a loaded TOML `TestCase`, tagging each result with its requirement id. |
| `self_test(scope_t, afg_t, setup, stimulus) -> list[Result]` | The AFG loopback: drive → capture → measure → compare. |

### Minimal example (offline)

```python
from scopeblock import api
from scopeblock.transport.fake_transport import FakeTransport
from scopeblock.config import load_test_case

# In production `transport` is a VisaTransport(SCOPE_RESOURCE); here a fake stands in.
transport = FakeTransport()
# ... load a synthetic curve, or on the bench just configure + acquire:
case = load_test_case("configs/pwm_40pct_1khz.toml")
api.configure(transport, case.setup)
wf = api.acquire(transport, timeout=10.0)
for result in api.run_test_case(wf, case):
    print(result)                 # PASS [REQ-ECM-PWM-001] frequency: measured=1000 ...
api.export(wf, "capture.csv")
```

## Layout

```
src/scopeblock/
  waveform.py            # THE contract — numpy arrays + metadata
  api.py                 # public surface: configure/acquire/load/measure/compare/export
  transport/             # Transport interface + VISA + in-memory fake
  scope/                 # MSO44B driver + preamble parsing/scaling
  afg/                   # AFG31102 stimulus driver (self-test oracle)
  config/                # pydantic test-case schema + TOML loader
  analysis/              # measure.py (feature extraction) + compare.py (tolerances)
  io/                    # CSV / npz / MF4 export + reload
tests/unit/              # NO hardware — fake transport + synthetic waveforms
tests/integration/       # hardware-in-loop, @pytest.mark.hardware, skipped by default
tests/fixtures/          # synthetic-waveform generators
configs/                 # example TOML test cases
robot/                   # optional Robot Framework acceptance suite
docs/                    # design notes (incl. the pytest-vs-Robot decision)
```

## Test framework choice

**pytest is the primary engine; Robot Framework is an optional acceptance veneer.**
Full reasoning in [docs/automation-suite-decision.md](docs/automation-suite-decision.md).
In short: the pipeline is fine-grained numeric/array testing (pytest's home turf,
with `approx`, parametrization, and composable fixtures), it must run offline in
under a second, and JUnit XML already covers ASPICE traceability. Robot wraps the
same API for human-readable stakeholder suites — a leaf, not a root.

## Roadmap status

- **Phase 1 — connectivity + parse** ✅ Transport interface + fake + VISA; MSO44B
  configure/acquire; preamble parse + scaling → `Waveform`; AFG drive.
- **Phase 2 — measure + compare** ✅ Feature extraction; tolerance engine; TOML
  schema + loader; simulated AFG loopback green/red.
- **Phase 3 — integrate + harden** ◐ Stable `api.py`; pytest + optional Robot;
  CSV/MF4 export; sync/error handling (`*OPC?`, `*ESR?`/`ALLEV?`, timeouts,
  no-trigger). Remaining: JUnit/HTML reporting wiring in CI, optional TekHSI fast
  path, live-bench validation of `PT_OFF` handling.

> Hardware facts (SCPI commands, VISA resource strings, the conversion formula)
> are documented in [CLAUDE.md](CLAUDE.md) and must be verified against the live
> bench before trusting the time axis near the trigger point.
