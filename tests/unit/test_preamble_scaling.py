"""Preamble parsing + scaling round-trips through the fake transport."""

from __future__ import annotations

import numpy as np
import pytest

from scopeblock.transport.fake_transport import FakeTransport
from scopeblock.scope.preamble import Preamble, decode_curve, to_waveform, parse_ieee_block
from scopeblock.scope.mso44b import MSO44B
from tests.fixtures import make_pwm, make_sine, signal_to_codes


def test_ieee_block_roundtrip():
    from scopeblock.transport.fake_transport import encode_ieee_block

    payload = b"\x01\x02\x03\x04"
    block = encode_ieee_block(payload)
    assert block.startswith(b"#")
    assert parse_ieee_block(block) == payload


def test_scaling_recovers_volts_and_time():
    wf_true = make_sine(frequency=1000, amplitude=4.0, offset=1.0)
    codes, pre = signal_to_codes(wf_true, ymult=0.0005, yoff=100.0, yzero=0.2)

    fake = FakeTransport()
    fake.load_curve(codes, pre)

    preamble = Preamble.from_transport(fake, source="CH1")
    raw = fake.query_binary("CURVe?")
    recovered = to_waveform(decode_curve(raw, preamble), preamble)

    # volts recovered within one code's worth of quantization
    assert np.allclose(recovered.v, wf_true.v, atol=0.0005 * 1.5)
    assert recovered.dt == pytest.approx(wf_true.dt)
    assert recovered.n == wf_true.n


def test_mso44b_read_waveform_full_path():
    wf_true = make_pwm(frequency=2000, v_low=0.0, v_high=5.0, duty=40.0)
    codes, pre = signal_to_codes(wf_true, ymult=0.001)
    fake = FakeTransport()
    fake.load_curve(codes, pre)

    scope = MSO44B(fake)
    scope.session_setup()
    wf = scope.read_waveform("CH1")

    assert wf.channel == "CH1"
    assert wf.units == "V"
    assert np.isclose(wf.v.max(), 5.0, atol=0.05)
    assert np.isclose(wf.v.min(), 0.0, atol=0.05)


def test_dtype_signed_msb():
    pre = Preamble(
        xincr=1e-6, xzero=0.0, pt_off=0, ymult=1.0, yoff=0.0, yzero=0.0,
        nr_pt=4, byt_nr=2, bn_fmt="RI", byt_or="MSB", encdg="BINARY",
    )
    assert pre.dtype == np.dtype(">i2")
