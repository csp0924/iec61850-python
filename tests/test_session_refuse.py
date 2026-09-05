"""A session REFUSE must surface as a typed exception with its parameters.

An IED that does not recognise the addressing it is offered answers the session
CONNECT with an RF SPDU rather than dropping the socket. Distinguishing that
from a malformed peer is what tells an operator to fix the selectors, so the
reason code, the transport-disconnect parameter and the presentation
provider-reason are all carried on the exception.

The refusal SPDUs are written out byte by byte from the ISO 8327-1 parameter
layout, so the test does not depend on the encoder it is checking.
"""

from __future__ import annotations

import pytest

import iec61850

from _iso_peer import session_peer

# RF, LI 10: PI 50 with reason code 129, then PGI 193 carrying a five-byte CPR
# PPDU whose provider-reason, tag 0x8A, is 3.
REFUSE_WITH_PROVIDER_REASON = bytes(
    [0x0C, 0x0A, 0x32, 0x01, 0x81, 0xC1, 0x05, 0x30, 0x03, 0x8A, 0x01, 0x03]
)

# RF, LI 6: PI 17 transport disconnect 1, PI 50 with reason code 2, no user
# data, so there is no presentation provider-reason to report.
REFUSE_WITHOUT_USER_DATA = bytes([0x0C, 0x06, 0x11, 0x01, 0x01, 0x32, 0x01, 0x02])

# An SPDU identifier the session layer does not define.
UNKNOWN_SPDU = bytes([0x0B, 0x00])

CONNECT_TIMEOUT_MS = 2000


async def connect_error(answer: bytes) -> BaseException:
    """Return the exception a `connect` against a peer answering `answer` raises."""
    async with session_peer(answer) as peer:
        with pytest.raises(iec61850.IedError) as caught:
            await iec61850.IedConnection.connect(
                peer.addr, timeout_ms=CONNECT_TIMEOUT_MS
            )
    return caught.value


def test_the_refusal_error_is_a_connection_error() -> None:
    """Callers that already catch connection failures must keep catching this."""
    assert issubclass(iec61850.IedSessionRefusedError, iec61850.IedConnectionError)
    assert issubclass(iec61850.IedSessionRefusedError, iec61850.IedError)


async def test_refusal_reports_reason_and_provider_reason() -> None:
    err = await connect_error(REFUSE_WITH_PROVIDER_REASON)

    assert isinstance(err, iec61850.IedSessionRefusedError)
    assert err.reason_code == 129
    assert err.transport_disconnect is None
    assert err.provider_reason == 3
    assert "session selector unknown" in str(err)
    assert "called-presentation-address-unknown" in str(err)


async def test_refusal_without_user_data_has_no_provider_reason() -> None:
    err = await connect_error(REFUSE_WITHOUT_USER_DATA)

    assert isinstance(err, iec61850.IedSessionRefusedError)
    assert err.reason_code == 2
    assert err.transport_disconnect == 1
    assert err.provider_reason is None
    assert "rejection by called SS-user" in str(err)


async def test_an_unknown_spdu_is_not_reported_as_a_refusal() -> None:
    """The refusal branch must not swallow every unrecognised identifier."""
    err = await connect_error(UNKNOWN_SPDU)

    assert not isinstance(err, iec61850.IedSessionRefusedError)
    assert isinstance(err, iec61850.IedConnectionError)
    assert "unknown spdu id 0x0B" in str(err)
