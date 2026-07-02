"""Feature extraction — measurement, not sample-by-sample comparison.

Design rules (from CLAUDE.md):
* Edges via threshold crossing **with hysteresis** (Schmitt), so noise near a
  level can't double-count.
* Frequency from successive rising-edge periods; duty from high-time / period.
* Levels (V_high / V_low / Vpp) from a **histogram split**, never raw min/max —
  outliers and overshoot wreck min/max.

Everything operates on a :class:`~scopeblock.waveform.Waveform` and returns plain
floats (or a small dataclass), so it is trivially unit-testable offline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..waveform import Waveform


@dataclass
class Levels:
    """Robust vertical levels for a (typically two-state) signal."""

    v_low: float
    v_high: float

    @property
    def amplitude(self) -> float:
        return self.v_high - self.v_low

    @property
    def mid(self) -> float:
        return 0.5 * (self.v_low + self.v_high)


# -- levels --------------------------------------------------------------


def signal_levels(v: np.ndarray) -> Levels:
    """Estimate low/high levels robustly via a histogram split about the median.

    For a pulse/PWM signal this yields the two flat levels; for a sine it yields
    the lower/upper halves' medians (still useful, and outlier-immune). The split
    point is the global median, which separates the two clusters for any signal
    with roughly balanced time at each level. For unbalanced duty we fall back to
    percentiles so a thin high pulse is still found.
    """
    v = np.asarray(v, dtype=float)
    if v.size == 0:
        raise ValueError("empty waveform")
    mid = np.median(v)
    lower = v[v <= mid]
    upper = v[v >= mid]
    if lower.size and upper.size:
        v_low = float(np.median(lower))
        v_high = float(np.median(upper))
    else:  # degenerate (flat) signal
        v_low = v_high = float(mid)

    # Guard against unbalanced duty collapsing one cluster onto the median:
    # widen using robust percentiles if the split looks too narrow.
    p_low, p_high = np.percentile(v, [2.0, 98.0])
    if (v_high - v_low) < 0.5 * (p_high - p_low):
        v_low, v_high = float(p_low), float(p_high)
    return Levels(v_low=v_low, v_high=v_high)


# amplitude family 


def v_pp(wf: Waveform) -> float:
    """Peak-to-peak using robust percentiles (immune to single-sample spikes)."""
    lo, hi = np.percentile(wf.v, [0.5, 99.5])
    return float(hi - lo)


def v_amplitude(wf: Waveform) -> float:
    """High level minus low level (the 'amplitude' of a pulse)."""
    return signal_levels(wf.v).amplitude


def v_high(wf: Waveform) -> float:
    return signal_levels(wf.v).v_high


def v_low(wf: Waveform) -> float:
    return signal_levels(wf.v).v_low


def v_mean(wf: Waveform) -> float:
    return float(np.mean(wf.v))


def v_rms(wf: Waveform) -> float:
    return float(np.sqrt(np.mean(np.square(wf.v))))


# edges (Schmitt-triggered) 


def _schmitt_states(v: np.ndarray, low: float, high: float) -> np.ndarray:
    """Forward-filled hysteresis state: 1 above ``high``, 0 below ``low``.

    Samples in the dead band inherit the previous resolved state, so noise that
    only crosses one threshold never toggles the state.
    """
    state = np.full(v.shape, -1, dtype=np.int8)
    state[v >= high] = 1
    state[v <= low] = 0
    known = state >= 0
    if not known.any():
        return np.zeros(v.shape, dtype=np.int8)
    idx = np.where(known, np.arange(v.size), 0)
    np.maximum.accumulate(idx, out=idx)
    filled = state[idx]
    # leading dead-band samples take the first resolved state
    first = state[known][0]
    lead = idx == 0
    lead[np.argmax(known):] = False  # only the true leading run
    filled[lead] = first
    return filled


def _interp_cross(v: np.ndarray, t: np.ndarray, i: int, level: float) -> float:
    """Linear-interpolate the time at which the segment ending at ``i`` hits ``level``."""
    v0, v1 = v[i - 1], v[i]
    if v1 == v0:
        return float(t[i])
    frac = (level - v0) / (v1 - v0)
    return float(t[i - 1] + frac * (t[i] - t[i - 1]))


def edge_times(
    wf: Waveform,
    levels: Levels | None = None,
    hysteresis: float = 0.1,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(rising, falling)`` mid-level crossing times in seconds.

    ``hysteresis`` is a fraction of amplitude; the Schmitt band is
    ``mid ± hysteresis*amplitude``. Crossing times are interpolated at the mid
    level for sub-sample resolution.
    """
    levels = levels or signal_levels(wf.v)
    amp = levels.amplitude
    if amp <= 0:
        return np.array([]), np.array([])
    mid = levels.mid
    low = mid - hysteresis * amp
    high = mid + hysteresis * amp
    state = _schmitt_states(wf.v, low, high)
    transitions = np.diff(state.astype(np.int8))
    rising_idx = np.where(transitions == 1)[0] + 1
    falling_idx = np.where(transitions == -1)[0] + 1
    rising = np.array([_interp_cross(wf.v, wf.t, i, mid) for i in rising_idx])
    falling = np.array([_interp_cross(wf.v, wf.t, i, mid) for i in falling_idx])
    return rising, falling


