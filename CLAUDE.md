# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

An **oscilloscope automation "scope block"** for an automotive ECM hardware-in-loop (HIL)
bench. It drives a **Tektronix MSO44B** oscilloscope over SCPI/VISA, pulls captures back to
the PC, parses them into a single `Waveform` object, measures signal features, and compares
them against expected values with tolerances — turning manual scope reading into automated,
traceable pass/fail checks that slot into a pytest / Robot Framework regression suite.

This package is **one block inside a larger test API**. Other blocks (a verification block,
etc.) live outside this repo and *consume* what this block produces. Treat the functions in
`src/scopeblock/api.py` as the integration boundary: keep that surface small, typed, and
stable. Do not assume anything about the larger API's internals.

A **Tektronix AFG31102** function generator is also on the bench. It is the **self-test
oracle**: drive a known signal, capture it, run the full pipeline, and assert the pipeline
reports back what was commanded — validating the scope block with no ECM in the loop.

## Core principles

- **Offline-first.** The whole pipeline (parse → measure → compare → export) must run and be
  tested with **no hardware attached**, using a fake transport and synthetic/saved waveforms.
  Hardware is only required for live acquisition. Never write code that can only be tested on
  the bench.
- **One contract.** Everything funnels through the `Waveform` object. Live capture and file
  load both emit the exact same shape; nothing downstream knows or cares which path produced it.
- **Transport is thin and swappable.** All SCPI/VISA I/O sits behind a `Transport` interface.
  Whether commands go out over LAN, USB, or a fake in-memory backend is an implementation
  detail the rest of the code never sees.
- **Don't over-build.** Implement the phase being worked on (see Roadmap). Stub later phases
  behind clear interfaces rather than half-implementing them.

## Hardware facts (reference — verify against the live bench)

### MSO44B oscilloscope
- SCPI instrument; no proprietary DLL needed. Talk to it over **VISA** (PyVISA + NI-VISA or
  TekVISA backend).
- VISA resource strings:
  - LAN: `TCPIP0::<ip>::INSTR` (VXI-11) or `TCPIP0::<ip>::4000::SOCKET`
  - USB: `USB0::0x0699::<product>::<serial>::INSTR`  (`0x0699` = Tektronix vendor id)
- Set `HEADer OFF` and `VERBose OFF` at session start so query responses are bare values.
- Sync/errors: `*IDN?` (handshake), `*OPC?` (operation complete — wait on this after acquire),
  `*ESR?` then `ALLEV?` (error status, then text).

### AFG31102 function generator
- SCPI instrument over VISA. Used to produce known stimulus / reference signals.
- Key commands: `SOURce<n>:FUNCtion:SHAPe`, `SOURce<n>:FREQuency`,
  `SOURce<n>:VOLTage:AMPLitude`, `SOURce<n>:VOLTage:OFFSet`, `SOURce<n>:PULSe:DCYCle`,
  `OUTPut<n>:STATE ON|OFF`.

### Waveform transfer (the parse step)
```
DATa:SOURce CH1
DATa:ENCdg SRIBinary        # signed integer binary
DATa:WIDth 2               # bytes per sample
DATa:STARt 1 ; DATa:STOP <record length>
WFMOutpre?                 # -> XINCR, XZERO, PT_OFF, YMULT, YOFF, YZERO,
                           #    NR_PT, ENCDG, BN_FMT, BYT_OR, BYT_NR
CURVe?                     # -> IEEE block of packed integer samples
```
Conversion from raw integer codes to engineering units:
```
v[n] = (code[n] - YOFF) * YMULT + YZERO     # volts
t[n] = XZERO + n * XINCR                     # seconds   (PT_OFF = trigger sample index)
```
> Confirm exact `PT_OFF` handling against a real `WFMOutpre?` response before trusting the
> time axis near the trigger point.

## Architecture & repo layout

```
src/scopeblock/
  waveform.py            # Waveform dataclass — THE contract. numpy arrays + metadata.
  api.py                 # public surface: configure/acquire/load/measure/compare/export
  transport/
    base.py              # Transport Protocol (write/query/query_binary)
    visa_transport.py    # PyVISA-backed SCPI transport
    fake_transport.py    # in-memory fake: canned preamble + synthetic CURVe? bytes
  scope/
    mso44b.py            # MSO44B: configure, acquire, curve-query orchestration
    preamble.py          # parse WFMOutpre fields + apply the affine transform -> Waveform
  afg/
    afg31102.py          # AFG stimulus driver (set shape/freq/ampl/duty, output on/off)
  config/
    schema.py            # typed config models (pydantic) for a test case
    loader.py            # load/validate TOML test cases
  analysis/
    measure.py           # feature extraction: freq, period, duty, Vpp, rise/fall, edges
    compare.py           # expected vs measured + tolerances -> Result (pass/fail + values)
  io/
    export.py            # CSV / MF4 export of a Waveform

tests/
  unit/                  # NO hardware. fake transport + synthetic/saved waveforms.
  integration/           # hardware-in-loop, marked @pytest.mark.hardware, skipped by default
  fixtures/              # saved captures + synthetic-waveform generators
configs/                 # example TOML test cases
robot/                   # optional Robot Framework suites wrapping the pytest fixtures
```

### The contract — `Waveform`
A dataclass holding `t: np.ndarray` (seconds), `v: np.ndarray` (volts), `dt: float`,
`t0: float`, `channel: str`, `units: str`, and `meta: dict` (the raw preamble: scaling
factors, encoding, record length, timestamp, source id). Keep it dependency-light and
serializable. This is the single most important type in the repo — change it deliberately.

