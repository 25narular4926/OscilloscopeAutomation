"""Synthetic-waveform generators for offline tests."""

from __future__ import annotations

from .synthetic import (
    make_pwm,
    make_sine,
    make_square,
    signal_to_codes,
)

__all__ = ["make_pwm", "make_sine", "make_square", "signal_to_codes"]