# timing family 


def period(wf: Waveform) -> float:
    """Mean period from successive rising-edge crossings."""
    rising, _ = edge_times(wf)
    if rising.size < 2:
        raise ValueError("need at least two rising edges to measure period")
    return float(np.mean(np.diff(rising)))


def frequency(wf: Waveform) -> float:
    """1 / period."""
    return 1.0 / period(wf)


def duty_cycle(wf: Waveform) -> float:
    """High-time / period, in **percent**, averaged over complete cycles."""
    rising, falling = edge_times(wf)
    if rising.size < 2:
        raise ValueError("need at least two rising edges to measure duty")
    duties = []
    for k in range(rising.size - 1):
        r0, r1 = rising[k], rising[k + 1]
        # the falling edge that belongs to this cycle
        fall = falling[(falling > r0) & (falling < r1)]
        if fall.size:
            high_time = fall[0] - r0
            duties.append(high_time / (r1 - r0))
    if not duties:
        raise ValueError("no complete high/low cycle found for duty measurement")
    return float(100.0 * np.mean(duties))


def _edge_transition_time(
    wf: Waveform, rising: bool, lo_pct: float = 0.1, hi_pct: float = 0.9
) -> float:
    """Rise or fall time across the first qualifying edge (10%..90% by default)."""
    levels = signal_levels(wf.v)
    amp = levels.amplitude
    if amp <= 0:
        raise ValueError("flat signal has no edge")
    lo_level = levels.v_low + lo_pct * amp
    hi_level = levels.v_low + hi_pct * amp
    r, f = edge_times(wf, levels)
    anchor = r if rising else f
    if anchor.size == 0:
        raise ValueError("no edge of the requested polarity found")
    t_edge = anchor[0]
    # search a small window around the mid crossing for the lo/hi crossings
    v, t = wf.v, wf.t
    # index nearest to t_edge
    i_mid = int(np.searchsorted(t, t_edge))
    i_mid = max(1, min(i_mid, v.size - 1))
    lo_t = _find_level_near(v, t, i_mid, lo_level, rising)
    hi_t = _find_level_near(v, t, i_mid, hi_level, rising)
    return abs(hi_t - lo_t)


def _find_level_near(v, t, i_mid, level, rising) -> float:
    """Walk outward from ``i_mid`` to the first segment crossing ``level``."""
    step = 1
    n = v.size
    # scan both directions; closest crossing wins
    for radius in range(1, n):
        for i in (i_mid - radius, i_mid + radius, i_mid):
            if 1 <= i < n:
                v0, v1 = v[i - 1], v[i]
                if (v0 - level) * (v1 - level) <= 0 and v0 != v1:
                    return _interp_cross(v, t, i, level)
        step += 1
    return float(t[i_mid])


def rise_time(wf: Waveform) -> float:
    return _edge_transition_time(wf, rising=True)


def fall_time(wf: Waveform) -> float:
    return _edge_transition_time(wf, rising=False)


def edge_count(wf: Waveform) -> float:
    r, f = edge_times(wf)
    return float(r.size + f.size)


# dispatch 

_MEASURES = {
    "vpp": v_pp,
    "vamplitude": v_amplitude,
    "vamp": v_amplitude,
    "vhigh": v_high,
    "vlow": v_low,
    "vtop": v_high,
    "vbase": v_low,
    "mean": v_mean,
    "rms": v_rms,
    "frequency": frequency,
    "freq": frequency,
    "period": period,
    "duty": duty_cycle,
    "duty_cycle": duty_cycle,
    "rise_time": rise_time,
    "risetime": rise_time,
    "fall_time": fall_time,
    "falltime": fall_time,
    "edges": edge_count,
}


def measure(wf: Waveform, kind: str, **params) -> float:
    """Measure a single named feature. ``kind`` is case-insensitive.

    Supported: vpp, vamplitude, vhigh, vlow, mean, rms, frequency, period, duty,
    rise_time, fall_time, edges.
    """
    key = kind.lower().replace(" ", "_")
    if key not in _MEASURES:
        raise KeyError(f"unknown measurement {kind!r}; known: {sorted(set(_MEASURES))}")
    return _MEASURES[key](wf)


def measure_all(wf: Waveform) -> dict[str, float]:
    """Compute the common feature set, skipping any that don't apply.

    Timing features are omitted (not errored) for signals without enough edges,
    so this is safe to call on a DC level or a single transient.
    """
    out: dict[str, float] = {}
    for name in ("vpp", "vamplitude", "vhigh", "vlow", "mean", "rms"):
        out[name] = _MEASURES[name](wf)
    for name in ("frequency", "period", "duty"):
        try:
            out[name] = _MEASURES[name](wf)
        except ValueError:
            pass
    return out