### The public surface — `api.py`
Keep these signatures stable; this is what the larger API integrates against:
```
configure(transport, config) -> AppliedState
acquire(transport, timeout)  -> Waveform
load(path)                   -> Waveform
measure(wf, kind, **params)  -> float
compare(wf, expected, tol)   -> Result
export(wf, path, fmt)        -> None
```

## Tech stack & setup

- **Python 3.11+**, type hints everywhere.
- **numpy** for sample arrays; **pyvisa** for transport; **pydantic** for config models;
  **tomli/tomllib** for TOML; **pytest** for tests; optionally **asammdf** for MF4 export.
- Tektronix's own packages (`tm_devices`, `tm_data_types`, `TekHSI`) are an optional faster
  path — wire them behind the `Transport` interface later; do **not** make the core depend on
  them.

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"          # editable install + dev extras
pytest                            # unit tests only (hardware tests skipped by default)
pytest -m hardware                # bench tests (requires SCOPE_RESOURCE / AFG_RESOURCE env)
ruff check . && ruff format .     # lint + format
mypy src                          # type-check
```

Instrument addresses come from env vars (`SCOPE_RESOURCE`, `AFG_RESOURCE`), never hardcoded.

## Conventions

- **Transport behind an interface.** Nothing outside `transport/` and `scope/` may import
  pyvisa or build SCPI strings. Higher layers take a `Transport`.
- **Scaling lives in `scope/preamble.py`.** Transport returns raw bytes + preamble; the scope
  layer turns them into a `Waveform`. Don't scale samples anywhere else.
- **Measurement = feature extraction, not sample-by-sample compare.**
  - Edges via threshold crossing **with hysteresis** (don't double-count noise).
  - Frequency from successive rising-edge periods; duty from high-time / period.
  - Levels (V_high / V_low / Vpp) from **percentile or histogram, never raw min/max** —
    outliers wreck min/max.
- **Test cases are data.** A test case is a TOML file (scope setup + expected values +
  tolerances), loaded and validated by `config/`. Validate before touching hardware.
- **SCPI hygiene.** Set `HEADer OFF`/`VERBose OFF` once per session; always `*OPC?` after an
  acquire before reading; surface `*ESR?`/`ALLEV?` errors as exceptions, not silent failures.
- **Failure modes are results, not hangs.** `acquire()` has a hard timeout; a no-trigger
  condition returns an explicit result/raises a typed error — it never blocks forever.
- **Tag for traceability.** Test cases carry a requirement id; reporting emits JUnit XML (and
  Robot HTML) so requirement → test → measured value → verdict is traceable for ASPICE.

## Testing strategy

- **Unit tests run with zero hardware.** Use `FakeTransport` (returns a canned preamble and
  synthetic `CURVe?` bytes) and synthetic-waveform generators in `tests/fixtures/`. Cover:
  preamble parsing + scaling round-trips, every measurement against signals with known
  freq/duty/levels, the comparison engine (pass and fail), config validation, export.
- **Hardware tests are opt-in.** Mark with `@pytest.mark.hardware`; skip automatically unless
  `SCOPE_RESOURCE` (and `AFG_RESOURCE` for loopback) are set. Never let CI depend on a bench.
- **The AFG loopback is the headline integration test.** Command a known signal on the AFG,
  capture it on a scope channel, run `parse → measure → compare`, and assert the measured
  freq/duty/amplitude match the commanded values within tolerance. This validates the whole
  block end-to-end without the ECM.

## Build roadmap (implement in order)

**Phase 1 — connectivity + parse.** `Transport` interface + `VisaTransport` + `FakeTransport`;
`mso44b.configure/acquire`; `preamble` parsing + scaling → `Waveform`; `afg31102` basic
drive. *Done when:* a known AFG signal can be captured (or a fixture loaded) and produces a
`Waveform` with correct volts/time, verified offline and on the bench.

**Phase 2 — measure + compare.** `analysis/measure.py` features + `analysis/compare.py`
tolerance engine; TOML config schema + loader. *Done when:* a config-driven test case goes
green/red correctly, validated by the AFG loopback (commanded values returned within tol).

**Phase 3 — integrate + harden.** Stable `api.py` surface; pytest fixtures (and Robot suites);
JUnit/HTML reporting with requirement tags; CSV/MF4 export; robust sync/error handling
(`*OPC?`, `*ESR?`/`ALLEV?`, timeouts, no-trigger, scope offline); optional TekHSI fast path.
*Done when:* the suite runs unattended and produces a traceable report, and the block is
importable/callable by the larger API.

## Open questions — confirm before committing to an implementation

These do not block Phase 1; pick sensible defaults, leave a `# TODO(confirm):` note, and keep
the affected code behind an interface so the decision is cheap to change.

1. **Transport:** LAN or USB on this bench? (sets the default VISA resource string and whether
   the TekHSI port-5000 fast path is reachable)
2. **API contract:** what does the larger API's verification block expect back from this block
   — a `Waveform`, a measurements dict, specific return types? Shape `api.py` to match once
   known.
3. **Measurement ownership:** does the verification block consume raw waveforms and measure
   itself, or expect this block to return computed features? (affects what `api.py` exposes)
4. **MSO44B firmware version:** affects which SCPI commands/measurements are available.

## Guardrails

- Never hardcode instrument IP addresses, serials, or any bench-specific secrets — use env
  vars / a gitignored local config.
- Don't add a hard dependency on vendor packages (`tm_devices`/`TekHSI`) in the core path;
  keep them optional and behind the `Transport` interface.
- Bench hardware is real: code that changes scope/AFG output (especially `OUTPut:STATE ON`)
  should be explicit and only run in clearly-marked hardware paths, never as a side effect of
  an import or a unit test.
