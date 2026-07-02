# `bench_connect.py` — a detailed guide

A walkthrough of what the script does (functions, then a line-by-line tour), plus
how to install the tools for **real hardware** and for the **no-hardware / simulator**
path.

---

## 1. What this script is (and isn't)

`bench_connect.py` is a **connectivity spike** — a small, throwaway diagnostic. Its
whole job is to prove the physical link to the oscilloscope works *before* you build
any library code on top of it:

> open a VISA session → confirm the instrument answers → put it in a clean state →
> read its error queue → close cleanly.

It is **not** part of the `scopeblock` library. But the open → identify → hygiene
sequence it performs is the exact blueprint that later becomes `VisaTransport` +
`MSO44B.session_setup()` (Function 2). So getting this right de-risks everything after it.

### Three background terms
- **VISA** (Virtual Instrument Software Architecture) — a standard API for talking to
  lab instruments over USB, LAN, GPIB, etc. You need a *VISA backend* (a driver) installed.
- **SCPI** (Standard Commands for Programmable Instruments) — the text command language
  the scope understands, e.g. `*IDN?` ("identify yourself"), `HEADer OFF`.
- **PyVISA** — the Python package that lets your code speak VISA. It needs a backend
  under it (NI-VISA, TekVISA, or the pure-Python `pyvisa-py`).

---

## 2. The shape of the script

There are just **two functions** and a launcher:

| Piece | Lines | Role |
|---|---|---|
| module docstring | 2–27 | What it does, usage examples, exit-code table. |
| `_parse_args()` | 36–52 | Defines and reads the `--resource` / `--timeout` command-line options. |
| `main()` | 55–115 | Does the actual work; returns an integer exit code. |
| `if __name__ == "__main__":` | 118–119 | Runs `main()` only when executed directly (not on import). |

A key design choice: **`main()` returns an exit code instead of calling `sys.exit()` itself.**
That makes it testable (you can call `main([...])` in a test and check the number) and
keeps the "quit the process" decision in one place (line 119).

---

## 3. Line-by-line tour

### Header — lines 1–33

```python
#!/usr/bin/env python3          # line 1  — lets Unix run the file directly as a program
"""bench_connect.py — ..."""    # lines 2–27 — the docstring: purpose, usage, exit codes
from __future__ import annotations   # line 29 — allows modern type hints (e.g. list[str] | None)
import argparse                 # line 31 — standard library: command-line argument parsing
import os                       # line 32 — standard library: read environment variables
import sys                      # line 33 — standard library: stderr + exit code
```

- **Line 1** (`#!...`) is a "shebang" — only meaningful on Linux/Mac if you mark the file
  executable. Harmless on Windows.
- **Line 29** lets you write `list[str] | None` type hints on older Pythons without error.
- Note there is **no `import pyvisa` at the top** — that's deliberate (explained at line 60).

### `_parse_args()` — lines 36–52

```python
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open a VISA session to the MSO44B and confirm it responds.",
    )
    parser.add_argument("--resource", default=None,
        help="VISA resource string (overrides the SCOPE_RESOURCE env var).")
    parser.add_argument("--timeout", type=int, default=5000,
        help="I/O timeout in milliseconds (hard cap so a no-reply cannot hang). Default: 5000.")
    return parser.parse_args(argv)
```

- Builds an argument parser. `--help` is added automatically by `argparse`.
- **`--resource`** defaults to `None` → meaning "not given," so `main()` can fall back to
  the environment variable.
- **`--timeout`** is forced to an `int` (milliseconds), default `5000` (5 seconds).
- **`argv=None`** parameter: when `None`, `argparse` reads the real command line. Passing a
  list instead (e.g. `_parse_args(["--timeout", "10000"])`) lets a test drive it directly.

### `main()` — the guarded imports, lines 55–77

```python
def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)                      # line 56 — get --resource / --timeout

    try:
        import pyvisa                             # line 61 — imported HERE, not at top
    except ImportError:
        print("pyvisa is not installed. ...", file=sys.stderr)   # line 63
        return 3                                  # line 65 — exit code 3
```

- **Line 61 — lazy import.** pyvisa is imported *inside* `main()`, not at the top of the
  file. Two payoffs: (1) importing this module never requires pyvisa (a test can import it
  freely), and (2) if pyvisa is missing you get a one-line hint (line 63) instead of a crash.
- **`file=sys.stderr`** sends the message to the *error* stream, not normal output — the
  convention for diagnostics, so it doesn't pollute real results.
- **`return 3`** — the exit code for "pyvisa not installed."

```python
    resource = args.resource or os.environ.get("SCOPE_RESOURCE")   # line 67
```

