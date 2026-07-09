# `scripts/` — bench utilities

Two parallel paths, split by how they talk to instruments. Same bench, two transports.

```
scripts/
  sim/     offline-first, pyvisa-sim. Runs with NO hardware. The dev/CI path.
  real/    real hardware, tm_devices. Needs TekVISA/NI-VISA + live instruments.
```

## `sim/` — the offline path (pyvisa-sim)

Hand-written thin wrappers over PyVISA, exercised against the `sim_mso44b.yaml`
pyvisa-sim device. This is the **offline-first** path from `CLAUDE.md`: the whole
pipeline (connect → identify → configure → acquire → validate) runs with no hardware
attached.

| Script | Role |
|---|---|
| `bench_connect.py` | Open a VISA session and confirm the instrument responds. |
| `bench_identify.py` | `connect()` / `identify()` — session + `*IDN?` handshake. |
| `bench_configure.py` | Apply vertical/horizontal/trigger/transfer settings. |
| `bench_acquire.py` | Arm a single-sequence capture, read back a `Waveform` (ASCII curve). |
| `bench_validate.py` | Acquire, then validate the whole wave against expected values. |
| `verify_sim_state.py` | Sanity-check the sim's stored state. |
| `sim_mso44b.yaml` | The pyvisa-sim device definition (one MSO44B). |

Run anything here with the sim backend:

```bat
cd scripts\sim
python bench_validate.py --backend sim_mso44b.yaml@sim --resource "TCPIP0::sim-scope::INSTR"
```

Each script has a `*-guide.md` next to it with a full walkthrough.

## `real/` — the hardware path (tm_devices)

Built on Tektronix's **`tm_devices`** Python driver. A `DeviceManager` owns the
instrument(s) on the bench, and `add_scope()` auto-selects the right driver from `*IDN?` —
so the per-device `connect()` / `identify()` boilerplate collapses into one call. Only the
scope is wired up for now; the AFG is an additive `add_afg()` away.

| File | Role |
|---|---|
| `bench_scope.py` | Connect to the MSO44B, identify it, read a channel's acquisition. |
| `bench_scope-guide.md` | Walkthrough of `bench_scope.py`. |
| `bench_configure.py` | Apply vertical/horizontal/trigger/transfer settings via the tm_devices command tree. |
| `bench_configure-guide.md` | Walkthrough of `bench_configure.py`. |
| `tm_devices-reference.md` | Reference for the `tm_devices` library functions (DeviceManager, drivers, SCPI tree). |

Install its dependencies (on any machine) with the pinned requirements file:

```bat
cd scripts\real
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
```

Then, with a live scope reachable:

```bat
set SCOPE_RESOURCE=TCPIP0::192.168.0.10::INSTR
python bench_scope.py --identify
python bench_scope.py --capture --channel 1
```

### Why `real/` can't use the sim

`tm_devices` speaks **real VISA** — it calls `read_stb()` (status byte), `clear()`
(device clear), and drains the output buffer expecting real instrument semantics.
pyvisa-sim implements none of these (`NotImplementedError`), so tm_devices cannot
connect to the sim. That's the whole reason the two paths live in separate folders:
`sim/` stays runnable with zero hardware for development and CI, while `real/` is the
tm_devices path you run on the actual bench. Prove logic in `sim/`, run it for real in
`real/`.

> `tm_devices` is a real dependency of the `real/` path (`pip install tm_devices`). Keep
> it out of the `sim/` path so offline development never depends on it.
