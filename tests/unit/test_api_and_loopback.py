"""The public API surface + the simulated AFG loopback (the headline test).

The loopback normally runs on the bench; here we run the *exact same code path*
against a FakeTransport pre-loaded with a synthetic capture, proving the
parse -> measure -> compare pipeline end to end with no hardware.
"""

from __future__ import annotations

import numpy as np

from scopeblock import api
from scopeblock.transport.fake_transport import FakeTransport
from scopeblock.afg.afg31102 import AFGSetup
from scopeblock.config.schema import ScopeSetup
from tests.fixtures import make_pwm, signal_to_codes


def _scope_fake_with(wf):
    codes, pre = signal_to_codes(wf, ymult=0.001)
    fake = FakeTransport()
    fake.load_curve(codes, pre)
    return fake


def test_configure_pushes_expected_commands():
    fake = FakeTransport()
    state = api.configure(fake, ScopeSetup(channel="CH1", vertical_scale=1.0, record_length=1000))
    assert state.channel == "CH1"
    assert any("HEADer OFF" in c for c in fake.history)
    assert any("CH1:SCAle 1.0" in c for c in fake.history)
    assert any("DATa:SOURce CH1" in c for c in fake.history)


def test_acquire_returns_waveform():
    wf_true = make_pwm(frequency=1000.0, v_low=0.0, v_high=5.0, duty=40.0)
    fake = _scope_fake_with(wf_true)
    wf = api.acquire(fake, timeout=2.0, source="CH1")
    assert wf.n == wf_true.n
    assert np.isclose(api.measure(wf, "frequency"), 1000.0, rtol=0.01)


def test_simulated_afg_loopback_passes():
    stimulus = AFGSetup(shape="PULSE", frequency=1000.0, amplitude=5.0, duty=40.0)
    # The "captured" signal the scope would see for that stimulus:
    captured = make_pwm(frequency=1000.0, v_low=0.0, v_high=5.0, duty=40.0)

    scope_fake = _scope_fake_with(captured)
    afg_fake = FakeTransport(idn="TEKTRONIX,AFG31102,FAKE,FV:1.0")

    results = api.self_test(
        scope_transport=scope_fake,
        afg_transport=afg_fake,
        setup=ScopeSetup(channel="CH1", record_length=captured.n),
        stimulus=stimulus,
    )
    assert results, "loopback produced no results"
    assert all(r.passed for r in results), "\n".join(str(r) for r in results)

    # the AFG was actually driven and then stopped
    assert any("OUTPut1:STATE ON" in c for c in afg_fake.history)
    assert any("OUTPut1:STATE OFF" in c for c in afg_fake.history)


def test_simulated_loopback_detects_wrong_signal():
    # AFG commands 1 kHz but the scope "captures" 2 kHz -> frequency check must fail
    stimulus = AFGSetup(shape="PULSE", frequency=1000.0, amplitude=5.0, duty=40.0)
    captured = make_pwm(frequency=2000.0, v_low=0.0, v_high=5.0, duty=40.0)

    results = api.self_test(
        scope_transport=_scope_fake_with(captured),
        afg_transport=FakeTransport(),
        setup=ScopeSetup(channel="CH1", record_length=captured.n),
        stimulus=stimulus,
    )
    freq_result = next(r for r in results if r.name == "frequency")
    assert not freq_result.passed


def test_run_test_case_tags_requirement():
    from scopeblock.config import load_test_case_str

    toml = """
    requirement_id = "REQ-ECM-PWM-001"
    [[expects]]
    kind = "frequency"
    expected = 1000.0
    rel = 0.02
    """
    case = load_test_case_str(toml)
    wf = make_pwm(frequency=1000.0)
    results = api.run_test_case(wf, case)
    assert results[0].requirement_id == "REQ-ECM-PWM-001"
    assert results[0].passed