- **Line 67 — resolve the address, flag first.** `args.resource or os.environ.get(...)`
  reads: "use `--resource` if it was given; otherwise fall back to the `SCOPE_RESOURCE`
  environment variable." If neither exists, `resource` becomes `None` (handled at line 80).
  The instrument address is **never hardcoded** — that's a project guardrail.

```python
    try:
        rm = pyvisa.ResourceManager()            # line 72 — loads the VISA backend
    except Exception as exc:
        print(f"No VISA backend available: {exc}", file=sys.stderr)   # line 74
        print("Install NI-VISA or TekVISA, or ... pip install pyvisa-py", file=sys.stderr)
        return 3                                  # line 77
```

- **Line 72 — create the ResourceManager.** This is what actually loads the VISA driver.
  It can *fail even when pyvisa is installed* if there's no backend behind it — which is
  exactly the situation on a fresh machine.
- **Lines 73–77** catch that and print install guidance instead of a traceback (again exit
  3). This is why running it right now says "No VISA backend available."

### `main()` — the no-resource path, lines 79–90

```python
    if not resource:                             # line 80
        print("SCOPE_RESOURCE is unset and --resource was not given.", file=sys.stderr)
        try:
            visible = rm.list_resources()        # line 83 — ask VISA what it can see
        except Exception as exc:
            visible = ()
            print(f"(could not list resources: {exc})", file=sys.stderr)
        print("Visible VISA resources:", visible or "(none found)", file=sys.stderr)
        print("Set SCOPE_RESOURCE or pass --resource <string> and retry.", file=sys.stderr)
        return 2                                  # line 90 — exit code 2
```

- **Line 80** triggers only if you gave neither `--resource` nor `SCOPE_RESOURCE`.
- **Line 83 — `list_resources()`** asks the backend to enumerate every instrument address
  it can currently see. This is genuinely useful: it prints the exact strings you could copy
  into `SCOPE_RESOURCE`.
- **`return 2`** — a distinct exit code so a script/CI can tell "no address configured"
  apart from other failures. Note: it fails *cleanly with guidance*, never a traceback.

### `main()` — the real work, lines 92–115

```python
    try:
        with rm.open_resource(resource) as inst:     # line 95 — open the session
            inst.timeout = args.timeout              # line 96 — hard I/O cap (ms)
            inst.read_termination = "\n"             # line 97 — how a reply ends
            inst.write_termination = "\n"            # line 98 — appended to each command

            idn = inst.query("*IDN?").strip()        # line 101 — handshake
            print("IDN:", idn)                       # line 102

            for cmd in ("HEADer OFF", "VERBose OFF", "*CLS"):   # line 105 — hygiene
                inst.write(cmd)                      # line 106

            errors = inst.query("ALLEV?").strip()    # line 109 — read the error queue
            print("ERR:", errors)                    # line 110

        return 0                                     # line 112 — success
    except pyvisa.errors.VisaIOError as exc:         # line 113
        print(f"VISA I/O error talking to {resource!r}: {exc}", file=sys.stderr)
        return 1                                     # line 115 — exit code 1
```

- **Line 95 — `with ... as inst:`** opens the session inside a *context manager*. Whatever
  happens next — success, an exception, Ctrl-C — Python runs the session's cleanup and
  **closes it** when the block ends. This prevents a "lingering lock" that would block the
  next connection. (This is the single most important reliability line.)
- **Line 96 — `inst.timeout`** sets the hard cap (in **milliseconds**) on how long any read
  waits. If the scope never replies, the call raises an error after this time rather than
  **hanging forever**. That's what "a no-reply cannot hang" means.
- **Lines 97–98 — terminations.** `write_termination="\n"` appends a newline to every
  command you send (SCPI instruments expect it); `read_termination="\n"` tells PyVISA a
  reply ends at a newline. Without these you get partial reads or spurious timeouts.
- **Line 101 — `*IDN?`** is the handshake: ask the scope to identify itself. `.strip()`
  removes trailing whitespace/newline. If this returns a Tektronix identity, the whole chain
  (cable → driver → backend → address) is proven working.
- **Lines 105–106 — session hygiene.** `HEADer OFF` and `VERBose OFF` make future query
  replies bare values instead of echoing the command; `*CLS` clears the status/error queue
  so you start from a known-clean state. Sent once, in a small loop.
- **Line 109 — `ALLEV?`** ("all events") reads the error/event queue back. An **empty**
  reply means all your commands were accepted.
- **Line 112 — `return 0`** after the `with` block: success. By this point the session has
  already been closed by the context manager.
- **Lines 113–115** catch a VISA I/O failure (timeout, cable pulled, bad command) and turn
  it into a clean message + **exit code 1** — no traceback.

### The launcher — lines 118–119

