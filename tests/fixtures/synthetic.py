"""Generate known signals (and their raw codes) with no hardware.

Two outputs are useful:
* :class:`Waveform` directly, for measurement/compare tests.
* ``(codes, preamble)``, for feeding ``FakeTransport.load_curve`` and exercising
  the full preamble-parse + scaling path.
"""

from __future__ import annotations

import numpy as np

from scopeblock.waveform import Waveform


def _time_axis(sample_rate: float, duration: float) -> np.ndarray:
    n = int(round(sample_rate * duration))
    return np.arange(n, dtype=float) / sample_rate


def make_sine(
    frequency: float = 1_000.0,
    amplitude: float = 5.0,   # Vpp
    offset: float = 0.0,
    sample_rate: float = 1_000_000.0,
    duration: float = 0.01,
    noise: float = 0.0,
    channel: str = "CH1",
) -> Waveform:
    t = _time_axis(sample_rate, duration)
    v = (amplitude / 2.0) * np.sin(2 * np.pi * frequency * t) + offset
    if noise:
        rng = np.random.default_rng(0)
        v = v + rng.normal(0.0, noise, size=v.shape)
    return Waveform.from_samples(v, dt=1.0 / sample_rate, channel=channel)


def make_square(
    frequency: float = 1_000.0,
    v_low: float = 0.0,
    v_high: float = 5.0,
    sample_rate: float = 1_000_000.0,
    duration: float = 0.01,
    channel: str = "CH1",
) -> Waveform:
    return make_pwm(
        frequency=frequency,
        v_low=v_low,
        v_high=v_high,
        duty=50.0,
        sample_rate=sample_rate,
        duration=duration,
        channel=channel,
    )


def make_pwm(
    frequency: float = 1_000.0,
    v_low: float = 0.0,
    v_high: float = 5.0,
    duty: float = 40.0,        # percent
    sample_rate: float = 1_000_000.0,
    duration: float = 0.01,
    rise: float = 0.0,         # seconds (0 = ideal step)
    noise: float = 0.0,
    channel: str = "CH1",
) -> Waveform:
    """A PWM/square pulse train — the bread-and-butter ECM output signal."""
    t = _time_axis(sample_rate, duration)
    phase = (t * frequency) % 1.0
    high = phase < (duty / 100.0)
    v = np.where(high, v_high, v_low).astype(float)
    if rise > 0:
        # simple first-order smoothing to give finite edges for rise/fall tests
        alpha = (1.0 / sample_rate) / rise
        alpha = min(alpha, 1.0)
        out = np.empty_like(v)
        out[0] = v[0]
        for i in range(1, v.size):
            out[i] = out[i - 1] + alpha * (v[i] - out[i - 1])
        v = out
    if noise:
        rng = np.random.default_rng(0)
        v = v + rng.normal(0.0, noise, size=v.shape)
    return Waveform.from_samples(v, dt=1.0 / sample_rate, channel=channel)


def signal_to_codes(
    wf: Waveform,
    ymult: float = 0.001,
    yoff: float = 0.0,
    yzero: float = 0.0,
    byt_nr: int = 2,
) -> tuple[np.ndarray, dict]:
    """Invert the affine transform: volts -> raw integer codes + a preamble dict.

    Lets a test push a known volts signal *through the scope's parse path* by
    loading the codes into FakeTransport and reading them back as a Waveform.
    """
    codes = np.round((wf.v - yzero) / ymult + yoff).astype(np.int64)
    preamble = {
        "BYT_NR": byt_nr,
        "BIT_NR": byt_nr * 8,
        "BN_FMT": "RI",
        "BYT_OR": "MSB",
        "ENCDG": "BINARY",
        "XINCR": wf.dt,
        "XZERO": wf.t0,
        "PT_OFF": 0,
        "YMULT": ymult,
        "YOFF": yoff,
        "YZERO": yzero,
        "YUNIT": "V",
        "XUNIT": "s",
    }
    return codes, preamble
