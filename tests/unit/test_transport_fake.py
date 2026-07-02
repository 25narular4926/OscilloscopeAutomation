"""FakeTransport behaviour — the foundation every offline test stands on."""

from __future__ import annotations

import numpy as np
import pytest

from scopeblock.transport.base import Transport, TransportError
from scopeblock.transport.fake_transport import FakeTransport


def test_implements_transport_protocol():
    assert isinstance(FakeTransport(), Transport)


def test_handshake_defaults():
    fake = FakeTransport()
    assert "MSO44B" in fake.query("*IDN?")
    assert fake.query("*OPC?") == "1"
    assert fake.query("*ESR?") == "0"


def test_write_is_recorded():
    fake = FakeTransport()
    fake.write("HEADer OFF")
    assert "HEADer OFF" in fake.history


def test_unknown_query_raises():
    fake = FakeTransport()
    with pytest.raises(TransportError):
        fake.query("NONSENSE?")


def test_canned_response():
    fake = FakeTransport()
    fake.set_response("TRIGger:STATE?", "TRIGGER")
    assert fake.query("TRIGger:STATE?") == "TRIGGER"


def test_curve_block_decodes_back():
    fake = FakeTransport()
    codes = np.array([-3, -1, 0, 1, 100, -100], dtype=np.int64)
    fake.load_curve(codes, {"BYT_NR": 2})
    raw = fake.query_binary("CURVe?")
    from scopeblock.scope.preamble import Preamble, decode_curve

    pre = Preamble.from_transport(fake)
    assert np.array_equal(decode_curve(raw, pre), codes)


def test_closed_transport_rejects_io():
    fake = FakeTransport()
    fake.close()
    with pytest.raises(TransportError):
        fake.write("X")
