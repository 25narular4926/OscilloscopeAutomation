"""Export + reload round-trips, and the file-load == capture invariant."""

from __future__ import annotations

import numpy as np
import pytest

from scopeblock.io.export import export, load_waveform
from tests.fixtures import make_pwm


@pytest.mark.parametrize("fmt", ["npz", "csv"])
def test_export_load_roundtrip(tmp_path, fmt):
    wf = make_pwm(frequency=1000.0, v_low=0.0, v_high=5.0, duty=40.0)
    path = tmp_path / f"cap.{fmt}"
    export(wf, path)
    back = load_waveform(path)
    assert back.n == wf.n
    assert back.channel == wf.channel
    assert np.allclose(back.v, wf.v, atol=1e-9 if fmt == "npz" else 1e-6)
    assert np.allclose(back.t, wf.t, atol=1e-9 if fmt == "npz" else 1e-6)


def test_npz_preserves_meta(tmp_path):
    wf = make_pwm()
    wf.meta = {"ymult": 0.001, "source": "CH1"}
    path = tmp_path / "cap.npz"
    export(wf, path)
    back = load_waveform(path)
    assert back.meta["ymult"] == 0.001


def test_unsupported_format(tmp_path):
    wf = make_pwm()
    with pytest.raises(ValueError):
        export(wf, tmp_path / "cap.xyz")
