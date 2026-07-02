"""Every measurement against signals with known freq / duty / levels."""

from __future__ import annotations

import numpy as np
import pytest

from scopeblock.analysis import measure, measure_all, signal_levels
from tests.fixtures import make_pwm, make_sine, make_square


def test_levels_pwm():
    wf = make_pwm(v_low=0.0, v_high=5.0, duty=40.0)
    lv = signal_levels(wf.v)
    assert lv.v_low == pytest.approx(0.0, abs=0.05)
    assert lv.v_high == pytest.approx(5.0, abs=0.05)
    assert lv.amplitude == pytest.approx(5.0, abs=0.1)


def test_vpp_sine():
    wf = make_sine(amplitude=6.0)
    assert measure(wf, "vpp") == pytest.approx(6.0, rel=0.03)


def test_frequency_square():
    wf = make_square(frequency=1000.0)
    assert measure(wf, "frequency") == pytest.approx(1000.0, rel=0.01)


def test_frequency_sine():
    wf = make_sine(frequency=2500.0)
    assert measure(wf, "frequency") == pytest.approx(2500.0, rel=0.01)


@pytest.mark.parametrize("duty", [20.0, 40.0, 75.0])
def test_duty_cycle(duty):
    wf = make_pwm(frequency=1000.0, duty=duty)
    assert measure(wf, "duty") == pytest.approx(duty, abs=0.5)


def test_period_matches_inverse_frequency():
    wf = make_square(frequency=500.0)
    assert measure(wf, "period") == pytest.approx(1.0 / measure(wf, "frequency"), rel=1e-6)


def test_amplitude_with_offset():
    wf = make_pwm(v_low=-2.0, v_high=3.0, duty=50.0)
    assert measure(wf, "vamplitude") == pytest.approx(5.0, abs=0.1)


def test_rise_time_finite_edge():
    wf = make_pwm(frequency=1000.0, duty=50.0, rise=2e-6, sample_rate=5_000_000.0)
    rt = measure(wf, "rise_time")
    assert rt > 0
    # first-order edge: 10-90% time ~= 2.2 * tau
    assert rt == pytest.approx(2.2 * 2e-6, rel=0.5)


def test_measure_all_on_dc_skips_timing():
    # a flat DC level: no edges, timing keys must be omitted, not error
    wf = make_pwm(v_low=2.0, v_high=2.0, duty=50.0)
    out = measure_all(wf)
    assert "frequency" not in out
    assert out["mean"] == pytest.approx(2.0, abs=1e-6)


def test_unknown_measurement_raises():
    wf = make_sine()
    with pytest.raises(KeyError):
        measure(wf, "bogus")


def test_noise_does_not_break_frequency():
    wf = make_pwm(frequency=1000.0, duty=50.0, noise=0.2)
    assert measure(wf, "frequency") == pytest.approx(1000.0, rel=0.02)
