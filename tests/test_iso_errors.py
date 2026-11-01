"""Typed connection and service errors raised against a scripted peer.

Each case drives the client down one failure a conforming server rarely or
never produces: an association refused at the ACSE or MMS level, a request
rejected, the association released or the connection dropped by the peer,
requests timing out, and continuation pages that do not move on. The peer in `_iso_peer` builds every
frame from the ISO octet layouts, so it does not depend on the package under
test.
"""

from __future__ import annotations

import socket

import pytest

import _demo
import iec61850
from _iso_peer import (
    AARE_ACCEPTED,
    AARE_REJECTED_PERMANENT,
    CLOSE,
    MMS_CONCLUDE_REQUEST,
    NO_ANSWER,
    RESET,
    aare,
    associated_peer,
    confirmed_response,
    initiate_error,
    invoke_id_of,
    reject,
    tlv,
)

READ_REF = "LD1/LLN0.Mod.stVal"
LOG_REF = "LD1/LLN0$Log"

# rejectReason CHOICE of a RejectPDU: confirmed-requestPDU [1], whose value 1
# is unrecognized-service.
REJECT_CONFIRMED_REQUEST = 1
REJECT_UNRECOGNIZED_SERVICE = 1

# The initiate class value an Initiate-ErrorPDU carries.
INITIATE_ERROR_CODE = 3


async def test_aare_rejection_raises_associate_failed() -> None:
    async with associated_peer(acse=aare(AARE_REJECTED_PERMANENT, None)) as addr:
        with pytest.raises(iec61850.IedConnectionError) as info:
            await iec61850.IedConnection.connect(addr, timeout_ms=2000)
    assert info.value.reason == "associate-failed"


async def test_initiate_error_raises_initiate_failed_with_its_code() -> None:
    acse = aare(AARE_ACCEPTED, initiate_error(INITIATE_ERROR_CODE))
    async with associated_peer(acse=acse) as addr:
        with pytest.raises(iec61850.IedConnectionError) as info:
            await iec61850.IedConnection.connect(addr, timeout_ms=2000)
    assert info.value.reason == "initiate-failed"
    assert info.value.code == INITIATE_ERROR_CODE


async def test_rejected_request_raises_reject_with_type_and_code() -> None:
    def answer(request: bytes) -> bytes:
        return reject(
            invoke_id_of(request), REJECT_CONFIRMED_REQUEST, REJECT_UNRECOGNIZED_SERVICE
        )

    async with associated_peer(answer) as addr:
        conn = await iec61850.IedConnection.connect(addr, timeout_ms=2000)
        try:
            with pytest.raises(iec61850.IedServiceError) as info:
                await conn.read_bool(READ_REF, iec61850.FC.ST)
            assert conn.is_connected is True
        finally:
            await conn.abort()
    assert info.value.reason == "reject"
    assert info.value.reject_type == REJECT_CONFIRMED_REQUEST
    assert info.value.code == REJECT_UNRECOGNIZED_SERVICE


async def test_conclude_from_the_peer_raises_released_by_peer() -> None:
    async with associated_peer(lambda _request: MMS_CONCLUDE_REQUEST) as addr:
        conn = await iec61850.IedConnection.connect(addr, timeout_ms=2000)
        try:
            with pytest.raises(iec61850.IedConnectionError) as info:
                await conn.read_bool(READ_REF, iec61850.FC.ST)
            assert conn.is_connected is False
        finally:
            await conn.abort()
    assert info.value.reason == "released-by-peer"


async def test_reset_connection_raises_connection_lost() -> None:
    """A TCP RST is a close of the peer, like an orderly one."""
    async with associated_peer(lambda _request: RESET) as addr:
        conn = await iec61850.IedConnection.connect(addr, timeout_ms=2000)
        try:
            with pytest.raises(iec61850.IedConnectionError) as info:
                await conn.read_bool(READ_REF, iec61850.FC.ST)
            assert conn.is_connected is False
        finally:
            await conn.abort()
    assert info.value.reason == "connection-lost"


