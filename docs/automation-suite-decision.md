# Test automation suite: pytest vs Robot Framework

**Decision: pytest is the primary engine. Robot Framework is an optional thin
acceptance layer on top — not the foundation.**

This matches CLAUDE.md ("optionally Robot Framework suites wrapping the pytest
fixtures") and the deck's Week 3 plan ("pytest / Robot + reporting"). Below is
the reasoning, so the choice is traceable and reversible.

## What we're actually testing

The scope block is a **Python library** consumed by a larger Python test API. The
bulk of the test surface is:

- preamble parsing + affine scaling round-trips (numeric, array-level asserts)
- every measurement against synthetic signals with *known* freq/duty/levels
- the tolerance comparison engine (pass **and** fail paths)
- config validation, export/reload round-trips
- one hardware loopback (AFG → MSO → pipeline) as the headline integration test

That is overwhelmingly **fine-grained, data-driven, numeric unit testing** —
pytest's home turf.

## Head to head

| Dimension | pytest | Robot Framework |
|---|---|---|
| Language fit | Native Python; tests *are* the code under test's language | Keyword DSL; Python lives behind a library wrapper |
| Numeric / array asserts | `pytest.approx`, numpy asserts, rich introspection on failure | Awkward; needs custom keywords for tolerance math |
| Parametrization | `@pytest.mark.parametrize` (e.g. duty 20/40/75%) is first-class | `Templates` exist but clumsier for numeric sweeps |
| Fixtures / setup | Powerful, composable fixtures (fake transport, tmp files) | Setup/teardown keywords, less composable |
| Offline-first speed | Sub-second full suite; trivial to gate hardware via markers | Heavier startup; markers via tags |
| Reporting for ASPICE | JUnit XML out of the box (`--junitxml`) → CI/requirement rollup | Rich HTML/XML (`log.html`, `report.html`, `output.xml`) — very readable for non-devs |
| Audience | Developers writing/maintaining the block | Test engineers / reviewers who read suites without reading Python |
| HIL ergonomics | Great via fixtures; bench access behind `@pytest.mark.hardware` | Strong for human-readable acceptance suites and manual-ish bench runs |

## Why pytest wins as the base

1. **The pipeline is numeric.** Measurement and tolerance logic want
   `pytest.approx` and numpy asserts. In Robot you'd reimplement that as custom
   keywords — more code, the very thing under test living behind a DSL.
2. **Offline-first is the core principle.** A sub-second pytest run with a
   `FakeTransport` is the inner dev loop. `@pytest.mark.hardware` + `conftest.py`
   already skip bench tests unless `SCOPE_RESOURCE` is set — CI never touches a
   bench.
3. **Traceability is still covered.** `pytest --junitxml=report.xml` emits the
   requirement → test → verdict evidence ASPICE needs. Each `Result` carries its
   `requirement_id`, so a parametrized config-driven test maps one TOML case to
   one JUnit testcase.
4. **It's the integration language.** The larger API is Python; sharing pytest
   fixtures across blocks is free.

## Where Robot Framework still earns its place (optional `[robot]` extra)

- **Human-readable acceptance suites.** A reviewer or test lead can read
  `Verify PWM Pin Outputs 40% Duty At 1 kHz` without reading Python.
- **Bench bring-up / manual-adjacent runs.** Keyword suites are pleasant to drive
  semi-interactively on the bench.
- **Polished HTML reports** for stakeholders who don't live in CI.

The Robot layer **wraps the same `scopeblock` API and the same pytest fixtures**;
it adds zero logic of its own. See [`robot/`](../robot/). If the team never wants
Robot, nothing of value is lost — it's a leaf, not a root.

## Bottom line

Build and gate everything on **pytest**. Keep **Robot Framework** as an optional,
thin, human-facing acceptance veneer installed via `pip install -e ".[robot]"`.
