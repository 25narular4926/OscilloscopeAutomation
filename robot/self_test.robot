*** Settings ***
Documentation     Human-readable acceptance suite for the scope block.
...               Runs offline against a simulated bench by default; point it at
...               real hardware by swapping `Open Simulated Bench` for `Open Bench`
...               (requires SCOPE_RESOURCE). This suite wraps the same scopeblock
...               API the pytest suite uses -- it adds no logic of its own.
Library           ScopeBlockLibrary.py


*** Test Cases ***
Verify PWM Pin Outputs 40 Percent Duty At 1 kHz    [Documentation]    REQ-ECM-PWM-001
    Open Simulated Bench    frequency=1000    v_low=0    v_high=5    duty=40
    Configure Scope         channel=CH1       record_length=10000
    Acquire Waveform
    Measurement Should Match    frequency      1000    tolerance_pct=1
    Measurement Should Match    duty           40      tolerance_pct=5
    Measurement Should Match    vamplitude     5       tolerance_pct=5

Verify Square Wave Frequency    [Documentation]    REQ-ECM-DO-002
    Open Simulated Bench    frequency=2500    duty=50
    Configure Scope
    Acquire Waveform
    Measurement Should Match    frequency      2500    tolerance_pct=1