```python
if __name__ == "__main__":     # true only when you run the file directly
    sys.exit(main())           # run main(), and exit the process with its return code
```

- **`if __name__ == "__main__":`** means "only do this when the file is *run*, not when it's
  *imported*." So a test can `import bench_connect` and call `main([...])` without the script
  auto-executing.
- **`sys.exit(main())`** runs `main()` and hands its integer return to the operating system
  as the process exit code — which is what a shell, Makefile, or CI job checks.

### Exit codes at a glance

| Code | Meaning |
|---|---|
| `0` | Session opened, instrument identified, closed clean. |
| `1` | VISA I/O failure (timeout, bad command, cable issue). |
| `2` | No resource configured (`SCOPE_RESOURCE` unset and no `--resource`) — resources listed. |
| `3` | pyvisa not installed, **or** no VISA backend available. |

---

## 4. Installing the tools for REAL hardware

To talk to the actual MSO44B you need **PyVISA + a VISA backend + the instrument address**.

### Step 1 — Python package
From the project folder (ideally in a virtual environment):
```bat
python -m venv .venv
.venv\Scripts\activate
pip install pyvisa
```
(Or, using the project's extras: `pip install -e ".[hardware]"`.)

### Step 2 — a VISA backend (the driver)
Pick **one** (NI-VISA is the most common and works with Tektronix scopes):
- **NI-VISA** — download "NI-VISA" from ni.com, run the installer, reboot.
- **TekVISA** — Tektronix's own VISA, download from tek.com.

This is the piece that was missing when the script said *"No VISA backend available."*

### Step 3 — connect the scope and find its address
- **USB:** plug a USB-B cable from the scope to the PC. The address looks like
  `USB0::0x0699::0x0522::<serial>::INSTR` (`0x0699` = Tektronix's vendor id).
- **LAN:** put the scope on the network; the address looks like
  `TCPIP0::192.168.0.10::INSTR` (use the scope's IP).

Don't know the exact string? Run the script with no address — it will **list what VISA sees**:
```bat
python scripts\bench_connect.py
```
Copy the printed resource string.

### Step 4 — set the address and run
```bat
set SCOPE_RESOURCE=TCPIP0::192.168.0.10::INSTR
python scripts\bench_connect.py
```
Expected output on success:
```
IDN: TEKTRONIX,MSO44,C012345,CF:91.1CT FV:2.0.3.950
ERR: 0,"No events to report - queue empty"
```
(and the process exits with code 0).

---

## 5. Installing for NO hardware (simulator)

You can exercise the script without any instrument in two escalating ways.

### Option A — `pyvisa-py` (a pure-Python backend, no NI-VISA needed)
This satisfies the "no VISA backend" error so `ResourceManager()` works. It won't invent an
instrument, but it lets the backend load and `list_resources()` run:
```bat
pip install pyvisa-py
python scripts\bench_connect.py        # now gets past ResourceManager(); lists resources
```
Good for proving your Python/pyvisa install is sane. You'll still get exit code 2 (no
resource) because there's no real instrument to address.

### Option B — `pyvisa-sim` (a *simulated instrument*, answers `*IDN?`)
This is the one that lets the script run its full flow — open, `*IDN?`, hygiene, `ALLEV?`,
close — against a **fake instrument defined in a YAML file**. This is exactly the "sim"
mentioned in the plan's offline-verify step.

1. Install both the simulator and the pure-Python backend it builds on:
   ```bat
   pip install pyvisa-sim pyvisa-py
   ```

2. Create a small YAML describing a fake scope. Save as `scripts\sim_mso44b.yaml`:
   ```yaml
   spec: "1.1"
   devices:
     mso44b:
       eom:
         TCPIP INSTR: { q: "\n", r: "\n" }
       dialogues:
         - q: "*IDN?"
           r: "TEKTRONIX,MSO44,SIM0001,FV:sim"
         - q: "ALLEV?"
           r: '0,"No events to report - queue empty"'
         # the hygiene writes are declared so the sim accepts them (no reply)
         - q: "HEADer OFF"
         - q: "VERBose OFF"
         - q: "*CLS"
   resources:
     TCPIP0::sim-scope::INSTR:
       device: mso44b
   ```
   *(This defines an instrument that replies to `*IDN?` and `ALLEV?`, and accepts the three
   hygiene writes.)*

   > **Note:** pyvisa-sim's YAML schema varies slightly between versions — see its docs
   > (`github.com/pyvisa/pyvisa-sim`). If a run reports an "unknown command" on one of the
   > hygiene writes, either declare it exactly as above or, for a quick check, comment those
   > three `inst.write(cmd)` lines out for the sim demo. The point of the sim is to prove the
   > open → `*IDN?` → close flow with no hardware; the hygiene writes are validated for real
   > on the bench.

3. Point the script at the simulator — **both the address and the backend come from
   outside the code** (no editing the script). The address is a normal `SCOPE_RESOURCE`;
   the backend is the sim (`<yaml>@sim`), passed via `--backend` or the `VISA_BACKEND` env var:
   ```bat
   set SCOPE_RESOURCE=TCPIP0::sim-scope::INSTR
   python scripts\bench_connect.py --backend scripts/sim_mso44b.yaml@sim
   ```
   or, entirely through the environment:
   ```bat
   set SCOPE_RESOURCE=TCPIP0::sim-scope::INSTR
   set VISA_BACKEND=scripts/sim_mso44b.yaml@sim
   python scripts\bench_connect.py
   ```
   Expected:
   ```
   IDN: TEKTRONIX,MSO44,SIM0001,FV:sim
   ERR: 0,"No events to report - queue empty"
   ```

> Note: nothing is hardcoded — the **address** comes from `SCOPE_RESOURCE`/`--resource` and
> the **backend** from `VISA_BACKEND`/`--backend`. On the real bench you just omit
> `--backend` (it falls back to the system NI-VISA/TekVISA).

### Which to install when

| Goal | Install | Result |
|---|---|---|
| Talk to the real MSO44B | `pyvisa` + **NI-VISA/TekVISA** | Full bench run. |
| Prove pyvisa works, no scope | `pyvisa` + `pyvisa-py` | Backend loads; lists resources. |
| Run the whole flow offline | `pyvisa` + `pyvisa-sim` + `pyvisa-py` + a YAML | `*IDN?`/`ALLEV?` answered by a fake scope. |

---

## 6. How to run — hardware and no-hardware

Two inputs decide how the script connects, and **both come from outside the code**:

| Input | Flag / env var | Meaning |
|---|---|---|
| Address | `--resource` / `SCOPE_RESOURCE` | *Which* instrument to open. |
| Backend | `--backend` / `VISA_BACKEND` | *Which* VISA implementation to use (omit = system NI-VISA/TekVISA). |

### A. Run ON hardware

**LAN / Ethernet — lightest, no vendor driver needed** (uses the pure-Python backend):
```bat
pip install pyvisa pyvisa-py
set SCOPE_RESOURCE=TCPIP0::192.168.0.10::INSTR
python scripts\bench_connect.py --backend @py
```

**USB — install NI-VISA (or TekVISA), then no --backend needed** (uses system VISA):
```bat
pip install pyvisa
:: install NI-VISA from ni.com first
set SCOPE_RESOURCE=USB0::0x0699::0x0522::C012345::INSTR
python scripts\bench_connect.py
```

**Don't know the address?** Run with none set — it lists what VISA can see, then copy one:
```bat
python scripts\bench_connect.py --backend @py     :: (or omit --backend if NI-VISA is installed)
```

Success prints an identity and an empty error queue, and exits 0:
```
IDN: TEKTRONIX,MSO44,C012345,CF:91.1CT FV:2.0.3.950
ERR: 0,"No events to report - queue empty"
```

### B. Run WITHOUT hardware (simulator)

Runs the full flow (open → `*IDN?` → hygiene → `ALLEV?` → close) against the fake scope in
`scripts\sim_mso44b.yaml` — no instrument, no vendor driver:
```bat
pip install pyvisa pyvisa-sim pyvisa-py
set SCOPE_RESOURCE=TCPIP0::sim-scope::INSTR
python scripts\bench_connect.py --backend scripts/sim_mso44b.yaml@sim
```
Expected:
```
IDN: TEKTRONIX,MSO44,SIM0001,FV:sim
ERR: 0,"No events to report - queue empty"
```
*(You can put the address in `SCOPE_RESOURCE` and the backend in `VISA_BACKEND` instead of
passing flags — same result.)*

### Common options + checking the result

```bat
python scripts\bench_connect.py --help             :: show all options
python scripts\bench_connect.py --timeout 10000    :: 10-second I/O cap
```
Check the exit code afterward: Windows `echo %ERRORLEVEL%`, Git-Bash `echo $?`
(`0` = success; see the exit-code table in section 3).

### At a glance

| Scenario | Install | Address (`SCOPE_RESOURCE`) | Backend (`--backend`) |
|---|---|---|---|
| Real scope over **LAN** | `pyvisa` + `pyvisa-py` | `TCPIP0::<ip>::INSTR` | `@py` |
| Real scope over **USB** | `pyvisa` + **NI-VISA** | `USB0::0x0699::...::INSTR` | *(omit)* |
| **No hardware** (sim) | `pyvisa` + `pyvisa-sim` + `pyvisa-py` | `TCPIP0::sim-scope::INSTR` | `scripts/sim_mso44b.yaml@sim` |
