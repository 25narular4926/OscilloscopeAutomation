"""The headline hardware integration test: the AFG loopback.

Command a known signal on the AFG31102, capture it on the MSO44B, run
parse -> measure -> compare, and assert the measured freq/duty/amplitude match
the commanded values within tolerance. Validates the whole block end-to-end
with no ECM.

Skipped automatically unless SCOPE_RESOURCE and AFG_RESOURCE are set (see
``tests/conftest.py``). Never let CI depend on a bench.
"""

from __future__ import annotations

import os

import pytest

from scopeblock import api
from scopeblock.afg.afg31102 import AFGSetup
from scopeblock.config.schema import ScopeSetup

pytestmark = pytest.mark.hardware


@pytest.fixture
def scope_transport():
    from scopeblock.transport.visa_transport import VisaTransport

    t = VisaTransport(os.environ["SCOPE_RESOURCE"], timeout_ms=15_000)
    yield t
    t.close()


@pytest.fixture
def afg_transport():
    from scopeblock.transport.visa_transport import VisaTransport

    resource = os.environ.get("AFG_RESOURCE")
    if not resource:
        pytest.skip("AFG_RESOURCE not set; loopback needs the function generator")
    t = VisaTransport(resource, timeout_ms=10_000)
    yield t
    t.close()


def test_afg_loopback(scope_transport, afg_transport):
    stimulus = AFGSetup(shape="PULSE", frequency=1000.0, amplitude=5.0, offset=2.5, duty=40.0)
    setup = ScopeSetup(
        channel="CH1",
        vertical_scale=1.0,
        vertical_offset=-2.5,
        sample_rate=2.5e6,
        record_length=100_000,
        trigger_source="CH1",
        trigger_level=2.5,
    )
    results = api.self_test(scope_transport, afg_transport, setup, stimulus, timeout=15.0)
    failures = [str(r) for r in results if not r.passed]
    assert not failures, "loopback failed:\n" + "\n".join(failures)