async def test_refused_connection_raises_transport_with_its_kind() -> None:
    """A transport failure that is not a close carries its category."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
    s.close()

    with pytest.raises(iec61850.IedConnectionError) as info:
        await iec61850.IedConnection.connect(
            f"127.0.0.1:{closed_port}", timeout_ms=10_000
        )
    assert info.value.reason == "transport"
    assert info.value.kind == "connection refused"
    assert isinstance(info.value.detail, str) and info.value.detail


async def test_orderly_close_raises_connection_lost() -> None:
    async with associated_peer(lambda _request: CLOSE) as addr:
        conn = await iec61850.IedConnection.connect(addr, timeout_ms=2000)
        try:
            with pytest.raises(iec61850.IedConnectionError) as info:
                await conn.read_bool(READ_REF, iec61850.FC.ST)
        finally:
            await conn.abort()
    assert info.value.reason == "connection-lost"


async def test_timed_out_requests_hold_every_slot_raises_outstanding_limit() -> None:
    async with associated_peer(lambda _request: NO_ANSWER) as addr:
        conn = await iec61850.IedConnection.connect(
            addr, timeout_ms=2000, request_timeout_ms=300, max_outstanding=1
        )
        try:
            with pytest.raises(iec61850.IedTimeoutError):
                await conn.read_bool(READ_REF, iec61850.FC.ST)
            with pytest.raises(iec61850.IedServiceError) as info:
                await conn.read_bool(READ_REF, iec61850.FC.ST)
        finally:
            await conn.abort()
    assert info.value.reason == "outstanding-limit"


async def test_name_list_page_repeating_its_names_raises_not_advancing() -> None:
    # getNameList [1]: listOfIdentifier [0] holding "LD1", moreFollows [1] TRUE
    page = tlv(0xA1, tlv(0xA0, tlv(0x1A, b"LD1")) + tlv(0x81, b"\xff"))

    def answer(request: bytes) -> bytes:
        return confirmed_response(invoke_id_of(request), page)

    async with associated_peer(answer) as addr:
        conn = await iec61850.IedConnection.connect(addr, timeout_ms=2000)
        try:
            with pytest.raises(iec61850.IedServiceError) as info:
                await conn.get_server_directory()
        finally:
            await conn.abort()
    assert info.value.reason == "name-list-not-advancing"


async def test_empty_journal_page_with_more_follows_raises_not_advancing() -> None:
    # readJournal [65]: an empty listOfJournalEntry [0], moreFollows [1] TRUE
    page = tlv(bytes([0xBF, 0x41]), tlv(0xA0, b"") + tlv(0x81, b"\xff"))

    def answer(request: bytes) -> bytes:
        return confirmed_response(invoke_id_of(request), page)

    async with associated_peer(answer) as addr:
        conn = await iec61850.IedConnection.connect(addr, timeout_ms=2000)
        try:
            with pytest.raises(iec61850.IedServiceError) as info:
                await conn.query_journal_all(
                    LOG_REF,
                    iec61850.JournalQuery(start_time_ms=_demo.JOURNAL_BASE_MS),
                )
        finally:
            await conn.abort()
    assert info.value.reason == "journal-not-advancing"


async def test_request_past_the_negotiated_pdu_size_raises_pdu_too_large(
    writable_server: str,
) -> None:
    conn = await iec61850.IedConnection.connect(
        writable_server, local_max_pdu_size=1000
    )
    try:
        with pytest.raises(iec61850.IedServiceError) as info:
            await conn.write_visible_string(
                _demo.WRITABLE_LABEL, iec61850.FC.SP, "x" * 2000
            )
        assert conn.is_connected is True
    finally:
        await conn.disconnect()
    assert info.value.reason == "pdu-too-large"
    assert info.value.max_size == 1000
    assert info.value.pdu_size > 1000
